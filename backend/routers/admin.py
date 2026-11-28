"""Admin: login accounts (Users & Access) plus the roster of crew people used to build groups.

Accounts are normally created by invitation: the administrator enters the
person's name, e-mail and role, and the person chooses a password from a
one-time e-mailed link. Shared OBS iPad accounts, which have no mailbox, can
still be created with a password set directly.
"""

from datetime import datetime, timedelta, timezone
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import account_mail
from auth_schema import UNUSABLE_PASSWORD
from crew_seed import CREW_POSITIONS
from database import ROLES, get_connection
from comms_core import EMAIL_RE
from paging import MAX_PAGE_SIZE
from security import (
    auth_setting, current_user, hash_password, log_action, now_iso, password_problems,
    revoke_user_sessions, session_alive, requires, utc_text,
)

router = APIRouter(tags=["admin"])

STATUSES = ("ACTIVE", "INVITED", "UNVERIFIED", "LOCKED", "DISABLED")


def _utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value):
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class UserCreate(BaseModel):
    """A shared device account without a mailbox: the password is set here."""
    employee_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(max_length=256)
    role: str
    email: str | None = Field(default=None, max_length=254)


class UserInvite(BaseModel):
    employee_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    role: str
    two_factor: bool = False


class UserUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str
    active: bool = True
    password: str | None = Field(default=None, max_length=256)
    # Omitted keeps the stored address; an empty string clears it.
    email: str | None = Field(default=None, max_length=254)
    two_factor: bool | None = None
    # With a new password: make the owner choose their own at next sign-in.
    # Omitted: yes for personal accounts, no for shared crew devices.
    require_change: bool | None = None


# Shared cabin devices: the administrator sets and keeps their password.
# 7.18: a crew member's own login ("crew") is personal, so it is not one.
SHARED_DEVICE_ROLES = ("crew_supervisor",)


class TwoFactorIn(BaseModel):
    enabled: bool


class CrewMemberIn(BaseModel):
    crew_code: str
    name: str
    position: str = "Cabin Crew"
    active: bool = True


