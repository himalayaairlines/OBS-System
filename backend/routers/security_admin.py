"""Administrator dashboard (sign-in analytics) and the security audit log.

Both read the existing audit_logs table plus users and user_sessions; nothing
here changes data. Codes, passwords and tokens are never written to the audit
log, so they cannot appear here.
"""

from datetime import date, datetime, timedelta, timezone
import json

from fastapi import APIRouter, Depends, Query

from database import ROLES, get_connection
from paging import MAX_PAGE_SIZE
from routers.admin import STATUSES, _user_rows
from security import requires, session_alive, utc_text

router = APIRouter(prefix="/security", tags=["security"])

SECURITY_MODULES = ("AUTHENTICATION", "USER MANAGEMENT", "ACCESS", "SMTP", "PROFILE")


def _utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _row(r) -> dict:
    meta = {}
    try:
        meta = json.loads(r["metadata"] or "{}")
    except (TypeError, ValueError):
        pass
    by_actor = r["module"] != "AUTHENTICATION"
    account = meta.pop("account", None) or (None if by_actor else r["employee_id"])
    return {"id": r["id"], "timestamp": utc_text(r["timestamp"]), "event": r["action"], "module": r["module"],
            "status": r["status"], "account": account, "by": r["employee_id"] if by_actor else None,
            "role": r["role"], "ip_address": r["ip_address"], "description": r["description"],
            "details": {k: v for k, v in meta.items() if v not in (None, "", [], {})}}


def _where(event, outcome, q, since, until, module=None, scope="all"):
    conditions: list = []
    params: list = []
    if module:
        conditions.append("module=?")
        params.append(module)
    elif scope == "security":
        conditions.append(f"module IN ({','.join('?' * len(SECURITY_MODULES))})")
        params.extend(SECURITY_MODULES)
    if event:
        conditions.append("action=?")
        params.append(event)
    if outcome:
        conditions.append("status=?")
        params.append(outcome)
    if q:
        like = f"%{q.strip().lower()}%"
        conditions.append("(lower(COALESCE(employee_id,'')) LIKE ? OR lower(COALESCE(ip_address,'')) LIKE ? "
                          "OR lower(description) LIKE ? OR lower(COALESCE(metadata,'')) LIKE ?)")
        params.extend([like] * 4)
    if since:
        conditions.append("substr(timestamp,1,10) >= ?")
        params.append(since)
    if until:
        conditions.append("substr(timestamp,1,10) <= ?")
        params.append(until)
    return " AND ".join(conditions) or "1=1", params


