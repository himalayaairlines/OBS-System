"""In-app notifications. Every signed-in user sees only their own."""

from fastapi import APIRouter, Depends, HTTPException, Query

from comms_core import iso, utc_now
from database import get_connection
from paging import MAX_PAGE_SIZE, PageParam
from security import current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(unread_only: bool = False,
                       severity: str | None = Query(None, pattern="^(LOW|CRITICAL|OUT)$"),
                       limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                       page: int | None = PageParam, page_size: int | None = Query(None, ge=1, le=MAX_PAGE_SIZE),
                       user: dict = Depends(current_user)):
    conditions, params = ["n.user_id=?"], [user["id"]]
    if unread_only:
        conditions.append("n.read_at IS NULL")
    if severity:
        conditions.append("n.severity=?")
        params.append(severity)
    where = " AND ".join(conditions)
    connection = get_connection()
    try:
        total = connection.execute(f"SELECT COUNT(*) c FROM user_notifications n WHERE {where}", params).fetchone()["c"]
        if page:
            limit = page_size or limit
            pages = max(1, -(-total // limit))
            page = min(page, pages)
            offset = (page - 1) * limit
        rows = connection.execute(
            f"""SELECT n.id, n.incident_id, n.product_id, n.kind, n.severity, n.title, n.body,
                       n.read_at, n.created_at, i.status AS incident_status
                FROM user_notifications n LEFT JOIN alert_incidents i ON i.id = n.incident_id
                WHERE {where} ORDER BY n.id DESC LIMIT ? OFFSET ?""", (*params, limit, offset)).fetchall()
        unread = connection.execute(
            "SELECT COUNT(*) c FROM user_notifications WHERE user_id=? AND read_at IS NULL",
            (user["id"],)).fetchone()["c"]
        body = {"total": total, "unread": unread, "items": [dict(r) for r in rows]}
        if page:
            body.update(page=page, page_size=limit, pages=pages, has_prev=page > 1, has_next=page < pages)
        return body
    finally:
        connection.close()


@router.get("/unread-count")
def unread_count(user: dict = Depends(current_user)):
    connection = get_connection()
    try:
        row = connection.execute(
            """SELECT COUNT(*) c, MAX(CASE WHEN severity IN ('CRITICAL','OUT') THEN 1 ELSE 0 END) urgent
               FROM user_notifications WHERE user_id=? AND read_at IS NULL""", (user["id"],)).fetchone()
        return {"unread": row["c"], "urgent": bool(row["urgent"])}
    finally:
        connection.close()


@router.post("/{notification_id}/read")
def mark_read(notification_id: int, user: dict = Depends(current_user)):
    connection = get_connection()
    try:
        with connection:
            changed = connection.execute(
                "UPDATE user_notifications SET read_at=COALESCE(read_at, ?) WHERE id=? AND user_id=?",
                (iso(utc_now()), notification_id, user["id"])).rowcount
    finally:
        connection.close()
    if not changed:
        # Someone else's notification looks exactly like a missing one.
        raise HTTPException(status_code=404, detail="Notification not found.")
    return {"status": "read"}


@router.post("/read-all")
def mark_all_read(user: dict = Depends(current_user)):
    connection = get_connection()
    try:
        with connection:
            changed = connection.execute(
                "UPDATE user_notifications SET read_at=? WHERE user_id=? AND read_at IS NULL",
                (iso(utc_now()), user["id"])).rowcount
    finally:
        connection.close()
    return {"status": "read", "count": changed}