class LockIn(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


def _clean_email(value: str | None) -> str | None:
    value = (value or "").strip().lower()
    if not value:
        return None
    if not EMAIL_RE.match(value):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    return value


def _email_free(connection, email, user_id=None):
    if email and connection.execute("SELECT 1 FROM users WHERE lower(email)=? AND id<>?",
                                    (email, user_id or -1)).fetchone():
        raise HTTPException(status_code=400, detail="Another account already uses that e-mail address.")


def account_status(row, lock_minutes: int, now=None) -> str:
    now = now or _utc()
    if not row["active"] and row["password"] != UNUSABLE_PASSWORD:
        return "DISABLED"
    if row["password"] == UNUSABLE_PASSWORD:
        return "INVITED" if row["active"] else "DISABLED"
    if row["locked_at"]:
        if row["lock_reason"] == "ADMIN":
            return "LOCKED"
        locked = _parse(row["locked_at"])
        if locked and now - locked < timedelta(minutes=lock_minutes):
            return "LOCKED"
    if row["email"] and not row["email_verified"]:
        return "UNVERIFIED"
    return "ACTIVE"


def _user_rows(connection):
    lock_minutes = int(auth_setting(connection, "lockout_minutes", "15"))
    now = _utc()
    sessions: dict[int, int] = {}
    for s in connection.execute("SELECT * FROM user_sessions WHERE revoked_at IS NULL").fetchall():
        if session_alive(s, now):
            sessions[s["user_id"]] = sessions.get(s["user_id"], 0) + 1
    out = []
    for r in connection.execute("SELECT * FROM users ORDER BY role, employee_id").fetchall():
        out.append({
            "id": r["id"], "employee_id": r["employee_id"], "name": r["name"], "role": r["role"],
            "email": r["email"], "active": bool(r["active"]), "created_at": utc_text(r["created_at"]),
            "status": account_status(r, lock_minutes, now), "email_verified": bool(r["email_verified"]),
            "two_factor": bool(r["two_factor"]), "last_login_at": utc_text(r["last_login_at"]),
            "last_login_ip": r["last_login_ip"], "sessions": sessions.get(r["id"], 0),
            "failed_attempts": r["failed_attempts"], "locked_at": utc_text(r["locked_at"]), "lock_reason": r["lock_reason"],
            "must_change_password": bool(r["must_change_password"]), "is_demo": bool(r["is_demo"]),
            "invited_at": utc_text(r["invited_at"]), "password_changed_at": utc_text(r["password_changed_at"]),
        })
    return out


def _one(connection, user_id):
    row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")
    return dict(row)


def _view(user_id):
    connection = get_connection()
    try:
        return next(u for u in _user_rows(connection) if u["id"] == user_id)
    finally:
        connection.close()


def _last_admin_guard(connection, existing, new_role=None, new_active=True):
    if existing["role"] == "admin" and (new_role not in (None, "admin") or not new_active):
        remaining = connection.execute(
            "SELECT COUNT(*) c FROM users WHERE role='admin' AND active=1 AND id<>? AND password<>?",
            (existing["id"], UNUSABLE_PASSWORD)).fetchone()["c"]
        if remaining == 0:
            raise HTTPException(status_code=400, detail="At least one active administrator is required.")


def _check_two_factor(connection, email):
    if not email:
        raise HTTPException(status_code=400, detail="Two-factor sign-in needs an e-mail address on the account.")
    if not account_mail.smtp_ready(connection):
        raise HTTPException(status_code=400, detail="Switch on outgoing e-mail (SMTP settings) before enabling "
                                                    "two-factor sign-in, or the person could not sign in.")


# ----------------------------- login accounts ----------------------------- #
@router.get("/users")
def list_users(q: str | None = Query(None, max_length=100), role: str | None = None,
               status: str | None = Query(None, pattern="^(ACTIVE|INVITED|UNVERIFIED|LOCKED|DISABLED)$"),
               page: int | None = Query(None, ge=1), page_size: int = Query(25, ge=1, le=MAX_PAGE_SIZE),
               user: dict = Depends(requires("users"))):
    connection = get_connection()
    try:
        rows = _user_rows(connection)
    finally:
        connection.close()
    needle = (q or "").strip().lower()
    filtered = [r for r in rows
                if (not needle or needle in r["name"].lower() or needle in r["employee_id"].lower()
                    or needle in (r["email"] or ""))
                and (not role or r["role"] == role) and (not status or r["status"] == status)]
    if not page:
        return filtered
    total = len(filtered)
    pages = max(1, -(-total // page_size))
    page = min(page, pages)
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    return {"items": filtered[(page - 1) * page_size: page * page_size], "total": total, "page": page,
            "page_size": page_size, "pages": pages, "has_prev": page > 1, "has_next": page < pages,
            "counts": counts}


@router.post("/users/invite")
def invite_user(data: UserInvite, user: dict = Depends(requires("users"))):
    from routers.auth import issue_link
    if data.role not in ROLES:
        raise HTTPException(status_code=400, detail="Unknown role.")
    employee_id = data.employee_id.strip().upper()
    email = _clean_email(data.email)
    connection = get_connection()
    try:
        _email_free(connection, email)
        if data.two_factor:
            _check_two_factor(connection, email)
        try:
            new_id = connection.execute(
                """INSERT INTO users(employee_id,name,password,role,active,created_at,email,two_factor,
                                     invited_at,invited_by,password_changed_at)
                   VALUES (?,?,?,?,1,?,?,?,?,?,NULL)""",
                (employee_id, data.name.strip(), UNUSABLE_PASSWORD, data.role, now_iso(), email,
                 int(data.two_factor), _iso(_utc()), user["employee_id"])).lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="That Employee ID already exists.")
        job = issue_link(connection, _one(connection, new_id), "INVITE")
        connection.commit()
    finally:
        connection.close()
    status = account_mail.send_now(job)
    log_action(user, "USER MANAGEMENT", "INVITE_USER",
               f"Invited {employee_id} ({data.role}) at {email}; e-mail {status.lower()}.", "user", new_id,
               metadata={"account": employee_id, "delivery": status})
    out = _view(new_id)
    out["delivery"] = status
    return out


@router.post("/users")
def create_user(data: UserCreate, user: dict = Depends(requires("users"))):
    if data.role not in ROLES:
        raise HTTPException(status_code=400, detail="Unknown role.")
    employee_id = data.employee_id.strip().upper()
    email = _clean_email(data.email)
    problems = password_problems(data.password, {"name": data.name, "employee_id": employee_id, "email": email})
    if problems:
        raise HTTPException(status_code=400, detail=" ".join(problems))
    connection = get_connection()
    try:
        _email_free(connection, email)
        connection.execute(
            """INSERT INTO users(employee_id,name,password,role,active,created_at,email,password_changed_at)
               VALUES (?,?,?,?,1,?,?,?)""",
            (employee_id, data.name.strip(), hash_password(data.password), data.role, now_iso(), email,
             _iso(_utc())),
        )
        connection.commit()
        row = connection.execute("SELECT id FROM users WHERE employee_id=?", (employee_id,)).fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="That Employee ID already exists.")
    finally:
        connection.close()
    log_action(user, "USER MANAGEMENT", "CREATE_USER",
               f"Created account {employee_id} with role {data.role}.", "user", row["id"],
               metadata={"account": employee_id})
    return _view(row["id"])


@router.put("/users/{user_id}")
def update_user(user_id: int, data: UserUpdate, user: dict = Depends(requires("users"))):
    if data.role not in ROLES:
        raise HTTPException(status_code=400, detail="Unknown role.")
    connection = get_connection()
    try:
        existing = _one(connection, user_id)
        _last_admin_guard(connection, existing, data.role, data.active)
        if existing["id"] == user["id"] and (data.role != existing["role"] or not data.active):
            raise HTTPException(status_code=400, detail="You cannot change your own role or disable yourself.")
        changes = []
        email = existing["email"]
        if data.email is not None:
            email = _clean_email(data.email)
            if email != existing["email"]:
                _email_free(connection, email, user_id)
                connection.execute("UPDATE users SET email=?, email_verified=0 WHERE id=?", (email, user_id))
                changes.append("e-mail")
        if data.two_factor is not None and bool(data.two_factor) != bool(existing["two_factor"]):
            if data.two_factor:
                _check_two_factor(connection, email)
            connection.execute("UPDATE users SET two_factor=? WHERE id=?", (int(data.two_factor), user_id))
            changes.append("two-factor " + ("on" if data.two_factor else "off"))
        if data.password:
            problems = password_problems(data.password, {**existing, "email": email})
            if problems:
                raise HTTPException(status_code=400, detail=" ".join(problems))
            # A password set by someone else must be changed by its owner,
            # except on a shared crew device, whose password the admin keeps.
            must_change = (data.require_change if data.require_change is not None
                           else data.role not in SHARED_DEVICE_ROLES)
            connection.execute(
                """UPDATE users SET password=?, password_changed_at=?, must_change_password=?,
                       failed_attempts=0, locked_at=NULL, lock_reason=NULL WHERE id=?""",
                (hash_password(data.password), _iso(_utc()), int(must_change), user_id))
            revoke_user_sessions(connection, user_id, "PASSWORD_SET_BY_ADMIN")
            changes.append("password")
        if data.role != existing["role"]:
            changes.append(f"role {existing['role']} -> {data.role}")
        if bool(data.active) != bool(existing["active"]):
            changes.append("enabled" if data.active else "disabled")
            if not data.active:
                revoke_user_sessions(connection, user_id, "ACCOUNT_DISABLED")
        connection.execute("UPDATE users SET name=?, role=?, active=? WHERE id=?",
                           (data.name.strip(), data.role, int(data.active), user_id))
        connection.commit()
    finally:
        connection.close()
    log_action(user, "USER MANAGEMENT", "UPDATE_USER",
               f"Updated account {existing['employee_id']}: {', '.join(changes) or 'details'}.", "user", user_id,
               metadata={"account": existing["employee_id"], "changes": changes})
    return _view(user_id)


def _simple(user_id, user, action, fn):
    connection = get_connection()
    try:
        existing = _one(connection, user_id)
        detail = fn(connection, existing)
        connection.commit()
    finally:
        connection.close()
    log_action(user, "USER MANAGEMENT", action, f"{detail} ({existing['employee_id']})", "user", user_id,
               metadata={"account": existing["employee_id"]})
    return existing


@router.post("/users/{user_id}/unlock")
def unlock_user(user_id: int, user: dict = Depends(requires("users"))):
    def run(connection, existing):
        if not existing["locked_at"] and not int(existing["failed_attempts"] or 0):
            raise HTTPException(status_code=400, detail="This account is not locked.")
        connection.execute("UPDATE users SET failed_attempts=0, locked_at=NULL, lock_reason=NULL WHERE id=?",
                           (user_id,))
        return "Unlocked"
    _simple(user_id, user, "UNLOCK_USER", run)
    return _view(user_id)


@router.post("/users/{user_id}/lock")
def lock_user(user_id: int, data: LockIn, user: dict = Depends(requires("users"))):
    def run(connection, existing):
        if existing["id"] == user["id"]:
            raise HTTPException(status_code=400, detail="You cannot lock your own account.")
        _last_admin_guard(connection, existing, new_active=False)
        connection.execute("UPDATE users SET locked_at=?, lock_reason='ADMIN' WHERE id=?", (_iso(_utc()), user_id))
        revoke_user_sessions(connection, user_id, "LOCKED_BY_ADMIN")
        return "Locked" + (f": {data.reason}" if data.reason else "")
    _simple(user_id, user, "LOCK_USER", run)
    return _view(user_id)


@router.post("/users/{user_id}/two-factor")
def set_two_factor(user_id: int, data: TwoFactorIn, user: dict = Depends(requires("users"))):
    def run(connection, existing):
        if data.enabled:
            _check_two_factor(connection, existing["email"])
        connection.execute("UPDATE users SET two_factor=? WHERE id=?", (int(data.enabled), user_id))
        return "Two-factor sign-in " + ("enabled" if data.enabled else "disabled")
    _simple(user_id, user, "TWO_FACTOR_ENABLED" if data.enabled else "TWO_FACTOR_DISABLED", run)
    return _view(user_id)


@router.post("/users/{user_id}/revoke-sessions")
def revoke_sessions(user_id: int, user: dict = Depends(requires("users"))):
    def run(connection, existing):
        ended = revoke_user_sessions(connection, user_id, "REVOKED_BY_ADMIN",
                                     keep_session_id=user["session_id"] if existing["id"] == user["id"] else None)
        return f"Signed out {ended} session(s)"
    _simple(user_id, user, "SESSIONS_REVOKED", run)
    return _view(user_id)


def _send_link(user_id, user, kind):
    from routers.auth import issue_link
    connection = get_connection()
    try:
        existing = _one(connection, user_id)
        if kind == "RESET" and existing["password"] == UNUSABLE_PASSWORD:
            raise HTTPException(status_code=400, detail="This person has not accepted the invitation yet. "
                                                        "Resend the invitation instead.")
        if kind == "INVITE" and existing["password"] != UNUSABLE_PASSWORD:
            raise HTTPException(status_code=400, detail="This account already has a password.")
        if not existing["active"]:
            raise HTTPException(status_code=400, detail="Enable the account first.")
        job = issue_link(connection, existing, kind)
        if kind == "INVITE":
            connection.execute("UPDATE users SET invited_at=?, invited_by=? WHERE id=?",
                               (_iso(_utc()), user["employee_id"], user_id))
        connection.commit()
    finally:
        connection.close()
    status = account_mail.send_now(job)
    log_action(user, "USER MANAGEMENT", "SEND_RESET_LINK" if kind == "RESET" else "RESEND_INVITE",
               f"{'Reset link' if kind == 'RESET' else 'Invitation'} for {existing['employee_id']}: {status.lower()}.",
               "user", user_id, status="SUCCESS" if status == "ACCEPTED" else "FAILED",
               metadata={"account": existing["employee_id"], "delivery": status})
    if status != "ACCEPTED":
        raise HTTPException(status_code=502, detail=f"The e-mail could not be sent ({status.lower()}). "
                                                    "Check SMTP settings and the Delivery log.")
    return {"status": "sent", "detail": f"Sent to {existing['email']}."}


@router.post("/users/{user_id}/send-reset")
def send_reset(user_id: int, user: dict = Depends(requires("users"))):
    return _send_link(user_id, user, "RESET")


@router.post("/users/{user_id}/resend-invite")
def resend_invite(user_id: int, user: dict = Depends(requires("users"))):
    return _send_link(user_id, user, "INVITE")


@router.delete("/users/{user_id}")
def delete_user(user_id: int, user: dict = Depends(requires("users"))):
    connection = get_connection()
    try:
        existing = _one(connection, user_id)
        if existing["id"] == user["id"]:
            raise HTTPException(status_code=400, detail="You cannot delete your own account.")
        _last_admin_guard(connection, existing, new_active=False)
        # Deactivate rather than delete so historical sales keep a valid author.
        connection.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))
        revoke_user_sessions(connection, user_id, "ACCOUNT_DISABLED")
        connection.commit()
    finally:
        connection.close()
    log_action(user, "USER MANAGEMENT", "DEACTIVATE_USER",
               f"Deactivated account {existing['employee_id']}.", "user", user_id,
               metadata={"account": existing["employee_id"]})
    return {"status": "success"}


