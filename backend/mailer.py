"""SMTP transport and the durable email delivery loop.

Delivery is at-least-once. A job is claimed with a lease, sent, then marked.
If the process dies between the SMTP server accepting a message and the row
being updated, the lease expires and the job is sent again. Every message
carries a stable Message-ID derived from the job, so a duplicate can be
recognised. SMTP cannot promise exactly-once delivery after an ambiguous
network failure, and ACCEPTED only means the server took the message, not
that it reached an inbox.
"""

from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate
import hashlib
import os
import smtplib
import socket
import ssl
import uuid

from comms_core import EMAIL_RE, iso, utc_now
from alert_engine import immediate
import secret_box

LEASE_SECONDS = 120
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 6 * 3600


class SmtpConfigError(Exception):
    """The SMTP settings are incomplete or not allowed."""


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def load_smtp(connection) -> dict:
    row = connection.execute("SELECT * FROM smtp_settings WHERE id=1").fetchone()
    return dict(row) if row else {}


def public_smtp(cfg: dict) -> dict:
    """What the API may show. The password itself never leaves the server."""
    view = {k: v for k, v in cfg.items() if k != "password_enc"}
    view["password_set"] = bool(cfg.get("password_enc"))
    view["plaintext_allowed"] = plaintext_allowed()
    return view


def plaintext_allowed() -> bool:
    return os.getenv("OBS_SMTP_ALLOW_PLAINTEXT", "").strip() == "1"


def smtp_password(cfg: dict) -> str | None:
    if not cfg.get("password_enc"):
        return None
    return secret_box.decrypt(cfg["password_enc"])


def validate_config(cfg: dict):
    if not cfg.get("host"):
        raise SmtpConfigError("SMTP host is not set.")
    if not cfg.get("sender_email") or not EMAIL_RE.match(cfg["sender_email"]):
        raise SmtpConfigError("Sender email is not set or not valid.")
    if cfg.get("security") == "none" and not plaintext_allowed():
        raise SmtpConfigError(
            "Unencrypted SMTP is disabled. Use STARTTLS or SSL, or set "
            "OBS_SMTP_ALLOW_PLAINTEXT=1 for an internal relay that has no TLS.")
    if cfg.get("auth_enabled") and not cfg.get("username"):
        raise SmtpConfigError("Authentication is enabled but no username is set.")


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
def _tls_context() -> ssl.SSLContext:
    # Certificate verification is always on. A private CA can be trusted with
    # OBS_SMTP_CA_FILE; there is deliberately no switch to skip verification.
    cafile = os.getenv("OBS_SMTP_CA_FILE") or None
    return ssl.create_default_context(cafile=cafile)


def open_smtp(cfg: dict, password: str | None, stages: list | None = None):
    """Connect, negotiate TLS and authenticate. Records each stage reached."""
    validate_config(cfg)
    stages = stages if stages is not None else []
    timeout = int(cfg.get("timeout_seconds") or 20)
    host, port = cfg["host"].strip(), int(cfg.get("port") or 587)
    if cfg.get("security") == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=_tls_context())
        stages.append("CONNECTED")
        stages.append("TLS")
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
        stages.append("CONNECTED")
        server.ehlo()
        if cfg.get("security") == "starttls":
            server.starttls(context=_tls_context())
            server.ehlo()
            stages.append("TLS")
    if cfg.get("auth_enabled"):
        if password is None:
            server.close()
            raise SmtpConfigError("Authentication is enabled but no password is stored.")
        server.login(cfg["username"], password)
        stages.append("AUTHENTICATED")
    return server


def redact(text: str, *secrets_) -> str:
    text = str(text or "")
    for secret in secrets_:
        if secret:
            text = text.replace(secret, "****")
    return text[:500]


