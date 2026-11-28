"""Password hashing, sessions, permission guards and audit logging.

Sessions (7.5) live in the `user_sessions` table. The client holds a random
bearer token; the database keeps only its SHA-256, the device, address and
timestamps. A session ends when it is signed out or revoked, after
`session_idle_minutes` without a request, or `session_hours` after sign-in
(OBS iPad accounts: `device_session_hours`, no idle limit). Changing or
resetting a password ends every other session of that user.
"""

from contextvars import ContextVar
from datetime import datetime, date, timedelta, timezone
import hashlib
import hmac
import json
import os
import re
import secrets

from fastapi import Depends, Header, HTTPException, Request

from database import ROLE_PERMISSIONS, get_connection

PBKDF2_ROUNDS = 310_000
# The account that works offline on board; see the module docstring.
DEVICE_ROLES = {"crew_supervisor", "crew"}
# While a weak or default password must be changed, only these work.
PASSWORD_CHANGE_PATHS = {"/me", "/logout", "/change-password", "/account", "/account/sessions"}

_CLIENT: ContextVar[dict] = ContextVar("obs_client", default={})


# --------------------------------------------------------------------------- #
# Request context (set by the middleware in main.py)
# --------------------------------------------------------------------------- #
def set_client(ip: str | None, user_agent: str | None, client: str | None, path: str | None):
    return _CLIENT.set({"ip": ip, "ua": (user_agent or "")[:300], "client": client, "path": path})


def reset_client(token):
    _CLIENT.reset(token)


def client_info() -> dict:
    return _CLIENT.get() or {}


def describe_device(user_agent: str | None, client: str | None = None) -> str:
    """'Chrome on Windows', 'OBS app on iPad'... Good enough to recognise a device."""
    ua = user_agent or ""
    if client in ("ios-app", "android-app"):
        return "OBS app on " + ("iPad / iPhone" if client == "ios-app" else "Android")
    browser = next((name for key, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                                           ("Chrome/", "Chrome"), ("Safari/", "Safari")) if key in ua), None)
    system = next((name for key, name in (("iPad", "iPad"), ("iPhone", "iPhone"), ("Android", "Android"),
                                          ("Windows", "Windows"), ("Mac OS X", "macOS"), ("Linux", "Linux"))
                   if key in ua), None)
    if "okhttp" in ua or "Expo" in ua or "CFNetwork" in ua:
        return "OBS app"
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or "Unknown device"


# --------------------------------------------------------------------------- #
# Administrator recovery code
# --------------------------------------------------------------------------- #
# Break-glass reset for an administrator who cannot use the e-mailed reset
# link. It works only when OBS_ADMIN_RECOVERY_CODE is set on the server: there
# is deliberately no built-in default, because a code shipped with the software
# would be the same for everyone.
RECOVERY_ATTEMPT_LIMIT = 5
RECOVERY_COOLDOWN_MINUTES = 15

_recovery_failures: list[datetime] = []


def _normalise_code(code: str) -> str:
    return (code or "").strip().upper()


def recovery_configured() -> bool:
    return len(os.getenv("OBS_ADMIN_RECOVERY_CODE", "").strip()) >= 12


def recovery_code_matches(code: str) -> bool:
    """True when `code` is the configured administrator recovery code."""
    configured = os.getenv("OBS_ADMIN_RECOVERY_CODE", "").strip()
    supplied = _normalise_code(code)
    if not supplied or not recovery_configured():
        return False
    return hmac.compare_digest(supplied, _normalise_code(configured))


