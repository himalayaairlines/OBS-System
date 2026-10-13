"""Sign-in, two-factor codes, password reset/invitation links, My Account.

Sign-in accepts the Employee ID or the account's e-mail address. Every failure
answers with the same message, so the screen cannot be used to find out which
accounts exist or which are locked; the real reason goes to the audit log.

Flow:
  POST /login             password -> session, or a code challenge when the
                          account has two-factor sign-in
  POST /login/verify      6-digit code -> session
  POST /login/resend      a new code (after 60 s)
  POST /password/forgot   e-mail a reset link (always answers the same)
  GET  /password/reset-info, POST /password/reset   use a reset or invitation link
"""

from datetime import datetime, timedelta, timezone
import secrets
import threading

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import account_mail
from auth_schema import UNUSABLE_PASSWORD
from database import get_connection
from security import (
    auth_setting, clear_recovery_failures, client_info, create_session, current_user,
    destroy_session, hash_password, log_action, needs_rehash, password_problems, permissions_for,
    record_recovery_failure, recovery_code_matches, recovery_configured, recovery_cooldown_remaining,
    revoke_user_sessions, session_alive, token_hash, utc_text, verify_password,
)

router = APIRouter(tags=["auth"])

GENERIC_FAILURE = "Invalid Employee ID / e-mail or password."
OTP_MINUTES = 5
OTP_RESEND_SECONDS = 60
OTP_MAX_ATTEMPTS = 5
OTP_MAX_SENDS = 5
RESET_MINUTES = 30
INVITE_HOURS = 72
# Per client address: failed sign-ins / reset requests allowed in the window.
IP_FAILURE_LIMIT = 20
IP_WINDOW_MINUTES = 10

_ip_failures: dict[str, list[datetime]] = {}
_ip_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(value):
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    name, domain = email.split("@", 1)
    return f"{name[:2]}{'*' * max(3, len(name) - 2)}@{domain}"


def _ip_blocked(ip: str | None) -> bool:
    if not ip:
        return False
    cutoff = datetime.now() - timedelta(minutes=IP_WINDOW_MINUTES)
    with _ip_lock:
        entries = [t for t in _ip_failures.get(ip, []) if t > cutoff]
        _ip_failures[ip] = entries
        return len(entries) >= IP_FAILURE_LIMIT


def _ip_failed(ip: str | None):
    if ip:
        with _ip_lock:
            _ip_failures.setdefault(ip, []).append(datetime.now())


def _audit(event, status, account=None, detail="", metadata=None, actor=None):
    """Security events: the account concerned goes in employee_id, the actor in metadata."""
    who = actor or {"employee_id": account, "role": None}
    meta = dict(metadata or {})
    if actor and account and account != actor.get("employee_id"):
        meta["account"] = account
    log_action(who, "AUTHENTICATION", event, detail or event.replace("_", " ").title(), "user", None,
               status=status, metadata=meta)


def _find_user(connection, identifier: str):
    ident = (identifier or "").strip()
    if not ident:
        return None
    if "@" in ident:
        rows = connection.execute("SELECT * FROM users WHERE lower(email)=?", (ident.lower(),)).fetchall()
        return dict(rows[0]) if len(rows) == 1 else None
    row = connection.execute("SELECT * FROM users WHERE employee_id=?", (ident.upper(),)).fetchone()
    return dict(row) if row else None


def _lock_state(connection, user) -> str | None:
    """'ADMIN', 'AUTO' (still running) or None. An expired automatic lock is cleared."""
    if not user.get("locked_at"):
        return None
    if user.get("lock_reason") == "ADMIN":
        return "ADMIN"
    minutes = int(auth_setting(connection, "lockout_minutes", "15"))
    locked = parse(user["locked_at"].replace(" ", "T") if "T" not in user["locked_at"] else user["locked_at"])
    if locked and utc_now() - locked < timedelta(minutes=minutes):
        return "AUTO"
    connection.execute("UPDATE users SET locked_at=NULL, lock_reason=NULL, failed_attempts=0 WHERE id=?",
                       (user["id"],))
    connection.commit()
    return None