def classify_error(exc: Exception) -> tuple[str, str]:
    """Return ('TEMP' | 'PERM', readable message)."""
    if isinstance(exc, SmtpConfigError):
        return "TEMP", str(exc)
    if isinstance(exc, secret_box.SecretUnavailable):
        return "TEMP", str(exc)
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "TEMP", f"Authentication failed ({exc.smtp_code}). Check the username and password."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in exc.recipients.values()]
        kind = "PERM" if codes and all(c >= 500 for c in codes) else "TEMP"
        detail = "; ".join(f"{addr}: {code} {msg.decode(errors='replace') if isinstance(msg, bytes) else msg}"
                           for addr, (code, msg) in exc.recipients.items())
        return kind, f"Recipient refused: {detail}"
    if isinstance(exc, smtplib.SMTPResponseException):
        message = exc.smtp_error.decode(errors="replace") if isinstance(exc.smtp_error, bytes) else exc.smtp_error
        # A refused sender is a configuration problem an admin can fix, so retry.
        if isinstance(exc, smtplib.SMTPSenderRefused):
            return "TEMP", f"Sender refused ({exc.smtp_code}): {message}"
        return ("PERM" if exc.smtp_code >= 500 else "TEMP"), f"Server replied {exc.smtp_code}: {message}"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "TEMP", f"TLS certificate verification failed: {exc.verify_message or exc}"
    if isinstance(exc, ssl.SSLError):
        return "TEMP", f"TLS negotiation failed: {exc}"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "TEMP", "Connection timed out."
    if isinstance(exc, socket.gaierror):
        return "TEMP", "SMTP host name could not be resolved."
    if isinstance(exc, (smtplib.SMTPException, OSError)):
        return "TEMP", f"{type(exc).__name__}: {exc}"
    return "TEMP", f"{type(exc).__name__}: {exc}"


def test_connection(cfg: dict) -> dict:
    """Connect, negotiate TLS and log in, then disconnect. Sends nothing."""
    stages: list[str] = []
    password = None
    started = utc_now()
    try:
        password = smtp_password(cfg)
        server = open_smtp(cfg, password, stages)
        try:
            server.noop()
        finally:
            server.quit()
        return {"ok": True, "stages": stages, "message": "Connected"
                + (", TLS negotiated" if "TLS" in stages else "")
                + (", authenticated" if "AUTHENTICATED" in stages else "") + ".",
                "tested_at": iso(started)}
    except Exception as exc:  # noqa: BLE001 - every failure is reported, none raised
        _, message = classify_error(exc)
        return {"ok": False, "stages": stages, "message": redact(message, password),
                "tested_at": iso(started)}


def build_message(job: dict, cfg: dict) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((cfg.get("sender_name") or "", cfg["sender_email"]))
    message["To"] = formataddr((job.get("recipient_name") or "", job["recipient_email"]))
    if cfg.get("reply_to"):
        message["Reply-To"] = cfg["reply_to"]
    message["Subject"] = " ".join(str(job["subject"]).split())
    message["Date"] = formatdate(localtime=False, usegmt=True)
    digest = hashlib.sha256(job["idempotency_key"].encode()).hexdigest()[:24]
    domain = cfg["sender_email"].rsplit("@", 1)[-1]
    message["Message-ID"] = f"<obs.{job['id']}.{digest}@{domain}>"
    message["X-OBS-Job"] = str(job["id"])
    message.set_content(job["body_text"])
    if job.get("body_html"):
        message.add_alternative(job["body_html"], subtype="html")
    return message


# --------------------------------------------------------------------------- #
# Delivery loop
# --------------------------------------------------------------------------- #
def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1))))


def queue_test_email(connection, actor: dict, to_email: str, to_name: str | None = None) -> int:
    from comms_core import load_settings, local_display, render_email
    settings = load_settings(connection)
    now = utc_now()
    subject, text, html_body = render_email(connection, "smtp_test", {
        "recipient_name": to_name or to_email, "sent_at": local_display(now, settings)})
    cfg = load_smtp(connection)
    return connection.execute(
        """INSERT INTO notification_jobs
           (idempotency_key,kind,recipient_email,recipient_name,subject,body_text,body_html,
            status,max_attempts,next_attempt_at,created_by,created_at)
           VALUES (?,'TEST',?,?,?,?,?,'PENDING',?,?,?,?)""",
        (f"TEST:{uuid.uuid4()}", to_email, to_name, subject, text, html_body,
         int(cfg.get("max_attempts") or 6), iso(now), actor.get("employee_id"), iso(now))).lastrowid


