"""Admin: login accounts plus the roster of crew people used to build groups."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from crew_seed import CREW_POSITIONS
from database import ROLES, get_connection
from security import current_user, hash_password, log_action, now_iso, requires

router = APIRouter(tags=["admin"])


class UserCreate(BaseModel):
    employee_id: str
    name: str
    password: str = Field(min_length=4)
    role: str


class UserUpdate(BaseModel):
    name: str
    role: str
    active: bool = True
    password: str | None = None


class CrewMemberIn(BaseModel):
    crew_code: str
    name: str
    position: str = "Cabin Crew"
    active: bool = True


# ----------------------------- login accounts ----------------------------- #
@router.get("/users")
def list_users(user: dict = Depends(requires("users"))):
    connection = get_connection()
    try:
        rows = connection.execute(
            """SELECT id, employee_id, name, role, active, created_at,
                      failed_attempts, locked_at
               FROM users ORDER BY role, employee_id"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        connection.close()


@router.post("/users")
def create_user(data: UserCreate, user: dict = Depends(requires("users"))):
    if data.role not in ROLES:
        raise HTTPException(status_code=400, detail="Unknown role.")
    employee_id = data.employee_id.strip().upper()
    connection = get_connection()
    try:
        connection.execute(
            "INSERT INTO users(employee_id,name,password,role,active,created_at) VALUES (?,?,?,?,1,?)",
            (employee_id, data.name.strip(), hash_password(data.password), data.role, now_iso()),
        )
        connection.commit()
        row = connection.execute(
            "SELECT id, employee_id, name, role, active FROM users WHERE employee_id=?",
            (employee_id,),
        ).fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="That Employee ID already exists.")
    finally:
        connection.close()
    log_action(user, "USER MANAGEMENT", "CREATE_USER",
               f"Created account {employee_id} with role {data.role}.", "user", row["id"])
    return dict(row)


@router.put("/users/{user_id}")
def update_user(user_id: int, data: UserUpdate, user: dict = Depends(requires("users"))):
    if data.role not in ROLES:
        raise HTTPException(status_code=400, detail="Unknown role.")
    connection = get_connection()
    try:
        existing = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="User not found.")
        if existing["role"] == "admin" and (data.role != "admin" or not data.active):
            remaining = connection.execute(
                "SELECT COUNT(*) c FROM users WHERE role='admin' AND active=1 AND id<>?", (user_id,)
            ).fetchone()["c"]
            if remaining == 0:
                raise HTTPException(status_code=400, detail="At least one active administrator is required.")
        if data.password:
            connection.execute(
                """UPDATE users SET name=?, role=?, active=?, password=?,
                       failed_attempts=0, locked_at=NULL
                   WHERE id=?""",
                (data.name.strip(), data.role, int(data.active), hash_password(data.password), user_id),
            )
        else:
            connection.execute(
                "UPDATE users SET name=?, role=?, active=? WHERE id=?",
                (data.name.strip(), data.role, int(data.active), user_id),
            )
        connection.commit()
        row = connection.execute(
            "SELECT id, employee_id, name, role, active FROM users WHERE id=?", (user_id,)
        ).fetchone()
    finally:
        connection.close()
    log_action(user, "USER MANAGEMENT", "UPDATE_USER",
               f"Updated account {row['employee_id']}.", "user", user_id)
    return dict(row)


@router.post("/users/{user_id}/unlock")
def unlock_user(user_id: int, user: dict = Depends(requires("users"))):
    """Clear a lock caused by repeated wrong passwords."""
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT id, employee_id, name, role, locked_at, failed_attempts FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found.")
        if not row["locked_at"] and not int(row["failed_attempts"] or 0):
            raise HTTPException(status_code=400, detail="This account is not locked.")
        connection.execute(
            "UPDATE users SET failed_attempts=0, locked_at=NULL WHERE id=?", (user_id,)
        )
        connection.commit()
        updated = connection.execute(
            """SELECT id, employee_id, name, role, active, failed_attempts, locked_at
               FROM users WHERE id=?""",
            (user_id,),
        ).fetchone()
    finally:
        connection.close()

    log_action(user, "USER MANAGEMENT", "UNLOCK_USER",
               f"Unlocked {row['employee_id']} after repeated incorrect passwords.",
               "user", user_id)
    return dict(updated)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, user: dict = Depends(requires("users"))):
    connection = get_connection()
    try:
        existing = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="User not found.")
        if existing["employee_id"] == user["employee_id"]:
            raise HTTPException(status_code=400, detail="You cannot delete your own account.")
        if existing["role"] == "admin":
            remaining = connection.execute(
                "SELECT COUNT(*) c FROM users WHERE role='admin' AND active=1 AND id<>?", (user_id,)
            ).fetchone()["c"]
            if remaining == 0:
                raise HTTPException(status_code=400, detail="At least one active administrator is required.")
        # Deactivate rather than delete so historical sales keep a valid author.
        connection.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))
        connection.commit()
    finally:
        connection.close()
    log_action(user, "USER MANAGEMENT", "DEACTIVATE_USER",
               f"Deactivated account {existing['employee_id']}.", "user", user_id)
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
            connection.execute("DELETE FROM crew_members WHERE id=?", (member_id,))
            message = "Crew member removed."
        connection.commit()
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