def recovery_cooldown_remaining() -> int:
    """Minutes left before recovery may be attempted again, or 0."""
    cutoff = datetime.now() - timedelta(minutes=RECOVERY_COOLDOWN_MINUTES)
    _recovery_failures[:] = [t for t in _recovery_failures if t > cutoff]
    if len(_recovery_failures) < RECOVERY_ATTEMPT_LIMIT:
        return 0
    oldest = min(_recovery_failures)
    remaining = (oldest + timedelta(minutes=RECOVERY_COOLDOWN_MINUTES)) - datetime.now()
    return max(1, int(remaining.total_seconds() // 60) + 1)


def record_recovery_failure():
    _recovery_failures.append(datetime.now())


def clear_recovery_failures():
    _recovery_failures.clear()


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(raw: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", raw.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt}${digest.hex()}"


def is_hashed(stored: str) -> bool:
    return isinstance(stored, str) and stored.startswith("pbkdf2$")


def verify_password(raw: str, stored: str) -> bool:
    # Plaintext rows are converted at start-up, so anything unhashed is refused.
    if not is_hashed(stored) or raw is None:
        return False
    try:
        _, rounds, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", raw.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def needs_rehash(stored: str) -> bool:
    try:
        return int(stored.split("$")[1]) < PBKDF2_ROUNDS
    except (IndexError, ValueError, AttributeError):
        return True


PASSWORD_MIN_LENGTH = 10


def password_problems(raw: str, user: dict | None = None) -> list[str]:
    """Unmet password rules, as sentences. Empty means the password is acceptable.

    At least 10 characters, three of lower-case / upper-case / digit / symbol,
    and none of the person's name parts, employee ID or e-mail name.
    """
    raw = raw or ""
    problems = []
    if len(raw) < PASSWORD_MIN_LENGTH:
        problems.append(f"Use at least {PASSWORD_MIN_LENGTH} characters.")
    classes = sum(bool(re.search(p, raw)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"))
    if classes < 3:
        problems.append("Use three of: lower-case, upper-case, digit, symbol.")
    lowered = raw.lower()
    words = set()
    for value in ((user or {}).get("name"), (user or {}).get("employee_id"),
                  ((user or {}).get("email") or "").split("@")[0]):
        for part in re.split(r"[\s._\-@]+", str(value or "").lower()):
            if len(part) >= 3:
                words.add(part)
    if any(word in lowered for word in words):
        problems.append("Do not use your name, employee ID or e-mail name.")
    return problems


# --------------------------------------------------------------------------- #
# Settings used here (read straight from app_settings to avoid an import cycle)
# --------------------------------------------------------------------------- #
def auth_setting(connection, key: str, default: str) -> str:
    row = connection.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] not in (None, "") else default


def _utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def session_limits(connection, role: str) -> tuple[int | None, int]:
    """(idle minutes or None, absolute hours) for a role."""
    if role in DEVICE_ROLES:
        return None, int(auth_setting(connection, "device_session_hours", "24"))
    return (int(auth_setting(connection, "session_idle_minutes", "60")),
            int(auth_setting(connection, "session_hours", "12")))


def create_session(user: dict, two_factor: bool = False, connection=None) -> str:
    """Open a session for `user` and return the bearer token (shown once)."""
    own = connection is None
    connection = connection or get_connection()
    try:
        info = client_info()
        idle, hours = session_limits(connection, user["role"])
        now = _utc()
        token = secrets.token_urlsafe(32)
        connection.execute(
            """INSERT INTO user_sessions
               (token_hash,user_id,created_at,last_seen_at,expires_at,idle_minutes,ip_address,
                user_agent,device,two_factor)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (token_hash(token), user["id"], _iso(now), _iso(now), _iso(now + timedelta(hours=hours)), idle,
             info.get("ip"), info.get("ua"), describe_device(info.get("ua"), info.get("client")),
             int(bool(two_factor))))
        if own:
            connection.commit()
        return token
    finally:
        if own:
            connection.close()


def destroy_session(token: str | None, reason: str = "SIGNED_OUT"):
    if not token:
        return
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE user_sessions SET revoked_at=?, revoked_reason=? WHERE token_hash=? AND revoked_at IS NULL",
            (_iso(_utc()), reason, token_hash(token)))
        connection.commit()
    finally:
        connection.close()


def revoke_user_sessions(connection, user_id: int, reason: str, keep_session_id: int | None = None) -> int:
    """End every live session of a user (optionally except one). Caller commits."""
    return connection.execute(
        "UPDATE user_sessions SET revoked_at=?, revoked_reason=? "
        "WHERE user_id=? AND revoked_at IS NULL AND (? IS NULL OR id<>?)",
        (_iso(_utc()), reason, user_id, keep_session_id, keep_session_id)).rowcount


def session_alive(row, now=None) -> bool:
    now = now or _utc()
    if row["revoked_at"] or _parse(row["expires_at"]) <= now:
        return False
    if row["idle_minutes"] and now - _parse(row["last_seen_at"]) > timedelta(minutes=int(row["idle_minutes"])):
        return False
    return True


def _session_user(token: str | None):
    """(user row, session row) for a live session, or (None, None)."""
    if not token:
        return None, None
    connection = get_connection()
    try:
        session = connection.execute("SELECT * FROM user_sessions WHERE token_hash=?",
                                     (token_hash(token),)).fetchone()
        if not session:
            return None, None
        now = _utc()
        if not session_alive(session, now):
            if not session["revoked_at"]:
                connection.execute(
                    "UPDATE user_sessions SET revoked_at=?, revoked_reason='EXPIRED' WHERE id=?",
                    (_iso(now), session["id"]))
                connection.commit()
            return None, None
        user = connection.execute(
            """SELECT id, employee_id, name, role, active, email, email_verified, two_factor,
                      must_change_password FROM users WHERE id=?""", (session["user_id"],)).fetchone()
        if not user or not user["active"]:
            connection.execute(
                "UPDATE user_sessions SET revoked_at=?, revoked_reason='ACCOUNT_DISABLED' WHERE id=?",
                (_iso(now), session["id"]))
            connection.commit()
            return None, None
        # Write activity at most once a minute.
        if now - _parse(session["last_seen_at"]) >= timedelta(seconds=60):
            info = client_info()
            connection.execute(
                "UPDATE user_sessions SET last_seen_at=?, ip_address=COALESCE(?, ip_address) WHERE id=?",
                (_iso(now), info.get("ip"), session["id"]))
            connection.commit()
        return dict(user), dict(session)
    finally:
        connection.close()


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


def current_user(authorization: str | None = Header(default=None)) -> dict:
    """Resolve the caller from a Bearer token issued by /login."""
    token = _bearer(authorization)
    user, session = _session_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    path = client_info().get("path") or ""
    if user["must_change_password"] and path not in PASSWORD_CHANGE_PATHS:
        raise HTTPException(
            status_code=403,
            detail="Your password must be changed before you continue. Open My Account and set a new one.")
    resolved = dict(user)
    resolved["token"] = token
    resolved["session_id"] = session["id"]
    return resolved


def permissions_for(role: str) -> set[str]:
    return ROLE_PERMISSIONS.get(role, set())


def _deny(user: dict, permission: str):
    log_action(user, "ACCESS", "PERMISSION_DENIED",
               f"{user['employee_id']} was refused '{permission}' on {client_info().get('path') or '?'}.",
               status="FAILED", metadata={"permission": permission, "path": client_info().get("path")})
    raise HTTPException(status_code=403, detail="You do not have permission to perform this action.")


def requires(permission: str):
    """Dependency factory: `user = Depends(requires("dashboard"))`."""

    def dependency(user: dict = Depends(current_user)) -> dict:
        if permission not in permissions_for(user["role"]):
            _deny(user, permission)
        return user

    return dependency


def requires_role(*roles: str):
    allowed = set(roles)

    def dependency(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in allowed:
            _deny(user, "role:" + "/".join(sorted(allowed)))
        return user

    return dependency


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def utc_text(value):
    """Any stored time as 'YYYY-MM-DDTHH:MM:SSZ' for the client.

    The database holds three formats: '...Z' (UTC, sessions and sign-ins),
    'YYYY-MM-DD HH:MM:SS' (SQLite CURRENT_TIMESTAMP, UTC) and
    'YYYY-MM-DDTHH:MM:SS' (now_iso, server-local time).
    """
    if not value:
        return value
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return value
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc) if " " in str(value) else stamp.astimezone()
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def today_iso() -> str:
    return date.today().isoformat()


def log_action(user, module, action, description, target_type=None, target_id=None,
               status="SUCCESS", metadata=None, ip=None):
    connection = get_connection()
    try:
        connection.execute(
            """INSERT INTO audit_logs
               (timestamp,employee_id,role,module,action,target_type,target_id,description,status,
                metadata,ip_address)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now_iso(),
                (user or {}).get("employee_id"),
                (user or {}).get("role"),
                module, action, target_type,
                None if target_id is None else str(target_id),
                description, status,
                json.dumps(metadata or {}, ensure_ascii=False, default=str),
                ip or client_info().get("ip"),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def validate_date(value: str, field: str) -> str:
    """Accept YYYY-MM-DD or a full ISO timestamp; return the date portion."""
    if not value:
        raise HTTPException(status_code=400, detail=f"{field} is required.")
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field}. Use the YYYY-MM-DD format.")


def date_filters(year=None, month=None, day=None, column="f.flight_date"):
    conditions, params = [], []
    if year:
        conditions.append(f"substr({column},1,4)=?")
        params.append(str(year))
    if month:
        conditions.append(f"substr({column},6,2)=?")
        params.append(f"{int(month):02d}")
    if day:
        conditions.append(f"substr({column},9,2)=?")
        params.append(f"{int(day):02d}")
    return conditions, params


def rate_for(currency: str, when: str, connection) -> float:
    code = (currency or "NPR").strip().upper()
    if code == "NPR":
        return 1.0
    target = when[:10]
    row = connection.execute(
        """SELECT unit, rate_to_npr FROM exchange_rates
           WHERE currency_code=? AND active=1
             AND effective_from <= ?
             AND (effective_to IS NULL OR effective_to >= ?)
           ORDER BY effective_from DESC, id DESC LIMIT 1""",
        (code, target, target),
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=400,
            detail=f"No exchange rate is configured for {code} on {target}.",
        )
    unit = float(row["unit"] or 1) or 1
    # Rates are quoted per unit (INR per 100, JPY per 10), so normalise to one.
    return float(row["rate_to_npr"]) / unit


def trip_root(connection, flight_id: int) -> int:
    """The leg that owns a trip's stock.

    A round trip is loaded once, for both directions, so the trolley belongs to
    the outbound leg and the return leg draws from the same rows. Everything
    that touches stock — loading, selling, the leftover count — resolves through
    here first.
    """
    row = connection.execute(
        "SELECT id, direction, linked_flight_id FROM flights WHERE id=?", (flight_id,)
    ).fetchone()
    if not row:
        return flight_id
    if row["direction"] == "RETURN" and row["linked_flight_id"]:
        return int(row["linked_flight_id"])
    return int(row["id"])


def trip_legs(connection, flight_id: int) -> list[int]:
    """Every leg sharing one trip's stock, outbound first."""
    root = trip_root(connection, flight_id)
    rows = connection.execute(
        "SELECT id FROM flights WHERE id=? OR linked_flight_id=? ORDER BY direction ASC",
        (root, root),
    ).fetchall()
    return [int(r["id"]) for r in rows]