# ------------------------------ crew roster ------------------------------- #
@router.get("/crew-members")
def list_crew_members(include_inactive: bool = False, user: dict = Depends(requires("crew_members"))):
    return _crew_members(include_inactive)


@router.get("/crew-positions")
def crew_positions(user: dict = Depends(current_user)):
    return list(CREW_POSITIONS)


@router.post("/crew-members")
def create_crew_member(data: CrewMemberIn, user: dict = Depends(requires("crew_members"))):
    code = data.crew_code.strip().upper()
    connection = get_connection()
    try:
        connection.execute(
            "INSERT INTO crew_members(crew_code,name,position,active) VALUES (?,?,?,?)",
            (code, data.name.strip(), data.position.strip() or "Cabin Crew", int(data.active)),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM crew_members WHERE crew_code=?", (code,)).fetchone()
        # 7.18: their own login, user name = crew code, default password to change.
        from crew_accounts import sync_crew_logins
        sync_crew_logins(connection, row["id"])
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="That crew code already exists.")
    finally:
        connection.close()
    log_action(user, "CREW ROSTER", "CREATE_CREW", f"Added crew member {code} - {data.name}.",
               "crew_member", row["id"])
    return dict(row)


@router.put("/crew-members/{member_id}")
def update_crew_member(member_id: int, data: CrewMemberIn, user: dict = Depends(requires("crew_members"))):
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM crew_members WHERE id=?", (member_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Crew member not found.")
        connection.execute(
            "UPDATE crew_members SET crew_code=?, name=?, position=?, active=? WHERE id=?",
            (data.crew_code.strip().upper(), data.name.strip(),
             data.position.strip() or "Cabin Crew", int(data.active), member_id),
        )
        connection.commit()
        updated = connection.execute("SELECT * FROM crew_members WHERE id=?", (member_id,)).fetchone()
        from crew_accounts import sync_crew_logins
        sync_crew_logins(connection, member_id)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="That crew code already exists.")
    finally:
        connection.close()
    log_action(user, "CREW ROSTER", "UPDATE_CREW", f"Updated crew member {data.crew_code}.",
               "crew_member", member_id)
    return dict(updated)