def deliver_due(connect, owner: str, limit: int = 20, job_ids: list[int] | None = None,
                force: bool = False) -> dict:
    """Send due jobs. `connect` returns a new sqlite connection.

    `force` lets an administrator's test message go out while SMTP is still
    switched off, so the settings can be proven before alerts depend on them.
    """
    connection = connect()
    try:
        cfg = load_smtp(connection)
        if not cfg.get("enabled") and not force:
            return {"claimed": 0, "skipped": "SMTP is disabled; jobs stay queued."}
        try:
            validate_config(cfg)
            password = smtp_password(cfg)
        except (SmtpConfigError, secret_box.SecretUnavailable) as exc:
            return {"claimed": 0, "skipped": f"{exc} Jobs stay queued."}

        now = utc_now()
        rate = max(1, int(cfg.get("rate_limit_per_minute") or 30))
        recent = connection.execute(
            "SELECT COUNT(*) c FROM notification_jobs WHERE status='ACCEPTED' AND accepted_at >= ?",
            (iso(now - timedelta(seconds=60)),)).fetchone()["c"]
        budget = min(limit, rate - recent)
        if budget <= 0:
            return {"claimed": 0, "skipped": "Rate limit reached for this minute."}

        lease = iso(now + timedelta(seconds=LEASE_SECONDS))
        with immediate(connection):
            if job_ids:
                marks = ",".join("?" * len(job_ids))
                where = (f"id IN ({marks}) AND (status IN ('PENDING','TEMP_FAILED') "
                         f"OR (status='PROCESSING' AND locked_until < ?))")
                params = (*job_ids, iso(now))
            else:
                where = ("(status IN ('PENDING','TEMP_FAILED') AND next_attempt_at <= ?) "
                         "OR (status='PROCESSING' AND locked_until < ?)")
                params = (iso(now), iso(now))
            connection.execute(
                f"""UPDATE notification_jobs
                    SET status='PROCESSING', lock_owner=?, locked_until=?, attempts=attempts+1, updated_at=?
                    WHERE id IN (SELECT id FROM notification_jobs WHERE {where}
                                 ORDER BY next_attempt_at, id LIMIT ?)""",
                (owner, lease, iso(now), *params, budget))
        jobs = [dict(r) for r in connection.execute(
            "SELECT * FROM notification_jobs WHERE status='PROCESSING' AND lock_owner=? AND locked_until=?",
            (owner, lease)).fetchall()]
        if not jobs:
            return {"claimed": 0}

        results = {"claimed": len(jobs), "accepted": 0, "temp_failed": 0, "failed": 0}
        server = None
        connect_error = None
        try:
            server = open_smtp(cfg, password)
        except Exception as exc:  # noqa: BLE001
            connect_error = exc

        for job in jobs:
            outcome, error, response = "ACCEPTED", None, None
            message = build_message(job, cfg)
            if connect_error is not None:
                kind, error = classify_error(connect_error)
                outcome = "FAILED" if kind == "PERM" else "TEMP_FAILED"
            else:
                try:
                    refused = server.send_message(message)
                    response = "250 accepted" if not refused else f"partially refused: {refused}"
                except Exception as exc:  # noqa: BLE001
                    kind, error = classify_error(exc)
                    outcome = "FAILED" if kind == "PERM" else "TEMP_FAILED"
                    if isinstance(exc, (smtplib.SMTPServerDisconnected, OSError)):
                        try:
                            server.close()
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            server = open_smtp(cfg, password)
                        except Exception as again:  # noqa: BLE001
                            connect_error = again
            done = utc_now()
            if outcome == "TEMP_FAILED" and job["attempts"] >= job["max_attempts"]:
                outcome = "FAILED"
                error = f"Gave up after {job['attempts']} attempts. Last error: {error}"
            with immediate(connection):
                if job["kind"] == "SECURITY" and outcome in ("ACCEPTED", "FAILED"):
                    # Sign-in codes and reset links must not sit in the delivery log.
                    connection.execute(
                        "UPDATE notification_jobs SET body_text=?, body_html=NULL WHERE id=?",
                        ("(Security message. The content was removed after sending.)", job["id"]))
                connection.execute(
                    """UPDATE notification_jobs SET status=?, last_error=?, smtp_response=COALESCE(?, smtp_response),
                           accepted_at=?, next_attempt_at=?, locked_until=NULL, lock_owner=NULL,
                           message_id=?, updated_at=?
                       WHERE id=? AND lock_owner=?""",
                    (outcome, redact(error, password, cfg.get("username")) if error else None, response,
                     iso(done) if outcome == "ACCEPTED" else None,
                     iso(done + _backoff(job["attempts"])) if outcome == "TEMP_FAILED" else job["next_attempt_at"],
                     message["Message-ID"], iso(done), job["id"], owner))
            results[{"ACCEPTED": "accepted", "TEMP_FAILED": "temp_failed", "FAILED": "failed"}[outcome]] += 1
        if server is not None:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass
        return results
    finally:
        connection.close()
