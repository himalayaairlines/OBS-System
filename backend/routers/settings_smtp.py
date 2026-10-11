"""SMTP configuration: view (masked), update, test connection, send a test email.

Reading the settings, changing them and testing them are separate permissions
(smtp_view, smtp_manage, smtp_test). The stored password is encrypted and is
never returned; the API only says whether one is set.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from comms_core import EMAIL_RE, allowed_email_domains, load_settings, utc_now, iso
from database import get_connection
from security import log_action, requires
import mailer
import secret_box

router = APIRouter(tags=["settings"])


class SmtpIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    host: str | None = Field(default=None, max_length=253)
    port: int = Field(default=587, ge=1, le=65535)
    security: str = Field(default="starttls", pattern="^(starttls|ssl|none)$")
    auth_enabled: bool = True
    username: str | None = Field(default=None, max_length=254)
    # None keeps the stored password; a value replaces it.
    password: str | None = Field(default=None, max_length=512)
    clear_password: bool = False
    sender_email: str | None = Field(default=None, max_length=254)
    sender_name: str | None = Field(default=None, max_length=120)
    reply_to: str | None = Field(default=None, max_length=254)
    timeout_seconds: int = Field(default=20, ge=3, le=120)
    max_attempts: int = Field(default=6, ge=1, le=20)
    rate_limit_per_minute: int = Field(default=30, ge=1, le=600)


class TestEmailIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_email: str = Field(max_length=254)


@router.get("/settings/smtp")
def get_smtp(user: dict = Depends(requires("smtp_view"))):
    connection = get_connection()
    try:
        cfg = mailer.load_smtp(connection)
        view = mailer.public_smtp(cfg)
        view["queue"] = {r["status"]: r["c"] for r in connection.execute(
            "SELECT status, COUNT(*) c FROM notification_jobs GROUP BY status").fetchall()}
        return view
    finally:
        connection.close()


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


@router.put("/settings/smtp")
def update_smtp(data: SmtpIn, user: dict = Depends(requires("smtp_manage"))):
    for label, value in (("Sender email", data.sender_email), ("Reply-To", data.reply_to)):
        if _clean(value) and not EMAIL_RE.match(value.strip()):
            raise HTTPException(status_code=400, detail=f"{label} is not a valid email address.")
    if data.security == "none" and not mailer.plaintext_allowed():
        raise HTTPException(
            status_code=400,
            detail="Unencrypted SMTP is disabled on this server. Choose STARTTLS or SSL.")
    connection = get_connection()
    try:
        before = mailer.load_smtp(connection)
        values = {
            "enabled": int(data.enabled), "host": _clean(data.host), "port": data.port,
            "security": data.security, "auth_enabled": int(data.auth_enabled),
            "username": _clean(data.username), "sender_email": _clean(data.sender_email),
            "sender_name": _clean(data.sender_name), "reply_to": _clean(data.reply_to),
            "timeout_seconds": data.timeout_seconds, "max_attempts": data.max_attempts,
            "rate_limit_per_minute": data.rate_limit_per_minute,
        }
        password_enc = before.get("password_enc")
        password_change = None
        if data.clear_password:
            password_enc, password_change = None, "cleared"
        elif data.password:
            password_enc, password_change = secret_box.encrypt(data.password), "replaced"
        if values["enabled"]:
            candidate = {**values, "password_enc": password_enc}
            try:
                mailer.validate_config(candidate)
            except mailer.SmtpConfigError as exc:
                raise HTTPException(status_code=400, detail=f"Cannot enable SMTP: {exc}")
            if candidate["auth_enabled"] and not password_enc:
                raise HTTPException(status_code=400, detail="Cannot enable SMTP: no password is stored.")
        connection.execute(
            """UPDATE smtp_settings SET enabled=:enabled, host=:host, port=:port, security=:security,
                   auth_enabled=:auth_enabled, username=:username, sender_email=:sender_email,
                   sender_name=:sender_name, reply_to=:reply_to, timeout_seconds=:timeout_seconds,
                   max_attempts=:max_attempts, rate_limit_per_minute=:rate_limit_per_minute,
                   password_enc=:password_enc, updated_by=:updated_by, updated_at=:updated_at
               WHERE id=1""",
            {**values, "password_enc": password_enc, "updated_by": user["employee_id"],
             "updated_at": iso(utc_now())})
        connection.commit()
        after = mailer.load_smtp(connection)
    finally:
        connection.close()
    changed = {k: {"from": before.get(k), "to": after.get(k)}
               for k in values if before.get(k) != after.get(k)}
    if password_change:
        changed["password"] = password_change  # never the value
    log_action(user, "SMTP", "UPDATE_SMTP_SETTINGS",
               f"Updated SMTP settings ({', '.join(changed) or 'no changes'}).",
               "smtp_settings", 1, metadata={"changed": changed})
    return mailer.public_smtp(after)


@router.post("/settings/smtp/test-connection")
def test_smtp_connection(user: dict = Depends(requires("smtp_test"))):
    connection = get_connection()
    try:
        cfg = mailer.load_smtp(connection)
    finally:
        connection.close()
    result = mailer.test_connection(cfg)
    log_action(user, "SMTP", "TEST_SMTP_CONNECTION", result["message"], "smtp_settings", 1,
               status="SUCCESS" if result["ok"] else "FAILED", metadata={"stages": result["stages"]})
    return result


@router.post("/settings/smtp/send-test")
def send_test_email(data: TestEmailIn, user: dict = Depends(requires("smtp_test"))):
    address = data.to_email.strip().lower()
    if not EMAIL_RE.match(address):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    connection = get_connection()
    try:
        cfg = mailer.load_smtp(connection)
        settings = load_settings(connection)
        registered = connection.execute(
            "SELECT name FROM users WHERE lower(email)=? AND active=1", (address,)).fetchone()
        domain = address.rsplit("@", 1)[-1]
        sender_domain = (cfg.get("sender_email") or "").rsplit("@", 1)[-1].lower()
        if not registered and domain != sender_domain and domain not in allowed_email_domains(settings):
            raise HTTPException(
                status_code=400,
                detail="Test messages may only go to a registered user's address, the sender's "
                       "domain, or an allowed external domain.")
        with connection:
            job_id = mailer.queue_test_email(connection, user, address,
                                             registered["name"] if registered else None)
    finally:
        connection.close()

    owner = f"test:{user['employee_id']}:{job_id}"
    outcome = mailer.deliver_due(get_connection, owner, limit=1, job_ids=[job_id], force=True)
    connection = get_connection()
    try:
        if outcome.get("skipped"):
            # A test that could not even start should not go out hours later.
            with connection:
                connection.execute(
                    "UPDATE notification_jobs SET status='CANCELLED', last_error=? WHERE id=? AND status='PENDING'",
                    (outcome["skipped"], job_id))
        job = dict(connection.execute(
            "SELECT id, status, attempts, last_error, smtp_response, message_id, created_at, "
            "accepted_at, next_attempt_at FROM notification_jobs WHERE id=?", (job_id,)).fetchone())
    finally:
        connection.close()
    job["note"] = ("ACCEPTED means the SMTP server took the message. It does not prove it reached "
                   "the inbox; ask the recipient to confirm.")
    if outcome.get("skipped"):
        job["note"] = outcome["skipped"]
    log_action(user, "SMTP", "SEND_TEST_EMAIL", f"Test email job #{job_id} to {address}: {job['status']}.",
               "notification_job", job_id,
               status="SUCCESS" if job["status"] == "ACCEPTED" else "FAILED")
    return job