def _session_payload(connection, user_id: int, token: str):
    row = dict(connection.execute(
        """SELECT id, employee_id, name, role, email, email_verified, two_factor, must_change_password
           FROM users WHERE id=?""", (user_id,)).fetchone())
    return {"id": row["id"], "employee_id": row["employee_id"], "name": row["name"], "role": row["role"],
            "email": row["email"], "two_factor": bool(row["two_factor"]),
            "must_change_password": bool(row["must_change_password"]),
            "token": token, "permissions": sorted(permissions_for(row["role"]))}


def _finish_login(connection, user, two_factor: bool):
    """Open the session and record the sign-in."""
    info = client_info()
    known = connection.execute(
        "SELECT 1 FROM user_sessions WHERE user_id=? AND user_agent=? LIMIT 1",
        (user["id"], info.get("ua") or "")).fetchone()
    token = create_session(user, two_factor=two_factor, connection=connection)
    connection.execute("UPDATE users SET last_login_at=?, last_login_ip=?, failed_attempts=0 WHERE id=?",
                       (iso(utc_now()), info.get("ip"), user["id"]))
    connection.commit()
    _audit("LOGIN_SUCCESS", "SUCCESS", user["employee_id"], f"{user['employee_id']} signed in.",
           {"new_device": not bool(known), "two_factor": two_factor})
    return _session_payload(connection, user["id"], token)


def app_url(connection) -> str:
    import os
    return (os.getenv("OBS_APP_URL", "").strip() or auth_setting(connection, "app_url", "")).rstrip("/")


def issue_link(connection, user: dict, kind: str) -> str:
    """Create a RESET or INVITE token, queue its e-mail and return the job id. Caller sends."""
    base = app_url(connection)
    if not base:
        raise HTTPException(
            status_code=400,
            detail="The application address is not set, so e-mail links cannot be built. "
                   "Set it under SMTP settings (Application address).")
    if not user.get("email"):
        raise HTTPException(status_code=400, detail="This account has no e-mail address.")
    now = utc_now()
    minutes = INVITE_HOURS * 60 if kind == "INVITE" else RESET_MINUTES
    connection.execute("UPDATE auth_tokens SET used_at=? WHERE user_id=? AND purpose=? AND used_at IS NULL",
                       (iso(now), user["id"], kind))
    token = secrets.token_urlsafe(32)
    connection.execute(
        """INSERT INTO auth_tokens(purpose,token_hash,user_id,created_at,expires_at,ip_address,last_sent_at)
           VALUES (?,?,?,?,?,?,?)""",
        (kind, token_hash(token), user["id"], iso(now), iso(now + timedelta(minutes=minutes)),
         client_info().get("ip"), iso(now)))
    url = f"{base}/reset-password?token={token}" + ("&invite=1" if kind == "INVITE" else "")
    return account_mail.queue_link(connection, user, url, kind, minutes)


def _valid_link(connection, token: str):
    row = connection.execute(
        "SELECT * FROM auth_tokens WHERE token_hash=? AND purpose IN ('RESET','INVITE')",
        (token_hash(token or ""),)).fetchone()
    if not row or row["used_at"] or parse(row["expires_at"]) <= utc_now():
        raise HTTPException(status_code=400,
                            detail="This link is invalid or has expired. Ask for a new one.")
    user = connection.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
    if not user or (not user["active"] and row["purpose"] != "INVITE"):
        raise HTTPException(status_code=400, detail="This link is invalid or has expired. Ask for a new one.")
    return dict(row), dict(user)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class LoginRequest(BaseModel):
    identifier: str | None = Field(default=None, max_length=254)
    employee_id: str | None = Field(default=None, max_length=254)  # older apps
    password: str = Field(max_length=256)


class VerifyRequest(BaseModel):
    challenge: str = Field(max_length=100)
    code: str = Field(max_length=12)


class ResendRequest(BaseModel):
    challenge: str = Field(max_length=100)


class ForgotRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)


class ResetRequest(BaseModel):
    token: str = Field(max_length=200)
    new_password: str = Field(max_length=256)
    confirm_password: str = Field(max_length=256)


class ChangePassword(BaseModel):
    old_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)
    confirm_password: str = Field(max_length=256)


