"""The controlled cabin forms the crew complete after finishing a sector.

Four forms are filed per sector: the aircraft security check list, the cabin
safety report, the voyage report and lost and found. On a round trip the
outbound crew and the returning crew each file their own set, because the set
belongs to a duty group rather than to an aircraft.

Numbering follows the paper form's footer. `issue_no` runs serially per form
type across the airline, so the first cabin safety report ever filed is issue 1
and the next is issue 2. `rev_no` counts revisions of that one report: 0 when
first filed, and one higher after each amendment, whether the crew pulled it
back or management corrected it. The date is the day the form was filled.

Once filed, the crew may pull a report back once to fix an honest mistake.
After the second submission the report is closed to them, and only management
may change it, with remarks, against a preserved history.
"""

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from database import REPORT_UNSUBMIT_LIMIT, get_connection
from paging import PageParam, PageSizeParam, paginate_list, paginate_sql
from report_forms import REPORT_FORMS, REPORT_TYPES, enabled_report_types, field_index, form_for, set_enabled_report_types
import trip_flow
from security import (
    current_user, log_action, now_iso, permissions_for, requires, today_iso,
    validate_date,
)

router = APIRouter(tags=["flight reports"])


class ReportIn(BaseModel):
    flight_id: int
    report_type: str
    payload: dict = {}
    # Submit straight away, or keep it as a draft on the way there.
    submit: bool = True
    client_ref: str | None = None


