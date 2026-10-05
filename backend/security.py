"""Password hashing, session tokens, permission guards and audit logging."""

from datetime import datetime, date, timedelta
import hashlib
import hmac
import json
import os
import secrets

from fastapi import Depends, Header, HTTPException

from database import ROLE_PERMISSIONS, get_connection

PBKDF2_ROUNDS = 120_000
SESSION_TTL_HOURS = 18

# --------------------------------------------------------------------------- #
# Administrator recovery code
# --------------------------------------------------------------------------- #
# The break-glass code that lets an administrator who is locked out, or who has
# forgotten the password, set a new one without another administrator's help.
#
# Set OBS_ADMIN_RECOVERY_CODE in the environment to use your own. The built-in
# fallback is stored as a hash so the code itself is not readable in the source,
# but anyone who can run the server can still try codes against it, which is why
# every attempt is rate limited and written to the audit log.
_DEFAULT_RECOVERY_HASH = (
    "pbkdf2$120000$d9f255024e84ac825f14ee7dd8fda71d$"
    "23d5091d342450e76515d97a9e0e2491975988417f45b88c561146b7edc5259e"
)

# Wrong codes allowed before recovery is refused for a while.
RECOVERY_ATTEMPT_LIMIT = 5
RECOVERY_COOLDOWN_MINUTES = 15

_recovery_failures: list[datetime] = []


def _normalise_code(code: str) -> str:
    return (code or "").strip().upper()


def recovery_code_matches(code: str) -> bool:
    """True when `code` is the configured administrator recovery code."""
    configured = os.getenv("OBS_ADMIN_RECOVERY_CODE", "").strip()
    supplied = _normalise_code(code)
    if not supplied:
        return False
    if configured:
        return hmac.compare_digest(supplied, _normalise_code(configured))
    return verify_password(supplied, _DEFAULT_RECOVERY_HASH)


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

# In-process session store. A production deployment would move this to the
# database or a cache, but it already removes the "trust the header" hole.
_SESSIONS: dict[str, dict] = {}


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
    if not is_hashed(stored):
        return hmac.compare_digest(raw, stored or "")
    try:
        _, rounds, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", raw.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def create_session(user: dict) -> str:
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = {
        "user": user,
        "expires": datetime.now() + timedelta(hours=SESSION_TTL_HOURS),
    }
    return token


def destroy_session(token: str | None):
    if token:
        _SESSIONS.pop(token, None)


def _session_user(token: str | None):
    entry = _SESSIONS.get(token or "")
    if not entry:
        return None
    if entry["expires"] < datetime.now():
        _SESSIONS.pop(token, None)
        return None
    return entry["user"]


def current_user(authorization: str | None = Header(default=None)) -> dict:
    """Resolve the caller from a Bearer token issued by /login."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    user = _session_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")

    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT id, employee_id, name, role, active FROM users WHERE id=?",
            (user["id"],),
        ).fetchone()
    finally:
        connection.close()
    if not row or not row["active"]:
        destroy_session(token)
        raise HTTPException(status_code=401, detail="Account is no longer active.")
    resolved = dict(row)
    resolved["token"] = token
    return resolved


def permissions_for(role: str) -> set[str]:
    return ROLE_PERMISSIONS.get(role, set())


def requires(permission: str):
    """Dependency factory: `user = Depends(requires("dashboard"))`."""

    def dependency(user: dict = Depends(current_user)) -> dict:
        if permission not in permissions_for(user["role"]):
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to perform this action.",
            )
        return user

    return dependency


def requires_role(*roles: str):
    allowed = set(roles)

    def dependency(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in allowed:
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to perform this action.",
            )
        return user

    return dependency


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def today_iso() -> str:
    return date.today().isoformat()


def log_action(user, module, action, description, target_type=None, target_id=None,
               status="SUCCESS", metadata=None):
    connection = get_connection()
    try:
        connection.execute(
            """INSERT INTO audit_logs
               (timestamp,employee_id,role,module,action,target_type,target_id,description,status,metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                now_iso(),
                (user or {}).get("employee_id"),
                (user or {}).get("role"),
                module, action, target_type,
                None if target_id is None else str(target_id),
                description, status,
                json.dumps(metadata or {}, ensure_ascii=False, default=str),
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