class AdminRecovery(BaseModel):
    employee_id: str
    recovery_code: str
    new_password: str = Field(max_length=256)
    confirm_password: str = Field(max_length=256)


# --------------------------------------------------------------------------- #
# Sign-in
# --------------------------------------------------------------------------- #
@router.post("/login")
def login(data: LoginRequest):
    ip = client_info().get("ip")
    identifier = (data.identifier or data.employee_id or "").strip()
    if _ip_blocked(ip):
        _audit("LOGIN_THROTTLED", "FAILED", identifier, f"Too many failed sign-ins from {ip}.")
        raise HTTPException(status_code=429, detail="Too many attempts. Wait a few minutes and try again.")
    connection = get_connection()
    try:
        user = _find_user(connection, identifier)
        if not user or user["password"] == UNUSABLE_PASSWORD:
            _ip_failed(ip)
            _audit("LOGIN_FAILED", "FAILED", identifier, f"Failed sign-in for {identifier}.",
                   {"reason": "unknown_user_or_no_password"})
            raise HTTPException(status_code=401, detail=GENERIC_FAILURE)
        lock = _lock_state(connection, user)
        if lock:
            _ip_failed(ip)
            _audit("LOGIN_BLOCKED", "FAILED", user["employee_id"], f"Sign-in to locked account {user['employee_id']}.",
                   {"reason": "locked", "lock": lock})
            raise HTTPException(status_code=401, detail=GENERIC_FAILURE)
        if not verify_password(data.password, user["password"]):
            _ip_failed(ip)
            attempts = int(user["failed_attempts"] or 0) + 1
            limit = int(auth_setting(connection, "lockout_attempts", "5"))
            locking = attempts >= limit
            connection.execute(
                "UPDATE users SET failed_attempts=?, locked_at=?, lock_reason=? WHERE id=?",
                (attempts, iso(utc_now()) if locking else None, "AUTO" if locking else None, user["id"]))
            connection.commit()
            _audit("ACCOUNT_LOCKED" if locking else "LOGIN_FAILED", "FAILED", user["employee_id"],
                   f"{user['employee_id']}: wrong password ({attempts} of {limit}).",
                   {"reason": "wrong_password", "failed_attempts": attempts})
            raise HTTPException(status_code=401, detail=GENERIC_FAILURE)
        if not user["active"]:
            _audit("LOGIN_FAILED", "FAILED", user["employee_id"], f"{user['employee_id']} is disabled.",
                   {"reason": "disabled"})
            raise HTTPException(status_code=401, detail=GENERIC_FAILURE)

        updates = {"failed_attempts": 0}
        if needs_rehash(user["password"]):
            updates["password"] = hash_password(data.password)
        if auth_setting(connection, "require_strong_passwords", "1") == "1" and password_problems(data.password, user):
            updates["must_change_password"] = 1
        sets = ", ".join(f"{k}=?" for k in updates)
        connection.execute(f"UPDATE users SET {sets} WHERE id=?", (*updates.values(), user["id"]))
        connection.commit()

        if user["two_factor"] and user["email"]:
            _audit("LOGIN_PASSWORD_OK", "SUCCESS", user["employee_id"], "Password accepted; code required.")
            return _start_challenge(connection, user)
        return _finish_login(connection, user, two_factor=False)
    finally:
        connection.close()


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _start_challenge(connection, user):
    if not account_mail.smtp_ready(connection):
        _audit("OTP_FAILED", "FAILED", user["employee_id"], "Sign-in code could not be sent: e-mail is off.",
               {"reason": "smtp_disabled"})
        raise HTTPException(status_code=503,
                            detail="Your verification code could not be sent because e-mail is not set up. "
                                   "Contact the system administrator.")
    now = utc_now()
    challenge = secrets.token_urlsafe(18)
    code = _new_code()
    connection.execute(
        "UPDATE auth_tokens SET used_at=? WHERE user_id=? AND purpose='OTP' AND used_at IS NULL",
        (iso(now), user["id"]))
    connection.execute(
        """INSERT INTO auth_tokens(purpose,token_hash,challenge,user_id,created_at,expires_at,ip_address,last_sent_at)
           VALUES ('OTP',?,?,?,?,?,?,?)""",
        (token_hash(challenge + ":" + code), challenge, user["id"], iso(now),
         iso(now + timedelta(minutes=OTP_MINUTES)), client_info().get("ip"), iso(now)))
    job = account_mail.queue_code(connection, user, code, OTP_MINUTES)
    connection.commit()
    status = account_mail.send_now(job)
    if status != "ACCEPTED":
        _audit("OTP_FAILED", "FAILED", user["employee_id"], "Sign-in code could not be sent.",
               {"challenge": challenge[:8], "delivery": status})
        raise HTTPException(status_code=503, detail="Your verification code could not be sent. Try again "
                                                     "in a moment or contact the system administrator.")
    _audit("OTP_SENT", "SUCCESS", user["employee_id"], "Sign-in code e-mailed.", {"challenge": challenge[:8]})
    return {"otp_required": True, "challenge": challenge, "email": mask_email(user["email"]),
            "expires_in": OTP_MINUTES * 60, "resend_in": OTP_RESEND_SECONDS}


