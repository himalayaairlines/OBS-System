"""Cash (7.18): the floating cash and the cash sales collection of a sector.

The paper "Floating Cash Balance / Cash Sales Collection" sheet, one per
sector, with a block per crew member who carried cash:

  floating cash   opening balance by denomination (Inventory hands it out)
                  and closing balance (what came back)
  collection      NPR by denomination, plus foreign currency lines
                  (currency, amount, exchange rate, NPR value)
  totals          total collected, the crew member's commission, net cash,
                  and the grand total for the sector

Who may do what
  * any role with "cash" (crew, Inventory, Management, Admin) fills a part
    that has not been saved yet; finance only reads ("cash_view");
  * a crew member reaches only the sectors they are rostered on;
  * once a part is saved only the administrator may change it, with
    remarks; once the sheet is approved (Inventory, Management or Admin)
    the same applies to the whole sheet;
  * every save, change, approval and fix is in the sheet's history and in
    the audit log.

Reconciliation
  The sales recorded on the crew members' phones say what each of them
  should have collected, per currency. When every rostered crew member has
  uploaded (finished the sector) the saved collection is compared with that;
  a difference raises a notice everyone sees at the top of the app until it
  is corrected, or until the administrator marks it fixed (a system mistake).
  The notice stays in the sheet's history either way.
"""

import html
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from database import COMMISSION_RATE, CREW_ROLES, get_connection
from paging import PageParam, PageSizeParam, paginate_list
from security import current_user, log_action, permissions_for, requires, validate_date

router = APIRouter(tags=["cash"])

DENOMINATIONS = (1000, 500, 100, 50, 20, 10, 5)
SECTIONS = ("float_open", "float_close", "collection")
SECTION_LABELS = {"float_open": "opening floating cash", "float_close": "closing floating cash",
                  "collection": "cash sales collection"}
TOLERANCE = 0.5          # NPR / currency units: anything above is a difference

SCHEMA = """
CREATE TABLE IF NOT EXISTS cash_sheets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id INTEGER NOT NULL UNIQUE REFERENCES flights(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'OPEN',
    approved_by TEXT, approved_at TEXT, approval_remarks TEXT,
    created_by TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cash_sheet_crew (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id INTEGER NOT NULL REFERENCES cash_sheets(id) ON DELETE CASCADE,
    crew_account TEXT NOT NULL,
    crew_name TEXT NOT NULL,
    staff_no TEXT,
    float_open TEXT, float_open_by TEXT, float_open_at TEXT,
    float_close TEXT, float_close_by TEXT, float_close_at TEXT,
    npr_collect TEXT, fcy_collect TEXT, collection_by TEXT, collection_at TEXT,
    remarks TEXT,
    UNIQUE(sheet_id, crew_account)
);
CREATE TABLE IF NOT EXISTS cash_sheet_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id INTEGER NOT NULL REFERENCES cash_sheets(id) ON DELETE CASCADE,
    at TEXT NOT NULL, by TEXT NOT NULL, action TEXT NOT NULL,
    crew_account TEXT, detail TEXT, remarks TEXT
);
CREATE TABLE IF NOT EXISTS cash_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id INTEGER NOT NULL REFERENCES cash_sheets(id) ON DELETE CASCADE,
    flight_id INTEGER NOT NULL,
    crew_account TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT, closed_by TEXT, close_reason TEXT, close_remarks TEXT
);
CREATE INDEX IF NOT EXISTS idx_cash_alerts_open ON cash_alerts(closed_at);
"""


def initialize_cash(connection):
    connection.executescript(SCHEMA)
    connection.commit()


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_admin(user):
    return "users" in permissions_for(user["role"])


def _may_view(user):
    return bool(permissions_for(user["role"]) & {"cash", "cash_view"})