@router.get("/audit")
def audit_log(event: str | None = Query(None, max_length=60),
              outcome: str | None = Query(None, pattern="^(SUCCESS|FAILED)$"),
              q: str | None = Query(None, max_length=100),
              date_from: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
              date_to: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
              module: str | None = Query(None, max_length=40),
              scope: str = Query("all", pattern="^(all|security)$"),
              page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
              user: dict = Depends(requires("security_view"))):
    """Every recorded action: sign-ins and account changes, and also sales,
    stock returns, products, orders, approvals and corrections."""
    where, params = _where(event, outcome, q, date_from, date_to, module, scope)
    connection = get_connection()
    try:
        total = connection.execute(f"SELECT COUNT(*) c FROM audit_logs WHERE {where}", params).fetchone()["c"]
        pages = max(1, -(-total // page_size))
        page = min(page, pages)
        rows = connection.execute(
            f"SELECT * FROM audit_logs WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size)).fetchall()
        events = [r["action"] for r in connection.execute(
            "SELECT DISTINCT action FROM audit_logs WHERE (?='' OR module=?) ORDER BY action",
            (module or "", module or "")).fetchall()]
        modules = [r["module"] for r in connection.execute(
            "SELECT DISTINCT module FROM audit_logs WHERE module IS NOT NULL ORDER BY module").fetchall()]
    finally:
        connection.close()
    return {"items": [_row(r) for r in rows], "total": total, "page": page, "page_size": page_size,
            "pages": pages, "has_prev": page > 1, "has_next": page < pages, "events": events,
            "modules": modules}


@router.get("/overview")
def overview(days: int = Query(14, ge=7, le=90), user: dict = Depends(requires("security_view"))):
    connection = get_connection()
    try:
        users = _user_rows(connection)
        now = _utc()
        live = [s for s in connection.execute("SELECT * FROM user_sessions WHERE revoked_at IS NULL").fetchall()
                if session_alive(s, now)]
        start = (date.today() - timedelta(days=days - 1)).isoformat()
        daily = {}
        for r in connection.execute(
                """SELECT substr(timestamp,1,10) d, action, status, COUNT(*) c FROM audit_logs
                   WHERE module='AUTHENTICATION' AND substr(timestamp,1,10) >= ?
                     AND action IN ('LOGIN_SUCCESS','LOGIN_FAILED','LOGIN_BLOCKED','ACCOUNT_LOCKED','OTP_FAILED')
                   GROUP BY d, action, status""", (start,)).fetchall():
            entry = daily.setdefault(r["d"], {"success": 0, "failed": 0})
            entry["success" if r["action"] == "LOGIN_SUCCESS" else "failed"] += r["c"]
        trend = []
        for offset in range(days):
            day = (date.fromisoformat(start) + timedelta(days=offset)).isoformat()
            trend.append({"date": day, **daily.get(day, {"success": 0, "failed": 0})})
        since_24h = (datetime.now() - timedelta(hours=24)).replace(microsecond=0).isoformat()
        since_7d = (datetime.now() - timedelta(days=7)).replace(microsecond=0).isoformat()
        signed_24h = connection.execute(
            "SELECT COUNT(DISTINCT employee_id) c FROM audit_logs WHERE action='LOGIN_SUCCESS' AND timestamp>=?",
            (since_24h,)).fetchone()["c"]
        signed_7d = connection.execute(
            "SELECT COUNT(DISTINCT employee_id) c FROM audit_logs WHERE action='LOGIN_SUCCESS' AND timestamp>=?",
            (since_7d,)).fetchone()["c"]
        failed_24h = connection.execute(
            """SELECT COUNT(*) c FROM audit_logs WHERE module='AUTHENTICATION' AND status='FAILED'
               AND action IN ('LOGIN_FAILED','LOGIN_BLOCKED','ACCOUNT_LOCKED','OTP_FAILED') AND timestamp>=?""",
            (since_24h,)).fetchone()["c"]
        denied_24h = connection.execute(
            "SELECT COUNT(*) c FROM audit_logs WHERE action='PERMISSION_DENIED' AND timestamp>=?",
            (since_24h,)).fetchone()["c"]
        top_ips = [dict(r) for r in connection.execute(
            """SELECT ip_address, COUNT(*) failures FROM audit_logs
               WHERE module='AUTHENTICATION' AND status='FAILED' AND timestamp>=? AND ip_address IS NOT NULL
               GROUP BY ip_address ORDER BY failures DESC LIMIT 5""", (since_7d,)).fetchall()]
        recent = [_row(r) for r in connection.execute(
            f"""SELECT * FROM audit_logs WHERE module IN ({','.join('?' * len(SECURITY_MODULES))})
                ORDER BY id DESC LIMIT 4""", SECURITY_MODULES).fetchall()]
    finally:
        connection.close()
    active_users = [u for u in users if u["status"] != "DISABLED"]
    by_role = {}
    for u in active_users:
        by_role[u["role"]] = by_role.get(u["role"], 0) + 1
    devices = {}
    for s in live:
        devices[s["device"] or "Unknown"] = devices.get(s["device"] or "Unknown", 0) + 1
    with_email = [u for u in active_users if u["email"]]
    return {
        "users": {
            "total": len(users), "by_status": {s: sum(1 for u in users if u["status"] == s) for s in STATUSES},
            "by_role": [{"role": r, "count": by_role.get(r, 0)} for r in ROLES if by_role.get(r)],
            "two_factor": sum(1 for u in active_users if u["two_factor"]),
            "with_email": len(with_email),
            "verified_email": sum(1 for u in with_email if u["email_verified"]),
            "must_change_password": sum(1 for u in active_users if u["must_change_password"]),
            "demo_accounts": sum(1 for u in active_users if u["is_demo"]),
            "never_signed_in": sum(1 for u in active_users if not u["last_login_at"]),
        },
        "sessions": {"active": len(live), "users_online": len({s["user_id"] for s in live}),
                     "by_device": [{"device": k, "count": v} for k, v in sorted(devices.items(), key=lambda x: -x[1])]},
        "sign_ins": {"users_24h": signed_24h, "users_7d": signed_7d, "failed_24h": failed_24h,
                     "denied_24h": denied_24h, "trend": trend},
        "top_failing_ips": top_ips,
        "recent": recent,
    }


# --------------------------------------------------------------------------- #
# Sign-in settings
# --------------------------------------------------------------------------- #
from fastapi import HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from security import auth_setting, log_action  # noqa: E402

SETTING_KEYS = ("session_idle_minutes", "session_hours", "device_session_hours", "lockout_attempts",
                "lockout_minutes", "require_strong_passwords", "app_url")


class SecuritySettingsIn(BaseModel):
    session_idle_minutes: int = Field(ge=5, le=24 * 60)
    session_hours: int = Field(ge=1, le=72)
    device_session_hours: int = Field(ge=1, le=168)
    lockout_attempts: int = Field(ge=3, le=20)
    lockout_minutes: int = Field(ge=1, le=24 * 60)
    require_strong_passwords: bool = True
    app_url: str = Field(default="", max_length=300)


def _settings(connection):
    import os
    values = {k: auth_setting(connection, k, "") for k in SETTING_KEYS}
    return {
        "session_idle_minutes": int(values["session_idle_minutes"] or 60),
        "session_hours": int(values["session_hours"] or 12),
        "device_session_hours": int(values["device_session_hours"] or 24),
        "lockout_attempts": int(values["lockout_attempts"] or 5),
        "lockout_minutes": int(values["lockout_minutes"] or 15),
        "require_strong_passwords": values["require_strong_passwords"] != "0",
        "app_url": values["app_url"],
        "app_url_from_environment": os.getenv("OBS_APP_URL", "").strip() or None,
    }


@router.get("/settings")
def get_security_settings(user: dict = Depends(requires("users"))):
    connection = get_connection()
    try:
        return _settings(connection)
    finally:
        connection.close()


@router.put("/settings")
def put_security_settings(data: SecuritySettingsIn, user: dict = Depends(requires("users"))):
    url = data.app_url.strip().rstrip("/")
    if url and not (url.startswith("https://") or url.startswith("http://")):
        raise HTTPException(status_code=400, detail="The application address must start with https:// or http://.")
    values = data.model_dump()
    values["app_url"] = url
    values["require_strong_passwords"] = "1" if data.require_strong_passwords else "0"
    connection = get_connection()
    try:
        before = _settings(connection)
        for key, value in values.items():
            connection.execute(
                "INSERT INTO app_settings(key,value,updated_by) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by",
                (key, str(value), user["employee_id"]))
        connection.commit()
        after = _settings(connection)
    finally:
        connection.close()
    changed = {k: {"from": before[k], "to": after[k]} for k in SETTING_KEYS if before[k] != after[k]}
    log_action(user, "USER MANAGEMENT", "UPDATE_SECURITY_SETTINGS",
               f"Sign-in settings changed: {', '.join(changed) or 'no changes'}.", metadata=changed)
    return after