def _challenge(connection, challenge: str):
    row = connection.execute("SELECT * FROM auth_tokens WHERE purpose='OTP' AND challenge=?",
                             (challenge,)).fetchone()
    if not row or row["used_at"] or parse(row["expires_at"]) <= utc_now() or row["attempts"] >= OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=400, detail="This code has expired. Sign in again to get a new one.")
    return dict(row)


@router.post("/login/verify")
def verify_code(data: VerifyRequest):
    ip = client_info().get("ip")
    if _ip_blocked(ip):
        raise HTTPException(status_code=429, detail="Too many attempts. Wait a few minutes and try again.")
    connection = get_connection()
    try:
        row = _challenge(connection, data.challenge)
        user = dict(connection.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone())
        code = "".join(ch for ch in data.code if ch.isdigit())
        if token_hash(data.challenge + ":" + code) != row["token_hash"]:
            _ip_failed(ip)
            attempts = row["attempts"] + 1
            connection.execute("UPDATE auth_tokens SET attempts=? WHERE id=?", (attempts, row["id"]))
            connection.commit()
            _audit("OTP_FAILED", "FAILED", user["employee_id"], "Wrong sign-in code.",
                   {"challenge": data.challenge[:8], "attempts": attempts})
            left = OTP_MAX_ATTEMPTS - attempts
            if left <= 0:
                raise HTTPException(status_code=400, detail="Too many wrong codes. Sign in again to get a new one.")
            raise HTTPException(status_code=400, detail=f"That code is not correct. {left} "
                                                        f"attempt{'s' if left != 1 else ''} left.")
        if not user["active"] or _lock_state(connection, user):
            raise HTTPException(status_code=401, detail=GENERIC_FAILURE)
        connection.execute("UPDATE auth_tokens SET used_at=? WHERE id=?", (iso(utc_now()), row["id"]))
        connection.execute("UPDATE users SET email_verified=1 WHERE id=?", (user["id"],))
        connection.commit()
        return _finish_login(connection, user, two_factor=True)
    finally:
        connection.close()


@router.post("/login/resend")
def resend_code(data: ResendRequest):
    connection = get_connection()
    try:
        row = _challenge(connection, data.challenge)
        since = (utc_now() - parse(row["last_sent_at"] or row["created_at"])).total_seconds()
        if since < OTP_RESEND_SECONDS:
            raise HTTPException(status_code=429, detail=f"Wait {int(OTP_RESEND_SECONDS - since)} s before "
                                                        "asking for another code.")
        if row["sends"] >= OTP_MAX_SENDS:
            raise HTTPException(status_code=429, detail="Too many codes requested. Sign in again.")
        user = dict(connection.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone())
        now = utc_now()
        code = _new_code()
        connection.execute(
            "UPDATE auth_tokens SET token_hash=?, expires_at=?, attempts=0, sends=sends+1, last_sent_at=? WHERE id=?",
            (token_hash(data.challenge + ":" + code), iso(now + timedelta(minutes=OTP_MINUTES)), iso(now), row["id"]))
        job = account_mail.queue_code(connection, user, code, OTP_MINUTES)
        connection.commit()
        status = account_mail.send_now(job)
        _audit("OTP_SENT", "SUCCESS" if status == "ACCEPTED" else "FAILED", user["employee_id"],
               "Sign-in code e-mailed again.", {"challenge": data.challenge[:8], "resend": True})
        if status != "ACCEPTED":
            raise HTTPException(status_code=503, detail="The code could not be sent. Try again in a moment.")
        return {"otp_required": True, "challenge": data.challenge, "email": mask_email(user["email"]),
                "expires_in": OTP_MINUTES * 60, "resend_in": OTP_RESEND_SECONDS}
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# Password reset and invitations
# --------------------------------------------------------------------------- #
FORGOT_REPLY = {"status": "sent",
                "detail": "If an account matches, a link to reset the password has been sent to its e-mail "
                          "address. The link is valid for 30 minutes."}