def _may_fill(user):
    return "cash" in permissions_for(user["role"])


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def _flight(connection, flight_id):
    row = connection.execute("SELECT * FROM flights WHERE id=?", (flight_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Flight not found.")
    return row


def _rostered(connection, flight_id):
    return [r["crew_account"] for r in connection.execute(
        "SELECT crew_account FROM flight_assignments WHERE flight_id=? ORDER BY id", (flight_id,))]


def _assert_access(connection, flight, user, write=False):
    if not _may_view(user):
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action.")
    if write and not _may_fill(user):
        raise HTTPException(status_code=403, detail="Finance reads the cash sheets but does not fill them.")
    if user["role"] in CREW_ROLES and user["employee_id"] not in _rostered(connection, flight["id"]):
        raise HTTPException(status_code=403, detail="You are not rostered on that sector.")


def _sheet(connection, flight_id, create_by=None):
    row = connection.execute("SELECT * FROM cash_sheets WHERE flight_id=?", (flight_id,)).fetchone()
    if row or not create_by:
        return row
    connection.execute("INSERT OR IGNORE INTO cash_sheets(flight_id,status,created_by,created_at) VALUES (?,?,?,?)",
                       (flight_id, "OPEN", create_by, _now()))
    return connection.execute("SELECT * FROM cash_sheets WHERE flight_id=?", (flight_id,)).fetchone()


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _deno_total(values: dict) -> float:
    return float(sum(int(k) * int(v or 0) for k, v in (values or {}).items() if str(k).isdigit()))


def _system_collection(connection, flight_id):
    """What the phones recorded, per crew login and currency (normal sales)."""
    out = {}
    for r in connection.execute(
            """SELECT crew_id, transaction_currency AS currency,
                      ROUND(SUM(transaction_currency_amount), 2) AS amount,
                      ROUND(SUM(npr_amount), 2) AS npr, COUNT(DISTINCT order_ref) AS transactions,
                      MAX(exchange_rate_to_npr) AS rate
               FROM sales WHERE flight_id=? AND sale_type='NORMAL'
               GROUP BY crew_id, transaction_currency""", (flight_id,)):
        crew = out.setdefault(r["crew_id"], {"currencies": {}, "npr_value": 0.0, "transactions": 0})
        crew["currencies"][r["currency"] or "NPR"] = {
            "amount": float(r["amount"] or 0), "npr": float(r["npr"] or 0), "rate": float(r["rate"] or 1),
            "transactions": int(r["transactions"] or 0)}
        crew["npr_value"] += float(r["npr"] or 0)
        crew["transactions"] += int(r["transactions"] or 0)
    return out


def _commission_by_account(connection, flight_id):
    """Each crew login's commission on the sector: pool share + own combos."""
    from routers.analytics import sector_commission
    data = sector_commission(connection, flight_id)
    by_member = {m["crew_member_id"]: m["total_npr"] for m in data["members"]}
    out = {}
    for r in connection.execute(
            "SELECT employee_id, crew_member_id FROM users WHERE crew_member_id IS NOT NULL"):
        if r["crew_member_id"] in by_member:
            out[r["employee_id"]] = by_member[r["crew_member_id"]]
    return out, data


def _all_uploaded(connection, flight):
    """Every rostered crew member has finished the sector on the server (their
    finish arrives after their sales), or the sector is closed."""
    if flight["status"] != "OPEN":
        return True
    rostered = _rostered(connection, flight["id"])
    if not rostered:
        return False
    done = {r["device"] for r in connection.execute(
        "SELECT device FROM trip_device_state WHERE flight_id=? AND finished_at IS NOT NULL", (flight["id"],))}
    return all(a in done for a in rostered)


def _block(row, system, commission, flight_status, sheet_status, user):
    float_open = _loads(row["float_open"], {})
    float_close = _loads(row["float_close"], {})
    npr = _loads(row["npr_collect"], {})
    fcy = _loads(row["fcy_collect"], [])
    fcy_npr = round(sum(float(x.get("amount") or 0) * float(x.get("rate") or 0) for x in fcy), 2)
    collected = round(_deno_total(npr) + fcy_npr, 2)
    sys_crew = system.get(row["crew_account"], {"currencies": {}, "npr_value": 0.0, "transactions": 0})
    comm = round(float(commission.get(row["crew_account"], 0.0)), 2)
    saved = {"float_open": bool(row["float_open_at"]), "float_close": bool(row["float_close_at"]),
             "collection": bool(row["collection_at"])}
    admin = _is_admin(user)
    approved = sheet_status == "APPROVED"
    editable = {s: (admin or (not saved[s] and not approved)) and _may_fill(user) for s in SECTIONS}
    if user["role"] in CREW_ROLES and row["crew_account"] != user["employee_id"]:
        # A crew member fills their own block; the floating cash may be filled
        # by whoever hands it out, which is not normally a crew member.
        editable = {s: False for s in SECTIONS}
    return {
        "id": row["id"], "crew_account": row["crew_account"], "crew_name": row["crew_name"],
        "staff_no": row["staff_no"], "remarks": row["remarks"],
        "float_open": float_open, "float_open_total": _deno_total(float_open),
        "float_open_by": row["float_open_by"], "float_open_at": row["float_open_at"],
        "float_close": float_close, "float_close_total": _deno_total(float_close),
        "float_close_by": row["float_close_by"], "float_close_at": row["float_close_at"],
        "npr_collect": npr, "npr_total": _deno_total(npr), "fcy_collect": fcy, "fcy_npr_total": fcy_npr,
        "collection_by": row["collection_by"], "collection_at": row["collection_at"],
        "total_collected": collected, "commission": comm, "net_cash": round(collected - comm, 2),
        "saved": saved, "editable": editable,
        "system": sys_crew,
        "system_suggestion": {
            "fcy": [{"currency": c, "amount": v["amount"], "rate": v["rate"]}
                    for c, v in sys_crew["currencies"].items() if c != "NPR"],
            "npr_total": sys_crew["currencies"].get("NPR", {}).get("amount", 0.0),
        },
    }


def _differences(block):
    """Saved collection against the phones' record, currency by currency."""
    if not block["saved"]["collection"]:
        return []
    entered = {"NPR": block["npr_total"]}
    for x in block["fcy_collect"]:
        code = str(x.get("currency") or "").upper()
        entered[code] = entered.get(code, 0.0) + float(x.get("amount") or 0)
    system = {c: v["amount"] for c, v in block["system"]["currencies"].items()}
    out = []
    for code in sorted(set(entered) | set(system)):
        a, b = round(entered.get(code, 0.0), 2), round(system.get(code, 0.0), 2)
        if abs(a - b) > TOLERANCE:
            out.append({"currency": code, "entered": a, "system": b, "difference": round(a - b, 2)})
    return out


def _render(connection, flight, user):
    sheet = _sheet(connection, flight["id"])
    system = _system_collection(connection, flight["id"])
    commission, comm_data = _commission_by_account(connection, flight["id"])
    rows = connection.execute("SELECT * FROM cash_sheet_crew WHERE sheet_id=? ORDER BY id",
                              (sheet["id"],)).fetchall() if sheet else []
    status = sheet["status"] if sheet else "OPEN"
    blocks = [_block(r, system, commission, flight["status"], status, user) for r in rows]
    # Rostered crew without a block yet are offered as empty blocks to start.
    have = {b["crew_account"] for b in blocks}
    names = {r["employee_id"]: (r["name"], r["crew_code"]) for r in connection.execute(
        """SELECT u.employee_id, u.name, m.crew_code FROM users u
           LEFT JOIN crew_members m ON m.id = u.crew_member_id""")}
    pending = [{"crew_account": a, "crew_name": names.get(a, (a, None))[0], "staff_no": names.get(a, (None, a))[1] or a}
               for a in _rostered(connection, flight["id"]) if a not in have]
    uploaded = _all_uploaded(connection, flight)
    for b in blocks:
        b["differences"] = _differences(b) if uploaded else []
    # Crew who sold but have no block are part of the reconciliation too.
    unblocked = [{"crew_account": a, **v} for a, v in system.items() if a not in have]
    totals = {
        "float_open": sum(b["float_open_total"] for b in blocks),
        "float_close": sum(b["float_close_total"] for b in blocks),
        "collected": round(sum(b["total_collected"] for b in blocks), 2),
        "commission": round(sum(b["commission"] for b in blocks), 2),
        "net_cash": round(sum(b["net_cash"] for b in blocks), 2),
        "system_npr_value": round(sum(v["npr_value"] for v in system.values()), 2),
        "sector_commission": comm_data["total_commission_npr"],
    }
    events = [dict(r) for r in connection.execute(
        "SELECT * FROM cash_sheet_events WHERE sheet_id=? ORDER BY id DESC", (sheet["id"],))] if sheet else []
    alerts = [dict(r) for r in connection.execute(
        "SELECT * FROM cash_alerts WHERE sheet_id=? ORDER BY id DESC", (sheet["id"],))] if sheet else []
    perms = permissions_for(user["role"])
    return {
        "flight": {k: flight[k] for k in ("id", "flight_number", "flight_date", "origin", "destination", "direction",
                                          "trip_type", "status")},
        "sheet_id": sheet["id"] if sheet else None, "sn": sheet["id"] if sheet else None,
        "status": status,
        "approved_by": sheet["approved_by"] if sheet else None, "approved_at": sheet["approved_at"] if sheet else None,
        "approval_remarks": sheet["approval_remarks"] if sheet else None,
        "denominations": list(DENOMINATIONS), "commission_rate": COMMISSION_RATE,
        "blocks": blocks, "pending_crew": pending, "unblocked_sales": unblocked,
        "all_uploaded": uploaded, "waiting_for": [] if uploaded else [
            a for a in _rostered(connection, flight["id"]) if a not in {r["device"] for r in connection.execute(
                "SELECT device FROM trip_device_state WHERE flight_id=? AND finished_at IS NOT NULL", (flight["id"],))}],
        "totals": totals, "events": events, "alerts": alerts,
        "may_fill": _may_fill(user), "may_approve": "cash_approve" in perms and status != "APPROVED",
        "is_admin": _is_admin(user),
    }


def reconcile(connection, flight, by="system"):
    """Open or close the difference notices of a sector. Commits."""
    sheet = _sheet(connection, flight["id"])
    if not sheet or not _all_uploaded(connection, flight):
        return
    system = _system_collection(connection, flight["id"])
    commission, _ = _commission_by_account(connection, flight["id"])
    fake_user = {"role": "admin", "employee_id": by}
    for row in connection.execute("SELECT * FROM cash_sheet_crew WHERE sheet_id=?", (sheet["id"],)).fetchall():
        block = _block(row, system, commission, flight["status"], sheet["status"], fake_user)
        diffs = _differences(block)
        open_alert = connection.execute(
            "SELECT * FROM cash_alerts WHERE sheet_id=? AND crew_account=? AND closed_at IS NULL",
            (sheet["id"], row["crew_account"])).fetchone()
        if diffs:
            parts = "; ".join(f"{d['currency']} entered {d['entered']:g}, phones recorded {d['system']:g}"
                              for d in diffs)
            message = (f"Cash for {flight['flight_number']} {flight['origin']}-{flight['destination']} "
                       f"{flight['flight_date']} ({row['crew_name']}) does not match the sales: {parts}.")
            if open_alert:
                if open_alert["message"] != message:
                    connection.execute("UPDATE cash_alerts SET message=? WHERE id=?", (message, open_alert["id"]))
            else:
                # A notice the administrator already marked fixed is not raised
                # again for the very same figures.
                fixed = connection.execute(
                    "SELECT 1 FROM cash_alerts WHERE sheet_id=? AND crew_account=? AND message=? "
                    "AND close_reason='FIXED'", (sheet["id"], row["crew_account"], message)).fetchone()
                if not fixed:
                    connection.execute(
                        "INSERT INTO cash_alerts(sheet_id,flight_id,crew_account,message,created_at) VALUES (?,?,?,?,?)",
                        (sheet["id"], flight["id"], row["crew_account"], message, _now()))
                    _event(connection, sheet["id"], "system", "MISMATCH", row["crew_account"], {"differences": diffs})
        elif open_alert:
            connection.execute(
                "UPDATE cash_alerts SET closed_at=?, closed_by='system', close_reason='MATCHED' WHERE id=?",
                (_now(), open_alert["id"]))
            _event(connection, sheet["id"], "system", "MATCHED", row["crew_account"], None)
    connection.commit()


def _event(connection, sheet_id, by, action, crew_account=None, detail=None, remarks=None):
    connection.execute(
        "INSERT INTO cash_sheet_events(sheet_id,at,by,action,crew_account,detail,remarks) VALUES (?,?,?,?,?,?,?)",
        (sheet_id, _now(), by, action, crew_account,
         json.dumps(detail, ensure_ascii=False) if detail is not None else None, remarks))


@router.get("/cash/sheets")
def list_sheets(date: str | None = None, date_to: str | None = None, flight_number: str | None = None,
                status: str | None = None, page: int | None = PageParam, page_size: int = PageSizeParam,
                user: dict = Depends(current_user)):
    """Sectors with their cash sheet state, newest first. Crew: their own sectors."""
    if not _may_view(user):
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action.")
    conditions, params = [], []
    if date:
        conditions.append("f.flight_date >= ?" if date_to else "f.flight_date = ?")
        params.append(validate_date(date, "date"))
    if date_to:
        conditions.append("f.flight_date <= ?")
        params.append(validate_date(date_to, "date"))
    if flight_number:
        conditions.append("f.flight_number LIKE ?")
        params.append(f"%{flight_number.strip().upper()[:12]}%")
    if user["role"] in CREW_ROLES:
        conditions.append("f.id IN (SELECT flight_id FROM flight_assignments WHERE crew_account=?)")
        params.append(user["employee_id"])
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""SELECT f.id, f.flight_number, f.flight_date, f.origin, f.destination, f.direction, f.status,
                       c.id AS sheet_id, COALESCE(c.status, 'NONE') AS sheet_status, c.approved_by,
                       (SELECT COUNT(*) FROM cash_sheet_crew b WHERE b.sheet_id=c.id) AS blocks,
                       (SELECT COUNT(*) FROM cash_sheet_crew b WHERE b.sheet_id=c.id AND b.collection_at IS NOT NULL) AS collections,
                       (SELECT COUNT(*) FROM cash_alerts a WHERE a.sheet_id=c.id AND a.closed_at IS NULL) AS open_alerts,
                       (SELECT COUNT(*) FROM cash_alerts a WHERE a.sheet_id=c.id) AS all_alerts,
                       (SELECT ROUND(COALESCE(SUM(s.npr_amount),0),2) FROM sales s
                         WHERE s.flight_id=f.id AND s.sale_type='NORMAL') AS sales_npr
                FROM flights f LEFT JOIN cash_sheets c ON c.flight_id=f.id
                {where} ORDER BY f.flight_date DESC, f.flight_number, f.direction""", params).fetchall()
    finally:
        connection.close()
    items = [dict(r) for r in rows]
    if status:
        wanted = status.strip().upper()
        items = [i for i in items if i["sheet_status"] == wanted
                 or (wanted == "MISMATCH" and i["open_alerts"])]
    if page:
        return paginate_list(items, page, page_size)
    return items


@router.get("/cash/flights/{flight_id}")
def get_sheet(flight_id: int, user: dict = Depends(current_user)):
    connection = get_connection()
    try:
        flight = _flight(connection, flight_id)
        _assert_access(connection, flight, user)
        reconcile(connection, flight)
        return _render(connection, flight, user)
    finally:
        connection.close()


class Denominations(BaseModel):
    values: dict[str, int] = {}


class FcyLine(BaseModel):
    currency: str = Field(min_length=3, max_length=3)
    amount: float = Field(ge=0, le=10_000_000)
    rate: float = Field(gt=0, le=100_000)
    deno: float | None = Field(default=None, gt=0, le=100_000)
    qty: int | None = Field(default=None, gt=0, le=100_000)


class BlockIn(BaseModel):
    crew_account: str = Field(min_length=1, max_length=40)
    crew_name: str | None = Field(default=None, max_length=80)
    staff_no: str | None = Field(default=None, max_length=20)
    section: str
    denominations: dict[str, int] = {}
    fcy: list[FcyLine] = []
    remarks: str | None = Field(default=None, max_length=500)


def _clean_denominations(values: dict) -> dict:
    out = {}
    for key, qty in (values or {}).items():
        if not str(key).isdigit() or int(key) not in DENOMINATIONS:
            raise HTTPException(status_code=400, detail=f"{key} is not an NPR note on the sheet.")
        qty = int(qty or 0)
        if qty < 0 or qty > 100_000:
            raise HTTPException(status_code=400, detail="A note count must be between 0 and 100,000.")
        if qty:
            out[str(int(key))] = qty
    return out


@router.put("/cash/flights/{flight_id}/blocks")
def save_block(flight_id: int, data: BlockIn, user: dict = Depends(current_user)):
    """Save one part of a crew member's block (opening float, closing float or
    the collection). The first save is open to anyone who fills cash sheets;
    after that only the administrator may change it, with remarks."""
    section = data.section.strip().lower()
    if section not in SECTIONS:
        raise HTTPException(status_code=400, detail="Unknown part of the cash sheet.")
    account = data.crew_account.strip().upper()
    remarks = (data.remarks or "").strip() or None
    connection = get_connection()
    try:
        flight = _flight(connection, flight_id)
        _assert_access(connection, flight, user, write=True)
        rostered = _rostered(connection, flight_id)
        if user["role"] in CREW_ROLES and account != user["employee_id"]:
            raise HTTPException(status_code=403, detail="A crew member fills their own block only.")
        sheet = _sheet(connection, flight_id, create_by=user["employee_id"])
        if sheet["status"] == "APPROVED" and not _is_admin(user):
            raise HTTPException(status_code=400, detail="This cash sheet is approved. Only the administrator can change it.")
        row = connection.execute("SELECT * FROM cash_sheet_crew WHERE sheet_id=? AND crew_account=?",
                                 (sheet["id"], account)).fetchone()
        if not row:
            known = connection.execute(
                """SELECT u.name, m.crew_code FROM users u LEFT JOIN crew_members m ON m.id=u.crew_member_id
                   WHERE u.employee_id=?""", (account,)).fetchone()
            if account not in rostered and not known and not (data.crew_name or "").strip():
                raise HTTPException(status_code=400, detail="Enter the crew member's name.")
            name = (data.crew_name or "").strip() or (known["name"] if known else account)
            staff = (data.staff_no or "").strip() or (known["crew_code"] if known and known["crew_code"] else account)
            connection.execute(
                "INSERT INTO cash_sheet_crew(sheet_id,crew_account,crew_name,staff_no) VALUES (?,?,?,?)",
                (sheet["id"], account, name[:80], staff[:20]))
            row = connection.execute("SELECT * FROM cash_sheet_crew WHERE sheet_id=? AND crew_account=?",
                                     (sheet["id"], account)).fetchone()
        saved_at = {"float_open": row["float_open_at"], "float_close": row["float_close_at"],
                    "collection": row["collection_at"]}[section]
        correcting = bool(saved_at) or sheet["status"] == "APPROVED"
        if correcting:
            if not _is_admin(user):
                raise HTTPException(status_code=400,
                                    detail="This part has been saved. Only the administrator can change it now.")
            if not remarks:
                raise HTTPException(status_code=400, detail="Remarks are mandatory when changing a saved part.")
        now = _now()
        before = {"float_open": row["float_open"], "float_close": row["float_close"],
                  "npr_collect": row["npr_collect"], "fcy_collect": row["fcy_collect"]}
        if section in ("float_open", "float_close"):
            values = _clean_denominations(data.denominations)
            connection.execute(
                f"UPDATE cash_sheet_crew SET {section}=?, {section}_by=?, {section}_at=? WHERE id=?",
                (json.dumps(values), user["employee_id"], now, row["id"]))
            detail = {"values": values, "total": _deno_total(values)}
        else:
            values = _clean_denominations(data.denominations)
            fcy = []
            for line in data.fcy:
                code = line.currency.strip().upper()
                if not code.isalpha() or code == "NPR":
                    raise HTTPException(status_code=400, detail="Foreign currency lines take a foreign currency code.")
                amount = line.amount or ((line.deno or 0) * (line.qty or 0))
                if amount:
                    fcy.append({"currency": code, "deno": line.deno, "qty": line.qty,
                                "amount": round(amount, 2), "rate": round(line.rate, 6),
                                "npr": round(amount * line.rate, 2)})
            connection.execute(
                "UPDATE cash_sheet_crew SET npr_collect=?, fcy_collect=?, collection_by=?, collection_at=? WHERE id=?",
                (json.dumps(values), json.dumps(fcy), user["employee_id"], now, row["id"]))
            detail = {"npr": values, "npr_total": _deno_total(values), "fcy": fcy}
        if data.staff_no and data.staff_no.strip() and correcting is False:
            connection.execute("UPDATE cash_sheet_crew SET staff_no=? WHERE id=?", (data.staff_no.strip()[:20], row["id"]))
        if remarks:
            connection.execute("UPDATE cash_sheet_crew SET remarks=? WHERE id=?", (remarks, row["id"]))
        if correcting:
            detail["before"] = before
        _event(connection, sheet["id"], user["employee_id"], "CORRECTED" if correcting else "SAVED", account,
               {"section": section, **detail}, remarks)
        connection.commit()
        reconcile(connection, flight, by=user["employee_id"])
        result = _render(connection, flight, user)
    finally:
        connection.close()
    log_action(user, "CASH", "CORRECT" if correcting else "SAVE",
               f"{'Corrected' if correcting else 'Saved'} the {SECTION_LABELS[section]} of {account} on "
               f"{flight['flight_number']} {flight['flight_date']}" + (f". Remarks: {remarks}" if remarks else "."),
               "flight", flight_id, metadata={"section": section, "crew_account": account})
    return result


class ApproveIn(BaseModel):
    remarks: str | None = Field(default=None, max_length=500)


@router.post("/cash/flights/{flight_id}/approve")
def approve_sheet(flight_id: int, data: ApproveIn, user: dict = Depends(requires("cash_approve"))):
    connection = get_connection()
    try:
        flight = _flight(connection, flight_id)
        sheet = _sheet(connection, flight_id)
        if not sheet:
            raise HTTPException(status_code=400, detail="Nothing has been entered on this cash sheet yet.")
        if sheet["status"] == "APPROVED":
            raise HTTPException(status_code=400, detail="This cash sheet is already approved.")
        unsaved = connection.execute(
            "SELECT COUNT(*) c FROM cash_sheet_crew WHERE sheet_id=? AND collection_at IS NULL", (sheet["id"],)).fetchone()["c"]
        if unsaved:
            raise HTTPException(status_code=400, detail=f"{unsaved} crew block(s) have no saved collection yet.")
        open_alerts = connection.execute(
            "SELECT COUNT(*) c FROM cash_alerts WHERE sheet_id=? AND closed_at IS NULL", (sheet["id"],)).fetchone()["c"]
        remarks = (data.remarks or "").strip() or None
        if open_alerts and not remarks:
            raise HTTPException(status_code=400,
                                detail="The cash does not match the sales. Explain the difference in the remarks to approve.")
        now = _now()
        connection.execute("UPDATE cash_sheets SET status='APPROVED', approved_by=?, approved_at=?, approval_remarks=? "
                           "WHERE id=?", (user["employee_id"], now, remarks, sheet["id"]))
        _event(connection, sheet["id"], user["employee_id"], "APPROVED", None, {"open_differences": open_alerts}, remarks)
        connection.commit()
        result = _render(connection, flight, user)
    finally:
        connection.close()
    log_action(user, "CASH", "APPROVE", f"Approved the cash sheet of {flight['flight_number']} {flight['flight_date']}"
               + (f". Remarks: {remarks}" if remarks else "."), "flight", flight_id)
    return result


@router.get("/cash/alerts")
def open_alerts(user: dict = Depends(current_user)):
    """The notices shown at the top of the app: cash that does not match."""
    if not _may_view(user):
        return []
    connection = get_connection()
    try:
        sql = """SELECT a.*, f.flight_number, f.flight_date, f.origin, f.destination
                 FROM cash_alerts a JOIN flights f ON f.id=a.flight_id WHERE a.closed_at IS NULL"""
        params = []
        if user["role"] in CREW_ROLES:
            sql += " AND a.flight_id IN (SELECT flight_id FROM flight_assignments WHERE crew_account=?)"
            params.append(user["employee_id"])
        return [dict(r) for r in connection.execute(sql + " ORDER BY a.id DESC LIMIT 20", params)]
    finally:
        connection.close()


class FixIn(BaseModel):
    remarks: str = Field(min_length=2, max_length=500)


@router.post("/cash/alerts/{alert_id}/fix")
def fix_alert(alert_id: int, data: FixIn, user: dict = Depends(requires("users"))):
    """Administrator: the difference is a system mistake. The notice goes; the
    record stays in the sheet's history."""
    connection = get_connection()
    try:
        alert = connection.execute("SELECT * FROM cash_alerts WHERE id=?", (alert_id,)).fetchone()
        if not alert:
            raise HTTPException(status_code=404, detail="Notice not found.")
        if alert["closed_at"]:
            raise HTTPException(status_code=400, detail="This notice is already closed.")
        connection.execute(
            "UPDATE cash_alerts SET closed_at=?, closed_by=?, close_reason='FIXED', close_remarks=? WHERE id=?",
            (_now(), user["employee_id"], data.remarks.strip(), alert_id))
        _event(connection, alert["sheet_id"], user["employee_id"], "FIXED", alert["crew_account"],
               {"message": alert["message"]}, data.remarks.strip())
        connection.commit()
    finally:
        connection.close()
    log_action(user, "CASH", "FIX_NOTICE", f"Marked fixed: {alert['message']} Remarks: {data.remarks.strip()}",
               "flight", alert["flight_id"])
    return {"ok": True}


@router.get("/cash/flights/{flight_id}/print", response_class=HTMLResponse)
def print_sheet(flight_id: int, user: dict = Depends(current_user)):
    """The sheet laid out like the paper form, for printing."""
    connection = get_connection()
    try:
        flight = _flight(connection, flight_id)
        _assert_access(connection, flight, user)
        data = _render(connection, flight, user)
    finally:
        connection.close()
    e = lambda v: html.escape("" if v is None else str(v))  # noqa: E731
    money = lambda v: f"{float(v or 0):,.2f}"  # noqa: E731

    def deno_table(title, values, total):
        rows = "".join(f"<tr><td>{d}</td><td>{e(values.get(str(d), '') or '')}</td>"
                       f"<td>{money(d * int(values.get(str(d), 0) or 0)) if values.get(str(d)) else ''}</td></tr>"
                       for d in DENOMINATIONS)
        return (f"<table><caption>{e(title)}</caption><tr><th>Deno</th><th>Qty</th><th>Amount</th></tr>{rows}"
                f"<tr class='t'><td colspan=2>Total</td><td>{money(total)}</td></tr></table>")

    def staff(b):
        return f"<p class=sig>Name/Staff No. <b>{e(b['crew_name'])}</b> ({e(b['staff_no'])}) &nbsp; Signature: ____________</p>"

    # Laid out as the paper form: the floating cash of every crew side by
    # side, then each crew's NPR and FCY collection side by side.
    floats = "".join(f"""<div class=crew><h3>Crew {i}</h3><div class=row>
        {deno_table('Opening Balance', b['float_open'], b['float_open_total'])}
        {deno_table('Closing Balance', b['float_close'], b['float_close_total'])}</div>{staff(b)}</div>"""
                     for i, b in enumerate(data["blocks"], start=1))
    blocks = []
    for i, b in enumerate(data["blocks"], start=1):
        fcy = "".join(f"<tr><td class=l>{e(x['currency'])}</td><td>{e(x.get('deno') or '')}</td><td>{e(x.get('qty') or '')}</td>"
                      f"<td>{money(x['amount'])}</td><td>{e(x['rate'])}</td>"
                      f"<td>{money(x['amount'] * x['rate'])}</td></tr>" for x in b["fcy_collect"])
        fcy += "<tr><td>&nbsp;</td><td></td><td></td><td></td><td></td><td></td></tr>" * max(0, 3 - len(b["fcy_collect"]))
        diffs = "".join(f"<li>{e(d['currency'])}: entered {d['entered']:g}, phones recorded {d['system']:g}</li>"
                        for d in b["differences"])
        blocks.append(f"""
        <div class=crew><h3>Crew {i}</h3><div class=row>
        {deno_table('Currency: NPR', b['npr_collect'], b['npr_total'])}
        <div><table><caption>Currency (FCY)</caption><tr><th>Currency</th><th>Deno</th><th>Qty</th><th>Amount</th>
        <th>Exchange Rate</th><th>NPR Currency</th></tr>
        {fcy}<tr class='t'><td colspan=5>Total</td><td>{money(b['fcy_npr_total'])}</td></tr></table>
        <table class=sum><tr><td class=l>Total Cash Collected (NPR)</td><td><b>{money(b['total_collected'])}</b></td></tr>
        <tr><td class=l>Crew Commission (10%)</td><td>{money(b['commission'])}</td></tr>
        <tr><td class=l>Net Cash</td><td><b>{money(b['net_cash'])}</b></td></tr></table></div></div>
        {staff(b)}
        {f"<ul class=diff>{diffs}</ul>" if diffs else ''}</div>""")
    f = data["flight"]
    t = data["totals"]
    page = f"""<!doctype html><html><head><meta charset=utf-8><title>Cash sheet {e(f['flight_number'])} {e(f['flight_date'])}</title>
<style>body{{font-family:Arial,sans-serif;font-size:12px;margin:18px;color:#111}}h1{{font-size:18px;margin:0}}
h2{{font-size:14px;margin:14px 0 6px}}small{{font-weight:normal;color:#555}}.row{{display:flex;gap:10px;flex-wrap:wrap}}
table{{border-collapse:collapse;margin-bottom:6px}}caption{{font-weight:bold;text-align:left;padding:2px 0}}
td,th{{border:1px solid #333;padding:3px 7px;text-align:right;min-width:42px}}th{{background:#eee}}
tr.t td{{font-weight:bold}}.tot{{margin:4px 0}}.diff{{color:#b00}}.sig{{color:#555}}section{{page-break-inside:avoid;border-top:1px solid #999;padding-top:6px}}
.head{{display:flex;justify-content:space-between}}h3{{font-size:13px;margin:8px 0 4px}}
.crew{{margin-right:18px}}td.l{{text-align:left}}table.sum td{{border:none;padding:2px 7px}}</style></head><body>
<div class=head><div><h1>Cash Sheet</h1>
<div>{e(f['flight_number'])} &middot; {e(f['origin'])}-{e(f['destination'])} &middot; {e(f['flight_date'])}</div></div>
<div>S.N {e(data['sn'] or '-')}<br>Status: {e(data['status'])}{(' by ' + e(data['approved_by'])) if data['approved_by'] else ''}</div></div>
<section><h2>FLOATING CASH BALANCE (NPR)</h2><div class=row>{floats or '<p>No crew entered yet.</p>'}</div></section>
<section><h2>CASH SALES COLLECTION</h2>{''.join(blocks) or '<p>No crew entered yet.</p>'}</section>
<section><h2>Grand Total ({' + '.join(f'Crew {i}' for i in range(1, len(data['blocks']) + 1)) or 'all crew'})</h2><p class=tot>Total cash collected (NPR): <b>{money(t['collected'])}</b> &nbsp;
Crew commission: <b>{money(t['commission'])}</b> &nbsp; Net cash: <b>{money(t['net_cash'])}</b> &nbsp;
Sales recorded on the phones (NPR value): <b>{money(t['system_npr_value'])}</b></p>
<p>Finance staff: ______________________ &nbsp; Signature: ______________________</p></section>
<script>window.onload=function(){{window.print()}}</script></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})