class ReportEdit(BaseModel):
    payload: dict
    # Mandatory once the report has been filed and the crew's own correction
    # has been used up.
    remarks: str | None = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _flight(connection, flight_id: int):
    row = connection.execute(
        """SELECT f.*, d.id AS duty_group_id, d.label AS duty_group_label,
                  (SELECT GROUP_CONCAT(crew_account, ',') FROM flight_assignments a WHERE a.flight_id = f.id) AS crew_account
           FROM flights f
           LEFT JOIN duty_groups d ON d.flight_id = f.id
           WHERE f.id=?""",
        (flight_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Flight not found.")
    return row


def _may_review(user) -> bool:
    return "reports_review" in permissions_for(user["role"])


def _may_read(user) -> bool:
    """7.18: Inventory reads the cabin reports too, without changing them."""
    return bool(permissions_for(user["role"]) & {"flight_reports", "reports_review", "reports_view"})


def _reader(user: dict = Depends(current_user)) -> dict:
    if not _may_read(user):
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action.")
    return user


def _crew_reader(user) -> bool:
    """A crew login: sees only the sectors it is rostered on."""
    return not (_may_review(user) or "reports_view" in permissions_for(user["role"]))


def _assert_device(connection, flight, user):
    """Only the iPad that worked the sector may file its reports."""
    if _may_review(user):
        return
    if not flight["crew_account"]:
        raise HTTPException(status_code=400, detail="No crew is rostered on this sector.")
    if user["employee_id"] not in str(flight["crew_account"]).split(","):
        raise HTTPException(
            status_code=403, detail="You were not rostered on that sector.",
        )


def _clean_payload(report_type: str, payload: dict) -> dict:
    """Keep only the fields the form actually defines, in the form's own shape."""
    fields = field_index(report_type)
    if not fields:
        raise HTTPException(status_code=400, detail="Unknown report type.")
    cleaned = {}
    for key, value in (payload or {}).items():
        field = fields.get(key)
        if not field:
            continue  # a field the form does not have is dropped rather than stored
        kind = field["type"]
        if kind in {"checks", "checklist"}:
            cleaned[key] = [str(v) for v in value] if isinstance(value, list) else []
        elif kind == "rows":
            rows = value if isinstance(value, list) else []
            cleaned[key] = [
                {c["key"]: str(row.get(c["key"], "") or "").strip()
                 for c in field["columns"]}
                for row in rows if isinstance(row, dict)
            ]
        else:
            cleaned[key] = str(value or "").strip()
    return cleaned


def _next_issue_no(connection, report_type: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(issue_no),0) + 1 AS next FROM flight_reports WHERE report_type=?",
        (report_type,),
    ).fetchone()
    return int(row["next"])


def _render(connection, row) -> dict:
    report = dict(row)
    report["payload"] = json.loads(report.get("payload") or "{}")
    form = form_for(report["report_type"]) or {}
    report["title"] = form.get("title", report["report_type"])
    report["manual"] = form.get("manual")
    report["appendix"] = form.get("appendix")
    # The footer as it is printed on the paper form.
    report["issue_label"] = f"Issue: {report['issue_no']:02d}"
    report["rev_label"] = f"Rev. {report['rev_no']:02d}"
    report["may_unsubmit"] = (
        report["status"] == "SUBMITTED"
        and int(report["unsubmit_count"]) < REPORT_UNSUBMIT_LIMIT
    )
    report["revisions"] = [dict(r) for r in connection.execute(
        """SELECT rev_no, action, changed_by, changed_at, remarks
           FROM flight_report_revisions WHERE report_id=? ORDER BY id""",
        (report["id"],),
    ).fetchall()]
    return report


REPORT_SELECT = """
SELECT r.*, f.flight_number, f.flight_date, f.origin, f.destination, f.direction,
       f.aircraft, f.aircraft_type, d.label AS duty_group_label
FROM flight_reports r
JOIN flights f ON f.id = r.flight_id
LEFT JOIN duty_groups d ON d.id = r.duty_group_id
"""


# --------------------------------------------------------------------------- #
# The forms themselves
# --------------------------------------------------------------------------- #
@router.get("/report-forms")
def report_forms(user: dict = Depends(_reader)):
    """The definition of every form, so the client renders it from the schema.
    The crew get only the forms the administrator has switched on (7.13);
    Management get them all, so a form filed before it was switched off can
    still be read."""
    if not _crew_reader(user):
        return REPORT_FORMS
    connection = get_connection()
    try:
        enabled = set(enabled_report_types(connection))
    finally:
        connection.close()
    return [f for f in REPORT_FORMS if f["type"] in enabled]


@router.get("/report-forms/visibility")
def report_forms_visibility(user: dict = Depends(requires("reports_review"))):
    """Which forms the crew see, for the administrator's switches."""
    connection = get_connection()
    try:
        enabled = set(enabled_report_types(connection))
    finally:
        connection.close()
    return [{"type": f["type"], "title": f["title"], "form_code": f["form_code"], "enabled": f["type"] in enabled}
            for f in REPORT_FORMS]


class VisibilityIn(BaseModel):
    enabled: list[str]


@router.put("/report-forms/visibility")
def set_report_forms_visibility(data: VisibilityIn, user: dict = Depends(requires("users"))):
    """Administrator only. A form switched off disappears from every crew iPad
    at once and no longer counts towards "all forms handled"; switched on, it
    is back for every crew iPad."""
    connection = get_connection()
    try:
        before = set(enabled_report_types(connection))
        chosen = set_enabled_report_types(connection, data.enabled, user["employee_id"])
    finally:
        connection.close()
    turned_on = [t for t in chosen if t not in before]
    turned_off = [t for t in before if t not in chosen]
    if turned_on or turned_off:
        log_action(user, "CABIN REPORT", "FORMS_VISIBILITY",
                   "Crew forms changed: " + "; ".join(filter(None, [
                       "on: " + ", ".join(turned_on) if turned_on else "",
                       "off: " + ", ".join(turned_off) if turned_off else ""])) + ".",
                   None, None, metadata={"enabled": chosen})
    return report_forms_visibility(user)


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
@router.get("/flight-reports")
def list_reports(flight_id: int | None = None, date: str | None = None,
                 flight_number: str | None = None, report_type: str | None = None,
                 status: str | None = None, limit: int = 300,
                 page: int | None = PageParam, page_size: int = PageSizeParam,
                 user: dict = Depends(_reader)):
    """Filed reports. Management filter by date and flight; crew see their own sectors."""
    connection = get_connection()
    try:
        conditions, params = [], []
        if _crew_reader(user):
            # A crew login sees only the sectors it was rostered on.
            conditions.append(
                """r.flight_id IN (SELECT flight_id FROM flight_assignments
                                   WHERE crew_account=?)"""
            )
            params.append(user["employee_id"])
        if flight_id:
            conditions.append("r.flight_id=?")
            params.append(flight_id)
        if date:
            conditions.append("f.flight_date=?")
            params.append(validate_date(date, "date"))
        if flight_number:
            conditions.append("f.flight_number=?")
            params.append(flight_number.strip().upper())
        if report_type:
            conditions.append("r.report_type=?")
            params.append(report_type.strip().upper())
        if status:
            conditions.append("r.status=?")
            params.append(status.strip().upper())
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        if page:
            return paginate_sql(
                connection,
                f"{REPORT_SELECT} {where} ORDER BY f.flight_date DESC, f.flight_number, r.report_type",
                params, page, page_size, mapper=lambda row: _render(connection, row))
        rows = connection.execute(
            f"""{REPORT_SELECT} {where}
                ORDER BY f.flight_date DESC, f.flight_number, r.report_type
                LIMIT ?""",
            [*params, min(limit, 1000)],
        ).fetchall()
        return [_render(connection, row) for row in rows]
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# 7.18: the forms outside the flight flow
# --------------------------------------------------------------------------- #
def _leg_forms(connection, flight_id: int, enabled: list[str]) -> list[dict]:
    """Each form of a sector with who did what: SUBMITTED, DRAFT,
    AWAITING_OTHER ("another crew member will fill it") or PENDING."""
    filed = {r["report_type"]: dict(r) for r in connection.execute(
        "SELECT id, report_type, status, submitted_by, submitted_at, created_by, created_at, edited_by, edited_at, rev_no "
        "FROM flight_reports WHERE flight_id=?", (flight_id,))}
    deferred = {}
    for r in connection.execute("SELECT report_type, device FROM flight_report_deferrals WHERE flight_id=?", (flight_id,)):
        deferred.setdefault(r["report_type"], []).append(r["device"])
    out = []
    for report_type in [t for t in REPORT_TYPES if t in enabled or t in filed]:
        form = form_for(report_type)
        row = filed.get(report_type)
        status = row["status"] if row else ("AWAITING_OTHER" if deferred.get(report_type) else "PENDING")
        out.append({
            "report_type": report_type, "title": form["title"], "form_code": form["form_code"],
            "status": status, "report_id": row["id"] if row else None,
            "submitted_by": row["submitted_by"] if row else None, "submitted_at": row["submitted_at"] if row else None,
            "saved_by": (row["edited_by"] or row["created_by"]) if row else None,
            "saved_at": (row["edited_at"] or row["created_at"]) if row else None,
            "rev_no": row["rev_no"] if row else None,
            "deferred_by": deferred.get(report_type, []),
            "switched_off": report_type not in enabled,
        })
    return out


@router.get("/flight-reports/my-sectors")
def my_report_sectors(date: str | None = None, user: dict = Depends(requires("flight_reports"))):
    """The sectors a crew member may file forms for: every sector they were
    rostered on, any date (newest first), whether or not the flight is over."""
    conditions = ["f.id IN (SELECT flight_id FROM flight_assignments WHERE crew_account=?)"]
    params = [user["employee_id"]]
    if date:
        conditions.append("f.flight_date=?")
        params.append(validate_date(date, "date"))
    connection = get_connection()
    try:
        enabled = enabled_report_types(connection)
        rows = connection.execute(
            f"""SELECT f.id, f.flight_number, f.flight_date, f.origin, f.destination, f.direction, f.trip_type,
                       f.linked_flight_id, f.status
                FROM flights f WHERE {' AND '.join(conditions)}
                ORDER BY f.flight_date DESC, f.flight_number, CASE f.direction WHEN 'RETURN' THEN 1 ELSE 0 END
                LIMIT 200""", params).fetchall()
        out = []
        for r in rows:
            forms = _leg_forms(connection, r["id"], enabled)
            mine = [f for f in forms if user["employee_id"] in f["deferred_by"]]
            out.append({**dict(r), "sector": f"{r['origin']}-{r['destination']}",
                        "submitted": sum(1 for f in forms if f["status"] == "SUBMITTED"),
                        "drafts": sum(1 for f in forms if f["status"] == "DRAFT"),
                        "pending": sum(1 for f in forms if f["status"] in ("PENDING", "AWAITING_OTHER")),
                        "total": len([f for f in forms if not f["switched_off"]]),
                        "left_to_others": bool(mine)})
        return out
    finally:
        connection.close()


@router.get("/flight-reports/trips")
def report_trips(date: str | None = None, date_to: str | None = None, flight_number: str | None = None,
                 status: str | None = None, page: int | None = PageParam, page_size: int = PageSizeParam,
                 user: dict = Depends(_reader)):
    """Management / Inventory / Admin: the forms trip by trip
    ("KTM-DXB-KTM (date)"), each leg with every form's state."""
    if _crew_reader(user):
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action.")
    conditions, params = ["(f.direction <> 'RETURN' OR f.linked_flight_id IS NULL)"], []
    if date:
        conditions.append("f.flight_date >= ?" if date_to else "f.flight_date = ?")
        params.append(validate_date(date, "date"))
    if date_to:
        conditions.append("f.flight_date <= ?")
        params.append(validate_date(date_to, "date"))
    if flight_number:
        conditions.append("(f.flight_number LIKE ? OR f.id IN (SELECT linked_flight_id FROM flights WHERE flight_number LIKE ?))")
        needle = f"%{flight_number.strip().upper()[:12]}%"
        params.extend([needle, needle])
    connection = get_connection()
    try:
        enabled = enabled_report_types(connection)
        roots = connection.execute(
            f"SELECT * FROM flights f WHERE {' AND '.join(conditions)} ORDER BY f.flight_date DESC, f.flight_number",
            params).fetchall()
        trips = []
        for root in roots:
            legs = [dict(root)] + [dict(r) for r in connection.execute(
                "SELECT * FROM flights WHERE linked_flight_id=? AND direction='RETURN' ORDER BY id", (root["id"],))]
            route = "-".join([legs[0]["origin"]] + [l["destination"] for l in legs])
            out_legs = []
            for leg in legs:
                forms = _leg_forms(connection, leg["id"], enabled)
                out_legs.append({
                    "id": leg["id"], "flight_number": leg["flight_number"], "flight_date": leg["flight_date"],
                    "origin": leg["origin"], "destination": leg["destination"], "direction": leg["direction"],
                    "original_destination": leg.get("original_destination"), "status": leg["status"],
                    "sector": f"{leg['origin']}-{leg['destination']}", "forms": forms,
                    "pending": sum(1 for f in forms if f["status"] != "SUBMITTED" and not f["switched_off"]),
                })
            pending = sum(l["pending"] for l in out_legs)
            trips.append({"root_id": root["id"], "flight_date": root["flight_date"],
                          "flight_numbers": " / ".join(dict.fromkeys(l["flight_number"] for l in legs)),
                          "route": route, "label": f"{route} ({root['flight_date']})",
                          "legs": out_legs, "pending": pending,
                          "complete": pending == 0})
        if status:
            wanted = status.strip().upper()
            trips = [t for t in trips if (wanted == "PENDING" and t["pending"]) or (wanted == "COMPLETE" and t["complete"])]
        if page:
            return paginate_list(trips, page, page_size)
        return trips
    finally:
        connection.close()


class SectorIn(BaseModel):
    flight_id: int


@router.post("/flight-reports/final-submit")
def final_submit(data: SectorIn, user: dict = Depends(requires("flight_reports"))):
    """The final submission of every form saved (as a draft) for a sector.
    Afterwards the crew can no longer change them; it is in the audit log who
    submitted them and when."""
    connection = get_connection()
    try:
        flight = _flight(connection, data.flight_id)
        _assert_device(connection, flight, user)
        enabled = enabled_report_types(connection)
        drafts = connection.execute(
            "SELECT * FROM flight_reports WHERE flight_id=? AND status='DRAFT'", (data.flight_id,)).fetchall()
        drafts = [d for d in drafts if d["report_type"] in enabled or _may_review(user)]
        if not drafts:
            already = connection.execute(
                "SELECT COUNT(*) FROM flight_reports WHERE flight_id=? AND status='SUBMITTED'", (data.flight_id,)).fetchone()[0]
            if already:
                # A replay from a phone that lost its signal, or a colleague
                # who submitted first: nothing is left to do, which is fine.
                return {**reports_for_flight(data.flight_id, user), "already_submitted": True}
            raise HTTPException(status_code=400, detail="There is no saved form to submit on this sector.")
        now = now_iso()
        titles = []
        for d in drafts:
            rev_no = int(d["rev_no"]) + (1 if int(d["unsubmit_count"] or 0) > 0 else 0)
            connection.execute(
                "UPDATE flight_reports SET status='SUBMITTED', submitted_by=?, submitted_at=?, rev_no=? "
                "WHERE id=? AND status='DRAFT'", (user["employee_id"], now, rev_no, d["id"]))
            connection.execute(
                """INSERT INTO flight_report_revisions(report_id,rev_no,payload,action,changed_by,changed_at)
                   VALUES (?,?,?,?,?,?)""",
                (d["id"], rev_no, d["payload"], "SUBMITTED" if rev_no == 0 else "RESUBMITTED", user["employee_id"], now))
            titles.append(form_for(d["report_type"])["title"])
        connection.commit()
    finally:
        connection.close()
    log_action(user, "CABIN REPORT", "FINAL_SUBMIT",
               f"Final submission for {flight['flight_number']} {flight['origin']}-{flight['destination']} "
               f"{flight['flight_date']}: {', '.join(titles)}.", "flight", data.flight_id,
               metadata={"forms": titles})
    return reports_for_flight(data.flight_id, user)


@router.post("/flight-reports/defer-all")
def defer_all(data: SectorIn, user: dict = Depends(requires("flight_reports"))):
    """"Another crew member will fill these forms": one press for the whole
    sector. It only records that this crew member left the forms to a
    colleague; it does not file anything."""
    connection = get_connection()
    try:
        flight = _flight(connection, data.flight_id)
        _assert_device(connection, flight, user)
        enabled = enabled_report_types(connection)
        done = {r["report_type"] for r in connection.execute(
            "SELECT report_type FROM flight_reports WHERE flight_id=? AND status='SUBMITTED'", (data.flight_id,))}
        now = now_iso()
        left = [t for t in enabled if t not in done]
        for report_type in left:
            connection.execute(
                "INSERT OR IGNORE INTO flight_report_deferrals(flight_id,report_type,device,created_at) VALUES (?,?,?,?)",
                (data.flight_id, report_type, user["employee_id"], now))
        connection.commit()
    finally:
        connection.close()
    log_action(user, "CABIN REPORT", "LEAVE_TO_COLLEAGUE",
               f"{user['employee_id']} left the forms of {flight['flight_number']} {flight['origin']}-{flight['destination']} "
               f"{flight['flight_date']} to another crew member.", "flight", data.flight_id)
    return reports_for_flight(data.flight_id, user)


@router.delete("/flight-reports/defer-all")
def undefer_all(flight_id: int, user: dict = Depends(requires("flight_reports"))):
    connection = get_connection()
    try:
        flight = _flight(connection, flight_id)
        _assert_device(connection, flight, user)
        connection.execute("DELETE FROM flight_report_deferrals WHERE flight_id=? AND device=?",
                           (flight_id, user["employee_id"]))
        connection.commit()
    finally:
        connection.close()
    return reports_for_flight(flight_id, user)


@router.get("/flight-reports/{report_id}")
def get_report(report_id: int, user: dict = Depends(_reader)):
    connection = get_connection()
    try:
        row = connection.execute(f"{REPORT_SELECT} WHERE r.id=?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Report not found.")
        if _crew_reader(user):
            _assert_device(connection, _flight(connection, row["flight_id"]), user)
        return _render(connection, row)
    finally:
        connection.close()


@router.get("/flights/{flight_id}/reports")
def reports_for_flight(flight_id: int, user: dict = Depends(_reader)):
    """Every form for a sector, filed or not, so the crew can see what is left.

    The outstanding list is what the application shows after a sector is
    finished, which is how the crew know the sector is not closed out yet.
    """
    connection = get_connection()
    try:
        flight = _flight(connection, flight_id)
        if _crew_reader(user):
            _assert_device(connection, flight, user)

        existing = {
            row["report_type"]: _render(connection, row)
            for row in connection.execute(
                f"{REPORT_SELECT} WHERE r.flight_id=?", (flight_id,)
            ).fetchall()
        }

        # "Submitted by the other iPad" marks (7.11).
        deferrals = {}
        for row in connection.execute(
                "SELECT report_type, device, created_at FROM flight_report_deferrals WHERE flight_id=?", (flight_id,)):
            deferrals.setdefault(row["report_type"], []).append({"device": row["device"], "at": row["created_at"]})
        devices = str(flight["crew_account"] or "").split(",") if flight["crew_account"] else []

        entries = []
        # The crew see the forms switched on for them; Management every form
        # that is on or already filed for this sector.
        visible = enabled_report_types(connection)
        if _may_review(user):
            visible = [t for t in REPORT_TYPES if t in visible or t in existing]
        for report_type in visible:
            form = form_for(report_type)
            status = existing.get(report_type, {}).get("status", "NOT_STARTED")
            marks = deferrals.get(report_type, [])
            entry = {
                "report_type": report_type,
                "title": form["title"],
                "form_code": form["form_code"],
                "manual": form["manual"],
                "appendix": form["appendix"],
                "report": existing.get(report_type),
                "status": "AWAITING_OTHER" if (status != "SUBMITTED" and marks) else status,
                "deferred_by": [m["device"] for m in marks],
                "deferred_by_me": user["employee_id"] in [m["device"] for m in marks],
                "other_device": next((d for d in devices if d != user["employee_id"]), None),
            }
            entries.append(entry)

        # 7.12: the sector's state as THIS iPad sees it. A leg this iPad has
        # finished is FLOWN for it even while the other iPad is still selling.
        my_state = (trip_flow.device_state(connection, flight_id, user["employee_id"])
                    if not _may_review(user) else {c: None for c in trip_flow.DEVICE_STATE_COLUMNS})
        my_finished = flight["status"] != "OPEN" or bool(my_state["finished_at"])
        return {
            "flight_id": flight_id,
            "my_finished": my_finished,
            "my_started": flight["direction"] != "RETURN" or bool(my_state["started_at"]) or (_may_review(user) and bool(flight["started_at"])),
            "flight_number": flight["flight_number"],
            "flight_date": flight["flight_date"],
            "origin": flight["origin"],
            "destination": flight["destination"],
            "direction": flight["direction"],
            "sector": f"{flight['origin']}-{flight['destination']}",
            "aircraft": flight["aircraft"],
            "aircraft_type": flight["aircraft_type"],
            "duty_group_id": flight["duty_group_id"],
            "duty_group_label": flight["duty_group_label"],
            "flight_status": "FLOWN" if (flight["status"] == "OPEN" and my_finished) else flight["status"],
            "merged_status": flight["status"],
            "trip_type": flight["trip_type"],
            "segment": 2 if flight["direction"] == "RETURN" else 1,
            # 7.18: forms are outside the flight flow; each form closes to the
            # crew on its own final submission, never because of the trip.
            "locked": False,
            "read_only": not _may_review(user) and "flight_reports" not in permissions_for(user["role"]),
            "drafts": [e["report_type"] for e in entries if e["status"] == "DRAFT"],
            "devices": devices,
            "reports": entries,
            # Still to handle on this iPad: neither submitted nor marked as the other iPad's.
            "outstanding": [e["report_type"] for e in entries if e["status"] not in ("SUBMITTED", "AWAITING_OTHER")],
            "awaiting_other": [e["report_type"] for e in entries if e["status"] == "AWAITING_OTHER"],
        }
    finally:
        connection.close()


class DeferIn(BaseModel):
    flight_id: int
    report_type: str


@router.post("/flight-reports/defer")
def defer_report(data: DeferIn, user: dict = Depends(requires("flight_reports"))):
    """"Submitted by the other iPad": this device leaves the form to the other
    one (7.11). The form counts as handled for the trip's progress; the other
    iPad's own submission arrives when it syncs. Management sees it as
    "awaiting the other iPad" until then."""
    report_type = data.report_type.strip().upper()
    if report_type not in REPORT_TYPES:
        raise HTTPException(status_code=400, detail="Unknown report type.")
    connection = get_connection()
    try:
        flight = _flight(connection, data.flight_id)
        _assert_device(connection, flight, user)
        trip_flow.assert_reports_open(connection, flight["id"], _may_review(user), user["employee_id"])
        if connection.execute("SELECT 1 FROM flight_reports WHERE flight_id=? AND report_type=? AND status='SUBMITTED'",
                              (data.flight_id, report_type)).fetchone():
            raise HTTPException(status_code=400, detail="This form has already been submitted.")
        connection.execute(
            "INSERT OR IGNORE INTO flight_report_deferrals(flight_id,report_type,device,created_at) VALUES (?,?,?,?)",
            (data.flight_id, report_type, user["employee_id"], now_iso()))
        connection.commit()
    finally:
        connection.close()
    log_action(user, "REPORTS", "DEFER_REPORT",
               f"{form_for(report_type)['title']} for {flight['flight_number']} marked as submitted by the other iPad.",
               "flight", data.flight_id, metadata={"report_type": report_type})
    return {"status": "deferred", "report_type": report_type}


@router.delete("/flight-reports/defer")
def undefer_report(flight_id: int, report_type: str, user: dict = Depends(requires("flight_reports"))):
    """Take the form back on this iPad."""
    report_type = report_type.strip().upper()
    connection = get_connection()
    try:
        flight = _flight(connection, flight_id)
        _assert_device(connection, flight, user)
        connection.execute("DELETE FROM flight_report_deferrals WHERE flight_id=? AND report_type=? AND device=?",
                           (flight_id, report_type, user["employee_id"]))
        connection.commit()
    finally:
        connection.close()
    return {"status": "cleared", "report_type": report_type}


# --------------------------------------------------------------------------- #
# Filing
# --------------------------------------------------------------------------- #
@router.post("/flight-reports")
def file_report(data: ReportIn, user: dict = Depends(requires("flight_reports"))):
    """Create or update a sector's report, and file it unless it is kept as a draft."""
    report_type = data.report_type.strip().upper()
    if report_type not in REPORT_TYPES:
        raise HTTPException(status_code=400, detail="Unknown report type.")
    form = form_for(report_type)
    payload = _clean_payload(report_type, data.payload)

    connection = get_connection()
    try:
        flight = _flight(connection, data.flight_id)
        _assert_device(connection, flight, user)
        if not _may_review(user) and report_type not in enabled_report_types(connection):
            raise HTTPException(status_code=400, detail="This form is switched off for the crew at the moment.")
        trip_flow.assert_reports_open(connection, flight["id"], _may_review(user), user["employee_id"])
        # 7.10: forms may be filled in at any time, before or after the sector
        # is finished. Finishing reminds the crew of any form still outstanding.

        existing = connection.execute(
            "SELECT * FROM flight_reports WHERE flight_id=? AND report_type=?",
            (data.flight_id, report_type),
        ).fetchone()

        now = now_iso()
        if existing:
            # A replayed offline submission is the same submission.
            if data.client_ref and existing["client_ref"] == data.client_ref \
                    and existing["status"] == "SUBMITTED":
                return _render(connection, connection.execute(
                    f"{REPORT_SELECT} WHERE r.id=?", (existing["id"],)).fetchone())

            if existing["status"] == "SUBMITTED" and not data.submit and not _may_review(user):
                raise HTTPException(
                    status_code=400,
                    detail="This form has had its final submission and can no longer be changed. "
                           "Ask Management if something needs correcting.")
            if existing["status"] == "SUBMITTED" and data.submit and data.client_ref \
                    and existing["submitted_by"] != user["employee_id"]:
                # 7.12: both iPads filled this form in on board, with no way of
                # knowing. Nothing is lost: the second copy is kept as the next
                # revision, marked as such, and Management sees both.
                rev_no = int(existing["rev_no"]) + 1
                note = f"Also filled in on iPad {user['employee_id']} while offline; merged on upload."
                connection.execute(
                    """UPDATE flight_reports SET payload=?, rev_no=?, status='SUBMITTED',
                           edited_by=?, edited_at=?, edit_remarks=? WHERE id=?""",
                    (json.dumps(payload, ensure_ascii=False), rev_no, user["employee_id"], now, note, existing["id"]))
                connection.execute(
                    """INSERT INTO flight_report_revisions(report_id,rev_no,payload,action,changed_by,changed_at,remarks)
                       VALUES (?,?,?,?,?,?,?)""",
                    (existing["id"], rev_no, json.dumps(payload, ensure_ascii=False), "MERGED",
                     user["employee_id"], now, note))
                connection.commit()
                merged = _render(connection, connection.execute(
                    f"{REPORT_SELECT} WHERE r.id=?", (existing["id"],)).fetchone())
                log_action(user, "CABIN REPORT", "MERGE",
                           f"{merged['title']} for {merged['flight_number']} {merged['flight_date']} was filled in on both "
                           f"iPads; the copy from {user['employee_id']} is kept as {merged['rev_label']}.",
                           "flight_report", existing["id"])
                return merged
            if existing["status"] == "SUBMITTED":
                raise HTTPException(
                    status_code=400,
                    detail=("This form has already been saved. To change it, edit it and give "
                            "remarks for the change."),
                )
            report_id = existing["id"]
            rev_no = int(existing["rev_no"])
            # Filing after pulling the report back counts as the next revision.
            if data.submit and int(existing["unsubmit_count"]) > 0:
                rev_no += 1
            connection.execute(
                """UPDATE flight_reports
                   SET payload=?, report_date=?, rev_no=?,
                       status=?, submitted_by=?, submitted_at=?
                   WHERE id=?""",
                (json.dumps(payload, ensure_ascii=False), today_iso(), rev_no,
                 "SUBMITTED" if data.submit else "DRAFT",
                 user["employee_id"] if data.submit else existing["submitted_by"],
                 now if data.submit else existing["submitted_at"],
                 report_id),
            )
        else:
            issue_no = _next_issue_no(connection, report_type)
            try:
                cur = connection.execute(
                    """INSERT INTO flight_reports
                           (flight_id,duty_group_id,report_type,form_code,issue_no,rev_no,
                            report_date,status,payload,created_by,created_at,
                            submitted_by,submitted_at,client_ref)
                       VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?,?)""",
                    (data.flight_id, flight["duty_group_id"], report_type,
                     form["form_code"], issue_no, today_iso(),
                     "SUBMITTED" if data.submit else "DRAFT",
                     json.dumps(payload, ensure_ascii=False),
                     user["employee_id"], now,
                     user["employee_id"] if data.submit else None,
                     now if data.submit else None,
                     data.client_ref),
                )
            except sqlite3.IntegrityError:
                # Two queued attempts raced; the first one won and is the answer.
                connection.rollback()
                row = connection.execute(
                    f"{REPORT_SELECT} WHERE r.flight_id=? AND r.report_type=?",
                    (data.flight_id, report_type),
                ).fetchone()
                if row:
                    return _render(connection, row)
                raise
            report_id = cur.lastrowid
            rev_no = 0

        if data.submit:
            connection.execute(
                """INSERT INTO flight_report_revisions
                       (report_id,rev_no,payload,action,changed_by,changed_at)
                   VALUES (?,?,?,?,?,?)""",
                (report_id, rev_no, json.dumps(payload, ensure_ascii=False),
                 "SUBMITTED" if rev_no == 0 else "RESUBMITTED", user["employee_id"], now),
            )
        connection.commit()
        row = connection.execute(f"{REPORT_SELECT} WHERE r.id=?", (report_id,)).fetchone()
        result = _render(connection, row)
    except HTTPException:
        connection.rollback()
        raise
    finally:
        connection.close()

    if data.submit:
        log_action(
            user, "CABIN REPORT", "SUBMIT",
            f"{form['title']} filed for {result['flight_number']} "
            f"{result['origin']}-{result['destination']} on {result['flight_date']} "
            f"({result['issue_label']}, {result['rev_label']}).",
            "flight_report", report_id,
            metadata={"report_type": report_type, "flight_id": data.flight_id,
                      "issue_no": result["issue_no"], "rev_no": result["rev_no"]},
        )
    return result


@router.post("/flight-reports/{report_id}/unsubmit")
def unsubmit_report(report_id: int, user: dict = Depends(requires("flight_reports"))):
    """Pull a filed report back so the crew can correct it. Allowed once."""
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM flight_reports WHERE id=?", (report_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Report not found.")
        flight = _flight(connection, row["flight_id"])
        if not _may_review(user):
            # 7.18: after the final submission the crew cannot reopen a form.
            raise HTTPException(status_code=403,
                                detail="A form cannot be changed after its final submission. Ask Management.")
        if row["status"] != "SUBMITTED":
            raise HTTPException(status_code=400, detail="This report has not been filed yet.")
        if int(row["unsubmit_count"]) >= REPORT_UNSUBMIT_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=("This report has already been pulled back once and can no longer be "
                        "changed on the iPad. Ask Management to correct it."),
            )

        now = now_iso()
        connection.execute(
            """UPDATE flight_reports SET status='DRAFT', unsubmit_count=unsubmit_count+1
               WHERE id=? AND status='SUBMITTED'""",
            (report_id,),
        )
        connection.execute(
            """INSERT INTO flight_report_revisions
                   (report_id,rev_no,payload,action,changed_by,changed_at)
               VALUES (?,?,?,'UNSUBMITTED',?,?)""",
            (report_id, row["rev_no"], row["payload"], user["employee_id"], now),
        )
        connection.commit()
        result = _render(connection, connection.execute(
            f"{REPORT_SELECT} WHERE r.id=?", (report_id,)).fetchone())
    except HTTPException:
        connection.rollback()
        raise
    finally:
        connection.close()

    log_action(
        user, "CABIN REPORT", "UNSUBMIT",
        f"{result['title']} pulled back for correction on {result['flight_number']} "
        f"{result['flight_date']}. This was the only pull-back available.",
        "flight_report", report_id,
        metadata={"report_type": result["report_type"]},
    )
    return result