@router.post("/password/forgot")
def forgot_password(data: ForgotRequest):
    ip = client_info().get("ip")
    if _ip_blocked(ip):
        raise HTTPException(status_code=429, detail="Too many attempts. Wait a few minutes and try again.")
    _ip_failed(ip)  # every request counts toward the address limit
    connection = get_connection()
    try:
        user = _find_user(connection, data.identifier)
        if not user or not user["active"] or not user.get("email") or user["password"] == UNUSABLE_PASSWORD:
            _audit("PASSWORD_RESET_REQUESTED", "FAILED", data.identifier.strip(),
                   "Reset requested for an account that cannot receive one.",
                   {"reason": "no_matching_active_account_with_email"})
            return FORGOT_REPLY
        recent = connection.execute(
            "SELECT COUNT(*) c FROM auth_tokens WHERE user_id=? AND purpose='RESET' AND created_at>=?",
            (user["id"], iso(utc_now() - timedelta(hours=1)))).fetchone()["c"]
        if recent >= 3:
            _audit("PASSWORD_RESET_REQUESTED", "FAILED", user["employee_id"], "Reset link limit reached.",
                   {"reason": "too_many_requests"})
            return FORGOT_REPLY
        try:
            job = issue_link(connection, user, "RESET")
        except HTTPException as exc:
            _audit("PASSWORD_RESET_REQUESTED", "FAILED", user["employee_id"], exc.detail, {"reason": "config"})
            return FORGOT_REPLY
        connection.commit()
    finally:
        connection.close()
    status = account_mail.send_now(job)
    _audit("PASSWORD_RESET_REQUESTED", "SUCCESS" if status == "ACCEPTED" else "FAILED", user["employee_id"],
           "Reset link e-mailed." if status == "ACCEPTED" else f"Reset link not sent ({status}).")
    return FORGOT_REPLY


@router.get("/password/reset-info")
def reset_info(token: str = Query(..., max_length=200)):
    connection = get_connection()
    try:
        row, user = _valid_link(connection, token)
    finally:
        connection.close()
    return {"kind": row["purpose"], "name": user["name"], "email": mask_email(user["email"]),
            "employee_id": user["employee_id"]}


@router.post("/password/reset")
def reset_password(data: ResetRequest):
    if data.new_password != data.confirm_password:
        raise HTTPException(status_code=400, detail="The two passwords do not match.")
    connection = get_connection()
    try:
        row, user = _valid_link(connection, data.token)
        problems = password_problems(data.new_password, user)
        if problems:
            raise HTTPException(status_code=400, detail=" ".join(problems))
        now = iso(utc_now())
        connection.execute(
            """UPDATE users SET password=?, password_changed_at=?, must_change_password=0, failed_attempts=0,
                   email_verified=1, active=CASE WHEN ? THEN 1 ELSE active END,
                   locked_at=CASE WHEN lock_reason='ADMIN' THEN locked_at ELSE NULL END,
                   lock_reason=CASE WHEN lock_reason='ADMIN' THEN lock_reason ELSE NULL END
               WHERE id=?""",
            (hash_password(data.new_password), now, row["purpose"] == "INVITE", user["id"]))
        connection.execute("UPDATE auth_tokens SET used_at=? WHERE user_id=? AND purpose IN ('RESET','INVITE') "
                           "AND used_at IS NULL", (now, user["id"]))
        ended = revoke_user_sessions(connection, user["id"], "PASSWORD_RESET")
        job = None
        if row["purpose"] == "RESET":
            job = account_mail.queue_notice(connection, user, "The password of your account was reset with an "
                                            "e-mailed link.", client_info().get("ip"), now.replace("T", " ")[:16])
        connection.commit()
    finally:
        connection.close()
    if job:
        account_mail.send_now(job)
    event = "INVITE_ACCEPTED" if row["purpose"] == "INVITE" else "PASSWORD_RESET"
    _audit(event, "SUCCESS", user["employee_id"], "Password set with an e-mailed link.", {"sessions_ended": ended})
    return {"status": "success", "kind": row["purpose"],
            "detail": ("Your password has been set. You can sign in now." if row["purpose"] == "INVITE" else
                       "Your password has been changed and all sessions were signed out. Sign in with the new "
                       "password.")}


