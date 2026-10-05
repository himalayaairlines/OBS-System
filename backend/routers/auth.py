from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import LOGIN_ATTEMPT_LIMIT, get_connection
from security import (
    clear_recovery_failures, create_session, current_user,
    destroy_session, hash_password, log_action, now_iso, permissions_for,
    record_recovery_failure, recovery_code_matches, recovery_cooldown_remaining,
    verify_password,
)

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    employee_id: str
    password: str


class ChangePassword(BaseModel):
    old_password: str
    new_password: str = Field(min_length=4)
    confirm_password: str


class AdminRecovery(BaseModel):
    """Break-glass reset for an administrator who is locked out or has
    forgotten the password. It needs the recovery code, not the old password."""

    employee_id: str
    recovery_code: str
    new_password: str = Field(min_length=4)
    confirm_password: str


LOCKED_MESSAGE = (
    f"This account is locked after {LOGIN_ATTEMPT_LIMIT} incorrect passwords. "
    "An administrator must unlock it."
)


@router.post("/login")
def login(data: LoginRequest):
    """Sign in, counting consecutive failures and locking the account at the limit.

    The lock is deliberate friction: an account is a person's identity in the
    audit trail, so repeated guessing has to stop rather than run forever.
    """
    employee_id = data.employee_id.strip().upper()
    connection = get_connection()
    try:
        row = connection.execute(
            """SELECT id, employee_id, name, password, role, active,
                      failed_attempts, locked_at
               FROM users WHERE employee_id=?""",
            (employee_id,),
        ).fetchone()

        # An unknown ID is answered exactly like a wrong password, so the login
        # screen cannot be used to discover which accounts exist.
        if not row:
            log_action(
                {"employee_id": employee_id, "role": "unknown"},
                "AUTHENTICATION", "LOGIN_FAILED",
                f"Failed sign-in for unknown ID {employee_id}.", status="FAILED",
            )
            raise HTTPException(status_code=401, detail="Invalid Employee ID or password.")

        if row["locked_at"]:
            log_action(
                {"employee_id": employee_id, "role": row["role"]},
                "AUTHENTICATION", "LOGIN_BLOCKED",
                f"Sign-in attempted on the locked account {employee_id}.", "user", row["id"],
                status="FAILED",
            )
            raise HTTPException(status_code=403, detail=LOCKED_MESSAGE)

        if not verify_password(data.password, row["password"]):
            attempts = int(row["failed_attempts"] or 0) + 1
            locking = attempts >= LOGIN_ATTEMPT_LIMIT
            connection.execute(
                "UPDATE users SET failed_attempts=?, locked_at=? WHERE id=?",
                (attempts, now_iso() if locking else None, row["id"]),
            )
            connection.commit()
            log_action(
                {"employee_id": employee_id, "role": row["role"]},
                "AUTHENTICATION", "ACCOUNT_LOCKED" if locking else "LOGIN_FAILED",
                (f"{employee_id} locked after {attempts} incorrect passwords."
                 if locking else
                 f"Incorrect password for {employee_id} ({attempts} of {LOGIN_ATTEMPT_LIMIT})."),
                "user", row["id"], status="FAILED",
            )
            if locking:
                raise HTTPException(status_code=403, detail=LOCKED_MESSAGE)
            remaining = LOGIN_ATTEMPT_LIMIT - attempts
            raise HTTPException(
                status_code=401,
                detail=f"Invalid Employee ID or password. "
                       f"{remaining} attempt{'' if remaining == 1 else 's'} left before the "
                       f"account is locked.",
            )

        if not row["active"]:
            raise HTTPException(status_code=403, detail="This account has been deactivated.")

        if int(row["failed_attempts"] or 0):
            connection.execute(
                "UPDATE users SET failed_attempts=0, locked_at=NULL WHERE id=?", (row["id"],)
            )
            connection.commit()
    finally:
        connection.close()

    user = {"id": row["id"], "employee_id": row["employee_id"], "name": row["name"], "role": row["role"]}
    token = create_session(user)
    log_action(user, "AUTHENTICATION", "LOGIN", f"{user['employee_id']} logged in.", "user", user["id"])
    return {**user, "token": token, "permissions": sorted(permissions_for(user["role"]))}


@router.post("/admin-recovery")
def admin_recovery(data: AdminRecovery):
    """Set a new administrator password using the recovery code.

    This exists so a locked-out or forgotten administrator password does not
    strand the system, since only an administrator can unlock accounts. It
    applies to administrator accounts alone, every attempt is written to the
    audit log, and wrong codes are rate limited.
    """
    employee_id = data.employee_id.strip().upper()

    cooldown = recovery_cooldown_remaining()
    if cooldown:
        raise HTTPException(
            status_code=429,
            detail=f"Too many incorrect recovery codes. Try again in {cooldown} minute(s).",
        )

    if data.new_password != data.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirmation do not match.")

    if not recovery_code_matches(data.recovery_code):
        record_recovery_failure()
        log_action(
            {"employee_id": employee_id, "role": "unknown"},
            "AUTHENTICATION", "RECOVERY_FAILED",
            f"Incorrect recovery code used against {employee_id}.", status="FAILED",
        )
        raise HTTPException(status_code=401, detail="The recovery code is not correct.")

    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT id, employee_id, name, role FROM users WHERE employee_id=?",
            (employee_id,),
        ).fetchone()
        # The code is correct, so the caller is trusted enough to be told that
        # the ID is not an administrator account.
        if not row or row["role"] != "admin":
            log_action(
                {"employee_id": employee_id, "role": "unknown"},
                "AUTHENTICATION", "RECOVERY_REFUSED",
                f"Recovery attempted for {employee_id}, which is not an administrator account.",
                status="FAILED",
            )
            raise HTTPException(
                status_code=400,
                detail="Recovery only applies to an administrator account.",
            )

        connection.execute(
            """UPDATE users SET password=?, failed_attempts=0, locked_at=NULL, active=1
               WHERE id=?""",
            (hash_password(data.new_password), row["id"]),
        )
        connection.commit()
    finally:
        connection.close()

    clear_recovery_failures()
    user = {"id": row["id"], "employee_id": row["employee_id"],
            "name": row["name"], "role": row["role"]}
    log_action(
        user, "AUTHENTICATION", "RECOVERY_PASSWORD_RESET",
        f"{employee_id} set a new password using the recovery code. "
        "Any lock on the account was cleared.",
        "user", row["id"],
    )
    return {"status": "success", "employee_id": employee_id,
            "detail": "Password changed. You can sign in with it now."}


@router.post("/logout")
def logout(user: dict = Depends(current_user)):
    destroy_session(user.get("token"))
    log_action(user, "AUTHENTICATION", "LOGOUT", f"{user['employee_id']} logged out.", "user", user["id"])
    return {"status": "success"}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return {**user, "permissions": sorted(permissions_for(user["role"]))}


@router.post("/change-password")
def change_password(data: ChangePassword, user: dict = Depends(current_user)):
    if data.new_password != data.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirmation do not match.")
    connection = get_connection()
    try:
        row = connection.execute("SELECT password FROM users WHERE id=?", (user["id"],)).fetchone()
        if not row or not verify_password(data.old_password, row["password"]):
            raise HTTPException(status_code=400, detail="The current password is incorrect.")
        connection.execute(
            "UPDATE users SET password=? WHERE id=?",
            (hash_password(data.new_password), user["id"]),
        )
        connection.commit()
    finally:
        connection.close()
    log_action(user, "PROFILE", "CHANGE_PASSWORD", "Password changed.", "user", user["id"])
    return {"status": "success"}