@router.put("/flight-reports/{report_id}")
def edit_report(report_id: int, data: ReportEdit,
                user: dict = Depends(requires("flight_reports"))):
    """Change a saved report. Remarks are mandatory.

    Management may correct any report. The crew may change their own sector's
    saved forms until they lock (next flight started, or whole flight finished).
    """
    remarks = (data.remarks or "").strip()
    if not remarks:
        raise HTTPException(
            status_code=400, detail="Remarks are mandatory when changing a saved report.",
        )
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM flight_reports WHERE id=?", (report_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Report not found.")
        reviewer = _may_review(user)
        if not reviewer:
            # 7.18: the crew edit a draft by saving it again; once finally
            # submitted only Management may correct it.
            raise HTTPException(status_code=403,
                                detail="A form cannot be changed after its final submission. Ask Management.")
        payload = _clean_payload(row["report_type"], data.payload)
        rev_no = int(row["rev_no"]) + 1
        now = now_iso()
        connection.execute(
            """UPDATE flight_reports
               SET payload=?, rev_no=?, status='SUBMITTED',
                   edited_by=?, edited_at=?, edit_remarks=?
               WHERE id=?""",
            (json.dumps(payload, ensure_ascii=False), rev_no,
             user["employee_id"], now, remarks, report_id),
        )
        connection.execute(
            """INSERT INTO flight_report_revisions
                   (report_id,rev_no,payload,action,changed_by,changed_at,remarks)
               VALUES (?,?,?,?,?,?,?)""",
            (report_id, rev_no, json.dumps(payload, ensure_ascii=False),
             "CORRECTED" if reviewer else "AMENDED", user["employee_id"], now, remarks),
        )
        connection.commit()
        result = _render(connection, connection.execute(
            f"{REPORT_SELECT} WHERE r.id=?", (report_id,)).fetchone())
    except HTTPException:
        connection.rollback()
        raise
    finally:
        connection.close()

    log_action(
        user, "CABIN REPORT", "CORRECT",
        f"{result['title']} corrected for {result['flight_number']} "
        f"{result['flight_date']} (now {result['rev_label']}). Remarks: {remarks}",
        "flight_report", report_id,
        metadata={"report_type": result["report_type"], "remarks": remarks,
                  "rev_no": rev_no},
    )
    return result


@router.get("/flight-reports/{report_id}/pdf")
def report_pdf(report_id: int, user: dict = Depends(_reader)):
    """The report laid out like the paper form, for printing or e-mailing."""
    from report_pdf import build_pdf
    connection = get_connection()
    try:
        row = connection.execute(f"{REPORT_SELECT} WHERE r.id=?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Report not found.")
        if _crew_reader(user):
            _assert_device(connection, _flight(connection, row["flight_id"]), user)
        report = _render(connection, row)
    finally:
        connection.close()
    pdf = build_pdf(report)
    name = f"{report['report_type'].lower()}-{report['flight_number']}-{report['flight_date']}-rev{report['rev_no']:02d}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/flight-reports/{report_id}/revisions")
def report_revisions(report_id: int, user: dict = Depends(_reader)):
    """Every version of a report, so a correction can be read against the original."""
    connection = get_connection()
    try:
        rows = connection.execute(
            """SELECT id, rev_no, payload, action, changed_by, changed_at, remarks
               FROM flight_report_revisions WHERE report_id=? ORDER BY id""",
            (report_id,),
        ).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"] or "{}")} for r in rows]
    finally:
        connection.close()