# --------------------------------------------------------------------------- #
# Administrator recovery (only when OBS_ADMIN_RECOVERY_CODE is set)
# --------------------------------------------------------------------------- #
@router.get("/admin-recovery")
def admin_recovery_available():
    return {"available": recovery_configured()}


@router.post("/admin-recovery")
def admin_recovery(data: AdminRecovery):
    employee_id = data.employee_id.strip().upper()
    if not recovery_configured():
        raise HTTPException(status_code=404, detail="Recovery codes are not enabled on this server. "
                                                    "Use 'Forgot password' instead.")
    cooldown = recovery_cooldown_remaining()
    if cooldown:
        raise HTTPException(status_code=429,
                            detail=f"Too many incorrect recovery codes. Try again in {cooldown} minute(s).")
    if data.new_password != data.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirmation do not match.")
    if not recovery_code_matches(data.recovery_code):
        record_recovery_failure()
        _audit("RECOVERY_FAILED", "FAILED", employee_id, f"Incorrect recovery code used against {employee_id}.")
        raise HTTPException(status_code=401, detail="The recovery code is not correct.")
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM users WHERE employee_id=?", (employee_id,)).fetchone()
        if not row or row["role"] != "admin" or not row["active"]:
            _audit("RECOVERY_REFUSED", "FAILED", employee_id, "Recovery refused: not an active administrator.")
            raise HTTPException(status_code=400, detail="Recovery only applies to an active administrator account.")
        problems = password_problems(data.new_password, dict(row))
        if problems:
            raise HTTPException(status_code=400, detail=" ".join(problems))
        connection.execute(
            """UPDATE users SET password=?, password_changed_at=?, must_change_password=0, failed_attempts=0,
                   locked_at=NULL, lock_reason=NULL WHERE id=?""",
            (hash_password(data.new_password), iso(utc_now()), row["id"]))
        revoke_user_sessions(connection, row["id"], "RECOVERY_RESET")
        connection.commit()
    finally:
        connection.close()
    clear_recovery_failures()
    _audit("RECOVERY_PASSWORD_RESET", "SUCCESS", employee_id, "Password set with the recovery code.")
    return {"status": "success", "employee_id": employee_id,
            "detail": "Password changed. You can sign in with it now."}


# --------------------------------------------------------------------------- #
# Signed-in user
# --------------------------------------------------------------------------- #
@router.post("/logout")
def logout(user: dict = Depends(current_user)):
    destroy_session(user.get("token"))
    _audit("LOGOUT", "SUCCESS", user["employee_id"], f"{user['employee_id']} signed out.")
    return {"status": "success"}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    out = {k: user[k] for k in ("id", "employee_id", "name", "role", "email")}
    out.update(two_factor=bool(user["two_factor"]), must_change_password=bool(user["must_change_password"]),
               permissions=sorted(permissions_for(user["role"])))
    return out


