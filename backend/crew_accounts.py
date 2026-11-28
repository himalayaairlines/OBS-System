"""A login for every cabin crew member (7.18).

Each person on the crew roster signs in on their own phone. The user name is
their crew code and the first password is DEFAULT_CREW_PASSWORD; it must be
changed at the first sign-in (must_change_password), so the shared default
never stays in use.

The login follows the roster: a crew member added, renamed or deactivated by
the administrator gets the same change on their account. An account the
administrator created by hand under the same code for another role is left
alone.
"""

from datetime import datetime, timezone

from database import DEFAULT_CREW_PASSWORD


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _columns(connection, table):
    return {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}


def sync_crew_logins(connection, member_id: int | None = None) -> dict:
    """Create or update the crew logins. Commits. Returns counts."""
    from security import hash_password

    if "crew_member_id" not in _columns(connection, "users"):
        connection.execute("ALTER TABLE users ADD COLUMN crew_member_id INTEGER")
    sql = "SELECT id, crew_code, name, active FROM crew_members"
    params = ()
    if member_id is not None:
        sql += " WHERE id=?"
        params = (member_id,)
    members = connection.execute(sql, params).fetchall()
    created = updated = 0
    default_hash = None
    has_must_change = "must_change_password" in _columns(connection, "users")
    for m in members:
        code = str(m["crew_code"]).strip().upper()
        linked = connection.execute("SELECT * FROM users WHERE crew_member_id=?", (m["id"],)).fetchone()
        if linked:
            changes = {}
            if linked["employee_id"] != code and not connection.execute(
                    "SELECT 1 FROM users WHERE employee_id=? AND id<>?", (code, linked["id"])).fetchone():
                changes["employee_id"] = code
            if linked["name"] != m["name"]:
                changes["name"] = m["name"]
            if bool(linked["active"]) != bool(m["active"]):
                changes["active"] = int(m["active"])
            if changes:
                sets = ", ".join(f"{k}=?" for k in changes)
                connection.execute(f"UPDATE users SET {sets} WHERE id=?", (*changes.values(), linked["id"]))
                if changes.get("active") == 0:
                    connection.execute(
                        "UPDATE user_sessions SET revoked_at=?, revoked_reason='ACCOUNT_DISABLED' "
                        "WHERE user_id=? AND revoked_at IS NULL", (_now(), linked["id"]))
                updated += 1
            continue
        existing = connection.execute("SELECT * FROM users WHERE employee_id=?", (code,)).fetchone()
        if existing:
            # Same code already a crew login: link it. Another role: leave it.
            if existing["role"] in ("crew", "crew_supervisor") and existing["crew_member_id"] is None:
                connection.execute("UPDATE users SET crew_member_id=?, name=?, active=? WHERE id=?",
                                   (m["id"], m["name"], int(m["active"]), existing["id"]))
                updated += 1
            continue
        if default_hash is None:
            # One hash for the batch: everyone starts on the same published
            # default and must replace it, so per-account salts add nothing.
            default_hash = hash_password(DEFAULT_CREW_PASSWORD)
        if has_must_change:
            connection.execute(
                "INSERT INTO users(employee_id,name,password,role,active,crew_member_id,must_change_password) "
                "VALUES (?,?,?,?,?,?,1)", (code, m["name"], default_hash, "crew", int(m["active"]), m["id"]))
        else:
            connection.execute(
                "INSERT INTO users(employee_id,name,password,role,active,crew_member_id) VALUES (?,?,?,?,?,?)",
                (code, m["name"], default_hash, "crew", int(m["active"]), m["id"]))
        created += 1
    connection.commit()
    return {"created": created, "updated": updated}


def crew_member_for_user(connection, user: dict):
    """The roster entry behind a crew login, or None."""
    row = connection.execute("SELECT crew_member_id FROM users WHERE employee_id=?", (user["employee_id"],)).fetchone()
    if row and row["crew_member_id"]:
        m = connection.execute("SELECT * FROM crew_members WHERE id=?", (row["crew_member_id"],)).fetchone()
        if m:
            return m
    return connection.execute("SELECT * FROM crew_members WHERE UPPER(crew_code)=?",
                              (str(user["employee_id"]).upper(),)).fetchone()