@router.delete("/crew-members/{member_id}")
def delete_crew_member(member_id: int, user: dict = Depends(requires("crew_members"))):
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM crew_members WHERE id=?", (member_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Crew member not found.")
        in_group = connection.execute(
            "SELECT COUNT(*) c FROM duty_group_members WHERE crew_member_id=?", (member_id,)
        ).fetchone()["c"]
        if in_group:
            # Keep commission history intact; just take them off the active roster.
            connection.execute("UPDATE crew_members SET active=0 WHERE id=?", (member_id,))
            message = "Crew member is in a group and has been deactivated instead of deleted."
        else:
            # The login goes with them: it is disabled, not deleted, so the
            # audit trail keeps a name behind every action it took.
            connection.execute("UPDATE users SET active=0 WHERE crew_member_id=?", (member_id,))
            connection.execute("UPDATE users SET crew_member_id=NULL WHERE crew_member_id=?", (member_id,))
            connection.execute("DELETE FROM crew_members WHERE id=?", (member_id,))
            message = "Crew member removed."
        connection.commit()
        if in_group:
            from crew_accounts import sync_crew_logins
            sync_crew_logins(connection, member_id)
    finally:
        connection.close()
    log_action(user, "CREW ROSTER", "REMOVE_CREW", f"{message} ({row['crew_code']})",
               "crew_member", member_id)
    return {"status": "success", "detail": message}


def _crew_members(include_inactive: bool):
    connection = get_connection()
    try:
        sql = "SELECT * FROM crew_members"
        if not include_inactive:
            sql += " WHERE active=1"
        # Purser first, then Senior Cabin Crew, then Cabin Crew.
        sql += (" ORDER BY CASE position WHEN 'Purser' THEN 0"
                " WHEN 'Senior Cabin Crew' THEN 1 ELSE 2 END, name")
        return [dict(r) for r in connection.execute(sql).fetchall()]
    finally:
        connection.close()