@router.post("/change-password")
def change_password(data: ChangePassword, user: dict = Depends(current_user)):
    if data.new_password != data.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirmation do not match.")
    connection = get_connection()
    try:
        row = dict(connection.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone())
        if not verify_password(data.old_password, row["password"]):
            _audit("PASSWORD_CHANGE_FAILED", "FAILED", user["employee_id"], "Wrong current password.")
            raise HTTPException(status_code=400, detail="The current password is incorrect.")
        if data.new_password == data.old_password:
            raise HTTPException(status_code=400, detail="Choose a password different from the current one.")
        problems = password_problems(data.new_password, row)
        if problems:
            raise HTTPException(status_code=400, detail=" ".join(problems))
        now = iso(utc_now())
        connection.execute(
            "UPDATE users SET password=?, password_changed_at=?, must_change_password=0 WHERE id=?",
            (hash_password(data.new_password), now, user["id"]))
        ended = revoke_user_sessions(connection, user["id"], "PASSWORD_CHANGED", keep_session_id=user["session_id"])
        job = None
        if row.get("email"):
            job = account_mail.queue_notice(connection, row, "The password of your account was changed.",
                                            client_info().get("ip"), now.replace("T", " ")[:16])
        connection.commit()
    finally:
        connection.close()
    if job:
        account_mail.send_now(job)
    _audit("PASSWORD_CHANGED", "SUCCESS", user["employee_id"], "Password changed.", {"other_sessions_ended": ended})
    return {"status": "success", "other_sessions_ended": ended}


@router.get("/account")
def account(user: dict = Depends(current_user)):
    connection = get_connection()
    try:
        row = dict(connection.execute(
            """SELECT id, employee_id, name, role, email, email_verified, two_factor, last_login_at,
                      last_login_ip, password_changed_at, must_change_password FROM users WHERE id=?""",
            (user["id"],)).fetchone())
        idle = int(auth_setting(connection, "session_idle_minutes", "60"))
        hours = int(auth_setting(connection, "session_hours", "12"))
        device_hours = int(auth_setting(connection, "device_session_hours", "24"))
    finally:
        connection.close()
    from security import DEVICE_ROLES
    row["session_policy"] = (f"This account is an OBS device: sessions end {device_hours} hours after sign-in."
                             if user["role"] in DEVICE_ROLES else
                             f"Sessions end after {idle} minutes without activity or {hours} hours after sign-in.")
    row["email_verified"] = bool(row["email_verified"])
    row["last_login_at"] = utc_text(row["last_login_at"])
    row["password_changed_at"] = utc_text(row["password_changed_at"])
    row["two_factor"] = bool(row["two_factor"])
    row["must_change_password"] = bool(row["must_change_password"])
    return row


def _live_sessions(connection, user_id: int, current_id: int | None = None):
    now = utc_now()
    rows = connection.execute(
        "SELECT * FROM user_sessions WHERE user_id=? AND revoked_at IS NULL ORDER BY last_seen_at DESC",
        (user_id,)).fetchall()
    return [{"id": r["id"], "device": r["device"], "ip_address": r["ip_address"], "created_at": r["created_at"],
             "last_seen_at": r["last_seen_at"], "two_factor": bool(r["two_factor"]),
             "current": r["id"] == current_id} for r in rows if session_alive(r, now)]


@router.get("/account/sessions")
def my_sessions(user: dict = Depends(current_user)):
    connection = get_connection()
    try:
        return _live_sessions(connection, user["id"], user["session_id"])
    finally:
        connection.close()


@router.delete("/account/sessions/{session_id}")
def end_my_session(session_id: int, user: dict = Depends(current_user)):
    connection = get_connection()
    try:
        changed = connection.execute(
            "UPDATE user_sessions SET revoked_at=?, revoked_reason='SIGNED_OUT_BY_USER' "
            "WHERE id=? AND user_id=? AND revoked_at IS NULL", (iso(utc_now()), session_id, user["id"])).rowcount
        connection.commit()
    finally:
        connection.close()
    if not changed:
        raise HTTPException(status_code=404, detail="Session not found.")
    _audit("SESSION_REVOKED", "SUCCESS", user["employee_id"], "Signed out a device.", {"session": session_id})
    return {"status": "success"}


@router.post("/account/sessions/revoke-others")
def end_other_sessions(user: dict = Depends(current_user)):
    connection = get_connection()
    try:
        ended = revoke_user_sessions(connection, user["id"], "SIGNED_OUT_BY_USER", keep_session_id=user["session_id"])
        connection.commit()
    finally:
        connection.close()
    _audit("SESSIONS_REVOKED", "SUCCESS", user["employee_id"], "Signed out all other devices.", {"ended": ended})
    return {"status": "success", "ended": ended}
