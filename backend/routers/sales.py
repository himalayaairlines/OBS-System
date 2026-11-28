"""Sales capture, offline sync and management corrections.

A passenger transaction is one `order_ref` covering one seat. It may hold
several lines, each a product or a bundle. Bundle revenue is allocated across
its components pro rata so product-level reporting stays accurate.

Sale types:
  NORMAL        -> revenue recorded, stock consumed
  COMPLEMENTARY -> no revenue, stock consumed (it left the trolley)
  CANCELLATION  -> no revenue, no stock consumed (the sale did not happen)
"""

import json
import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import math

from database import AIRCRAFT_TYPES, COMMISSION_RATE, CREW_ROLES, get_connection
import trip_flow
from paging import PageParam, PageSizeParam, paginate_list, paginate_sql
from security import (
    current_user, log_action, now_iso, permissions_for, rate_for, requires, trip_root,
    validate_date,
)

router = APIRouter(tags=["sales"])

SALE_TYPES = {"NORMAL", "COMPLEMENTARY", "CANCELLATION"}
SEAT_RE = re.compile(r"^(?:[1-9]|[1-9][0-9])([A-K])$", re.I)


def valid_seat(seat: str, aircraft_type: str | None) -> bool:
    """A seat must exist on the aircraft actually flying the leg."""
    match = SEAT_RE.match(seat)
    if not match:
        return False
    spec = AIRCRAFT_TYPES.get((aircraft_type or "").upper())
    if not spec:
        return True  # aircraft not recorded yet; fall back to the generic pattern
    row = int(seat[:-1])
    letters = {letter for block in spec["layout"] for letter in block}
    return 1 <= row <= spec["rows"] and seat[-1].upper() in letters


class SaleLine(BaseModel):
    product_id: int | None = None
    bundle_id: int | None = None
    quantity: int = Field(gt=0)


class DeviceEdit(BaseModel):
    """A correction the crew made to a queued sale on the iPad before it
    uploaded (7.14). Kept in the sale's history like any other correction."""
    at: str
    remarks: str = Field(min_length=1, max_length=500)
    before: dict = {}
    after: dict = {}


class SaleTransaction(BaseModel):
    order_ref: str = Field(min_length=6, max_length=64)
    flight_id: int
    seat_number: str
    # 7.11: normal sales pay into the sector's pooled commission, so no seller
    # is picked. A combo (bundle) sale names the crew member who sold it, and
    # that person earns the combo commission.
    sold_by_crew_member_id: int | None = None
    lines: list[SaleLine]
    payment_currency: str = "NPR"
    payment_method: str = "Cash"
    sale_type: str = "NORMAL"
    remarks: str | None = None
    transaction_time: str
    device_edits: list[DeviceEdit] = []
    # 7.18: what the passenger paid in the chosen currency, as the phone showed
    # it (the NPR price converted and rounded UP to a whole unit). Kept as the
    # cash actually collected; worked out here when the phone does not send it.
    payment_amount: float | None = Field(default=None, ge=0, le=10_000_000)


class SyncRequest(BaseModel):
    transactions: list[SaleTransaction]


class SaleEdit(BaseModel):
    product_id: int | None = None
    quantity: int | None = Field(default=None, gt=0)
    seat_number: str | None = None
    # Management may reattribute a sale recorded against the wrong crew member.
    sold_by_crew_member_id: int | None = None
    edit_remarks: str


def currency_amount(npr: float, rate: float, currency: str) -> float:
    """7.18: what the passenger pays in `currency` for an NPR price: converted
    at the day's rate and rounded UP to the next whole unit (3.0 -> 3,
    3.1 -> 4, 2.5 -> 3). NPR itself is charged as priced."""
    if not npr or npr <= 0:
        return 0.0
    if currency == "NPR" or not rate:
        return round(npr, 2)
    # The epsilon keeps an exact conversion (e.g. 3.0000000001 from float
    # division) from being pushed to the next unit.
    return float(math.ceil(npr / rate - 1e-9))


def _currency_split(line_nprs: list[float], rate: float, currency: str, paid: float | None) -> list[float]:
    """Spread the transaction's currency amount over its lines so the lines add
    up to exactly what was collected."""
    total_npr = sum(line_nprs)
    total = currency_amount(total_npr, rate, currency)
    if paid is not None and total_npr > 0:
        total = round(float(paid), 2)
    if not line_nprs:
        return []
    if total_npr <= 0:
        return [0.0] * len(line_nprs)
    out, allocated = [], 0.0
    for index, npr in enumerate(line_nprs):
        if index == len(line_nprs) - 1:
            out.append(round(total - allocated, 2))
        else:
            share = round(total * npr / total_npr, 2)
            out.append(share)
            allocated += share
    return out


def _group_from_roster(connection, flight, user):
    """A duty group made from the crew Inventory rostered on the sector, for a
    sale that arrives before any phone entered the names (7.18)."""
    members = [r["id"] for r in connection.execute(
        """SELECT DISTINCT m.id FROM flight_assignments a
           JOIN users u ON u.employee_id = a.crew_account
           JOIN crew_members m ON m.id = u.crew_member_id
           WHERE a.flight_id=? AND m.active=1""", (flight["id"],))]
    if not members:
        return None
    label = (f"{flight['flight_number']} / {flight['flight_date']} / "
             f"{'OUT' if flight['direction'] == 'OUTBOUND' else 'RET'}")
    try:
        group_id = connection.execute(
            "INSERT INTO duty_groups(flight_id,client_ref,label,created_by,created_at) VALUES (?,?,?,?,?)",
            (flight["id"], f"ROSTER-{flight['id']}", label, f"ROSTER ({user['employee_id']})", now_iso())).lastrowid
    except sqlite3.IntegrityError:
        return connection.execute("SELECT * FROM duty_groups WHERE flight_id=?", (flight["id"],)).fetchone()
    for member_id in members:
        connection.execute("INSERT INTO duty_group_members(duty_group_id,crew_member_id) VALUES (?,?)",
                           (group_id, member_id))
    return connection.execute("SELECT * FROM duty_groups WHERE id=?", (group_id,)).fetchone()


class PermanentError(Exception):
    """A failure that will never succeed on retry, so the client must stop queueing it."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #
def _expand_lines(connection, transaction: SaleTransaction):
    """Turn lines into concrete (product, quantity, npr_amount, bundle_id) rows."""
    expanded = []
    for line in transaction.lines:
        if bool(line.product_id) == bool(line.bundle_id):
            raise PermanentError("Each line must reference either a product or a bundle.")

        if line.product_id:
            product = connection.execute(
                "SELECT * FROM products WHERE id=? AND active=1", (line.product_id,)
            ).fetchone()
            if not product:
                raise PermanentError("The product is no longer available.")
            expanded.append({
                "product_id": product["id"],
                "bundle_id": None,
                "quantity": line.quantity,
                "unit_price": float(product["selling_price"]),
                "npr_amount": round(float(product["selling_price"]) * line.quantity, 2),
            })
            continue

        bundle = connection.execute(
            "SELECT * FROM bundles WHERE id=? AND active=1", (line.bundle_id,)
        ).fetchone()
        if not bundle:
            raise PermanentError("The bundle is no longer available.")
        items = connection.execute(
            """SELECT bi.product_id, bi.quantity, p.selling_price
               FROM bundle_items bi JOIN products p ON p.id = bi.product_id
               WHERE bi.bundle_id=? AND p.active=1 ORDER BY bi.id""",
            (line.bundle_id,),
        ).fetchall()
        if not items:
            raise PermanentError("The bundle has no active products.")

        list_value = sum(float(i["selling_price"]) * i["quantity"] for i in items)
        bundle_total = round(float(bundle["bundle_price"]) * line.quantity, 2)
        allocated = 0.0
        for index, item in enumerate(items):
            quantity = item["quantity"] * line.quantity
            if index == len(items) - 1:
                amount = round(bundle_total - allocated, 2)
            else:
                share = (float(item["selling_price"]) * item["quantity"]) / list_value if list_value else 0
                amount = round(bundle_total * share, 2)
                allocated += amount
            expanded.append({
                "product_id": item["product_id"],
                "bundle_id": bundle["id"],
                "quantity": quantity,
                "unit_price": round(amount / quantity, 4) if quantity else 0,
                "npr_amount": amount,
            })
    return expanded


def _record(connection, transaction: SaleTransaction, user: dict, replay: bool = False):
    """Write one passenger transaction. Raises PermanentError for unretryable input.

    `replay` marks an offline upload (7.12): the sale was taken on board before
    the iPad finished the sector, so the per-iPad "started / not finished"
    checks do not apply to it, and a leg Management has since finished still
    accepts it. Only a CLOSED (reconciled) flight refuses it.
    """
    if connection.execute(
        "SELECT 1 FROM sales WHERE order_ref=? LIMIT 1", (transaction.order_ref,)
    ).fetchone():
        return {"status": "already_recorded", "order_ref": transaction.order_ref}

    seat = transaction.seat_number.strip().upper()

    sale_type = transaction.sale_type.strip().upper()
    if sale_type not in SALE_TYPES:
        raise PermanentError("Invalid sale type.")
    remarks = (transaction.remarks or "").strip() or None
    # There is one cabin account and it may raise any sale type, so what keeps
    # a complementary or a cancellation honest is the mandatory reason below
    # plus the audit trail, not a role check.
    if sale_type != "NORMAL" and not remarks:
        raise PermanentError("Remarks are mandatory for complementary and cancellation transactions.")
    if not transaction.lines:
        raise PermanentError("The transaction has no items.")

    flight = connection.execute(
        "SELECT * FROM flights WHERE id=?", (transaction.flight_id,)
    ).fetchone()
    if not flight:
        raise PermanentError("Flight not found.")
    if flight["status"] == "FLOWN" and not replay:
        raise PermanentError(
            "Selling has been finished on this leg. Switch to the return flight."
        )
    if flight["status"] not in ("OPEN", "FLOWN"):
        raise PermanentError("This flight has been closed and reconciled.")
    accounts = [r["crew_account"] for r in connection.execute(
        "SELECT crew_account FROM flight_assignments WHERE flight_id=?", (transaction.flight_id,))]
    if replay:
        pass  # taken on board before this iPad finished; accepted as recorded
    elif user["employee_id"] in accounts and "sell_any_flight" not in permissions_for(user["role"]):
        # 7.12: an iPad sells on its own timetable. It must have started the leg
        # itself and not yet finished it; the other iPad does not come into it.
        try:
            trip_flow.assert_device_may_sell(connection, flight, user["employee_id"])
        except HTTPException as exc:
            raise PermanentError(exc.detail)
    elif not trip_flow.leg_started(flight):
        raise PermanentError("Start the second flight on the Sales page before selling on it.")
    if not flight["passengers"]:
        raise PermanentError("The PAX load has not been entered for this flight.")
    if not valid_seat(seat, flight["aircraft_type"]):
        raise PermanentError(
            f"Seat {seat} does not exist on a {flight['aircraft_type'] or 'this'} aircraft."
        )

    if "sell_any_flight" not in permissions_for(user["role"]):
        if not accounts:
            raise PermanentError("No crew is rostered on this sector.")
        if user["employee_id"] not in accounts:
            raise PermanentError("You are not rostered on that sector.")

    # The sale belongs to the sector's duty group: the pooled commission is
    # split across its members. Without a group there is nobody to pay, so
    # selling cannot start until the names have been entered.
    duty_group = connection.execute(
        "SELECT * FROM duty_groups WHERE flight_id=?", (transaction.flight_id,)
    ).fetchone()
    if not duty_group and (replay or user["role"] in CREW_ROLES):
        # 7.18: the names are entered on one phone; a phone that sold before it
        # ever saw them still uploads. The group starts from Inventory's roster.
        duty_group = _group_from_roster(connection, flight, user)
    if not duty_group:
        raise PermanentError(
            "No duty group has been formed for this sector. Enter the crew names before selling."
        )
    has_combo = any(line.bundle_id for line in transaction.lines)
    seller = None
    own = None
    if user["role"] in CREW_ROLES:
        # 7.18: every crew member sells on their own login, so the seller is
        # whoever is signed in — for products (sales ranking) and combos
        # (combo commission) alike. Nothing to pick at the till.
        from crew_accounts import crew_member_for_user
        own = crew_member_for_user(connection, user)
    if own is not None:
        seller = {"id": own["id"], "name": own["name"]}
    elif transaction.sold_by_crew_member_id:
        seller = connection.execute(
            """SELECT m.id, m.name FROM duty_group_members dm
               JOIN crew_members m ON m.id = dm.crew_member_id
               WHERE dm.duty_group_id=? AND dm.crew_member_id=?""",
            (duty_group["id"], transaction.sold_by_crew_member_id),
        ).fetchone()
        if not seller:
            raise PermanentError("The selected crew member is not in this sector's duty group.")
    elif has_combo:
        raise PermanentError("Choose the crew member who sold the combo. Combo commission is paid to that person.")

    # The membership now backs a real sale, so it stops being freely editable.
    if not duty_group["locked"]:
        connection.execute(
            "UPDATE duty_groups SET locked=1, locked_at=? WHERE id=?",
            (now_iso(), duty_group["id"]),
        )

    rate = rate_for(transaction.payment_currency, transaction.transaction_time, connection)
    expanded = _expand_lines(connection, transaction)
    # A round trip is loaded once, so both legs sell from the outbound leg's rows.
    stock_flight_id = trip_root(connection, transaction.flight_id)
    consumes_stock = sale_type in {"NORMAL", "COMPLEMENTARY"}
    records_revenue = sale_type == "NORMAL"
    currency = transaction.payment_currency.strip().upper()
    line_amounts = _currency_split([line["npr_amount"] if records_revenue else 0.0 for line in expanded],
                                   rate, currency, transaction.payment_amount)
    paid_total = round(sum(line_amounts), 2)
    computed_total = currency_amount(sum(line["npr_amount"] for line in expanded) if records_revenue else 0.0,
                                     rate, currency)
    if transaction.payment_amount is not None and abs(paid_total - computed_total) > max(1.0, 0.1 * computed_total):
        # The phone's rate and the server's rate for that day disagree by more
        # than rounding can explain: keep what was collected, show the gap.
        connection.execute(
            "INSERT INTO flight_conflicts(flight_id,device,field,kept_value,kept_by,device_value,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (transaction.flight_id, user["employee_id"], "payment_amount",
             f"{currency} {computed_total:g} at the server rate", None,
             f"{currency} {paid_total:g} collected ({transaction.order_ref})", now_iso()))

    total_npr = 0.0
    inserted = []
    for index, line in enumerate(expanded):
        if consumes_stock:
            # Conditional update: the stock check and the decrement are one
            # statement, so two devices cannot both pass the check.
            cur = connection.execute(
                """UPDATE inventory SET sold_quantity = sold_quantity + ?
                   WHERE flight_id=? AND product_id=?
                     AND loaded_quantity - sold_quantity - returned_quantity
                         - wasted_quantity >= ?""",
                (line["quantity"], stock_flight_id, line["product_id"], line["quantity"]),
            )
            if cur.rowcount == 0:
                product = connection.execute(
                    "SELECT product_name FROM products WHERE id=?", (line["product_id"],)
                ).fetchone()
                name = product["product_name"] if product else "product"
                if not replay:
                    raise PermanentError(f"Insufficient stock on board for {name}.")
                # 7.18: every phone sells offline from the same trolley figure,
                # so the uploads together can claim more than was loaded. The
                # sale happened (the cash is in the bag), so it is kept, and
                # the sector is flagged for Inventory to check the load count.
                connection.execute(
                    "UPDATE inventory SET sold_quantity = sold_quantity + ? WHERE flight_id=? AND product_id=?",
                    (line["quantity"], stock_flight_id, line["product_id"]))
                stock = connection.execute(
                    "SELECT loaded_quantity, sold_quantity, returned_quantity, wasted_quantity FROM inventory "
                    "WHERE flight_id=? AND product_id=?", (stock_flight_id, line["product_id"])).fetchone()
                kept = f"{name}: {stock['loaded_quantity'] if stock else '?'} loaded"
                sold_text = f"{name}: {stock['sold_quantity'] if stock else '?'} sold in total"
                open_row = connection.execute(
                    "SELECT id FROM flight_conflicts WHERE flight_id=? AND field='oversell' AND kept_value=? "
                    "AND resolved_at IS NULL", (transaction.flight_id, kept)).fetchone()
                if open_row:
                    connection.execute("UPDATE flight_conflicts SET device_value=?, device=? WHERE id=?",
                                       (sold_text, user["employee_id"], open_row["id"]))
                else:
                    connection.execute(
                        "INSERT INTO flight_conflicts(flight_id,device,field,kept_value,kept_by,device_value,created_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (transaction.flight_id, user["employee_id"], "oversell", kept, None, sold_text, now_iso()))

        npr_amount = line["npr_amount"] if records_revenue else 0.0
        total_npr += npr_amount
        inserted.append(connection.execute(
            """INSERT INTO sales
               (transaction_id,order_ref,flight_id,duty_group_id,sold_by_crew_member_id,
                product_id,bundle_id,crew_id,
                seat_number,quantity,unit_price,payment_method,transaction_time,
                transaction_currency,exchange_rate_to_npr,transaction_currency_amount,
                npr_amount,sale_type,remarks)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"{transaction.order_ref}-{index + 1}", transaction.order_ref,
                transaction.flight_id, duty_group["id"], seller["id"] if seller else None, line["product_id"],
                line["bundle_id"], user["employee_id"], seat, line["quantity"],
                line["unit_price"], transaction.payment_method, transaction.transaction_time,
                currency, rate, line_amounts[index], npr_amount, sale_type, remarks,
            ),
        ).lastrowid)

    # 7.14: corrections the crew made on the iPad while the sale was still in
    # the upload queue. Each is kept against every line of the sale, with its
    # mandatory remarks, so Management sees exactly what changed and why.
    for edit in transaction.device_edits:
        for sale_id in inserted:
            connection.execute(
                "INSERT INTO sale_edit_history(sale_id,changed_by,changed_at,remarks,before_json,after_json) VALUES (?,?,?,?,?,?)",
                (sale_id, user["employee_id"], edit.at, edit.remarks.strip(),
                 json.dumps(edit.before, ensure_ascii=False), json.dumps(edit.after, ensure_ascii=False)))
    if transaction.device_edits:
        last = transaction.device_edits[-1]
        for sale_id in inserted:
            connection.execute("UPDATE sales SET edited_by=?, edited_at=?, edit_remarks=? WHERE id=?",
                               (user["employee_id"], last.at, last.remarks.strip(), sale_id))

    return {
        "status": "success",
        "order_ref": transaction.order_ref,
        "sold_by": seller["name"] if seller else None,
        "sale_type": sale_type,
        "total_npr": round(total_npr, 2),
        "total_currency": paid_total,
        "currency": transaction.payment_currency.strip().upper(),
        "exchange_rate": rate,
        "lines": len(expanded),
    }


@router.post("/sales")
def record_sale(transaction: SaleTransaction, user: dict = Depends(requires("crew"))):
    connection = get_connection()
    try:
        result = _record(connection, transaction, user)
        connection.commit()
    except PermanentError as exc:
        connection.rollback()
        raise HTTPException(status_code=422, detail=exc.detail)
    except HTTPException:
        connection.rollback()
        raise
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    finally:
        connection.close()
    return result


@router.post("/sales/sync")
def sync_sales(data: SyncRequest, user: dict = Depends(requires("crew"))):
    """Batch upload of the offline queue.

    Each transaction is reported independently. A 422-class failure is marked
    `permanent` so the device drops it into a review list instead of retrying
    it forever and blocking everything behind it.
    """
    results = []
    for transaction in data.transactions:
        connection = get_connection()
        try:
            outcome = _record(connection, transaction, user, replay=True)
            connection.commit()
            results.append({**outcome, "permanent": False})
        except PermanentError as exc:
            connection.rollback()
            results.append({
                "order_ref": transaction.order_ref, "status": "rejected",
                "permanent": True, "detail": exc.detail,
            })
        except HTTPException as exc:
            connection.rollback()
            results.append({
                "order_ref": transaction.order_ref, "status": "rejected",
                "permanent": exc.status_code < 500, "detail": exc.detail,
            })
        except Exception as exc:  # noqa: BLE001 - transient, keep it queued
            connection.rollback()
            results.append({
                "order_ref": transaction.order_ref, "status": "failed",
                "permanent": False, "detail": str(exc),
            })
        finally:
            connection.close()

    accepted = sum(1 for r in results if r["status"] in {"success", "already_recorded"})
    rejected = [r for r in results if r.get("permanent")]
    if rejected:
        log_action(user, "SYNC", "SYNC_REJECTED",
                   f"{len(rejected)} queued transaction(s) were rejected on sync.",
                   status="FAILED", metadata={"rejected": rejected})
    return {"accepted": accepted, "rejected": len(rejected), "results": results}


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
SALE_SELECT = """
SELECT s.id, s.transaction_id, s.order_ref, s.flight_id, f.flight_number, f.flight_date,
       f.origin, f.destination, f.direction, f.aircraft,
       s.duty_group_id, d.label AS duty_group_label,
       s.sold_by_crew_member_id, cm.crew_code AS seller_code, cm.name AS seller_name,
       s.product_id, p.product_name, p.category,
       s.bundle_id, b.bundle_name, s.crew_id, s.seat_number, s.quantity, s.unit_price,
       s.payment_method, s.transaction_time, s.transaction_currency, s.exchange_rate_to_npr,
       s.transaction_currency_amount, s.npr_amount, s.sale_type, s.remarks,
       s.edited_by, s.edited_at, s.edit_remarks,
       f.trip_type, CASE WHEN f.direction='RETURN' THEN 2 ELSE 1 END AS segment,
       (SELECT COUNT(*) FROM sale_edit_history h WHERE h.sale_id = s.id) AS edit_count
FROM sales s
JOIN flights f ON f.id = s.flight_id
LEFT JOIN products p ON p.id = s.product_id
LEFT JOIN bundles b ON b.id = s.bundle_id
LEFT JOIN duty_groups d ON d.id = s.duty_group_id
LEFT JOIN crew_members cm ON cm.id = s.sold_by_crew_member_id
"""


@router.get("/sales")
def list_sales(flight_id: int | None = None, date: str | None = None,
               duty_group_id: int | None = None, crew_member_id: int | None = None,
               device: str | None = None, product_id: int | None = None,
               sale_type: str | None = None, seller_name: str | None = None,
               limit: int = 500,
               page: int | None = PageParam, page_size: int = PageSizeParam,
               # Transaction-level detail is an oversight function, not a
               # reporting one, so finance's dashboard access does not grant
               # it. The inventory supervisor needs it to reconcile what was
               # sold against what came back.
               user: dict = Depends(requires("sales_view"))):
    connection = get_connection()
    try:
        conditions, params = [], []
        if flight_id:
            conditions.append("s.flight_id=?")
            params.append(flight_id)
        if date:
            conditions.append("substr(s.transaction_time,1,10)=?")
            params.append(validate_date(date, "date"))
        if duty_group_id:
            conditions.append("s.duty_group_id=?")
            params.append(duty_group_id)
        if crew_member_id:
            conditions.append("s.sold_by_crew_member_id=?")
            params.append(crew_member_id)
        if device:
            # 7.12: the iPad login that recorded the sale, so Management can
            # look at one iPad's sales on a trip worked by two.
            conditions.append("s.crew_id=?")
            params.append(device.strip().upper())
        if product_id:
            conditions.append("(s.product_id=? OR s.bundle_id=?)")
            params.extend([product_id, -product_id])
        if sale_type:
            conditions.append("s.sale_type=?")
            params.append(sale_type.strip().upper())
        if seller_name:
            conditions.append("cm.name LIKE ?")
            params.append(f"%{seller_name.strip()}%")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        if page:
            return paginate_sql(connection, f"{SALE_SELECT} {where} ORDER BY s.id DESC", params, page, page_size)
        rows = connection.execute(
            f"{SALE_SELECT} {where} ORDER BY s.id DESC LIMIT ?", [*params, min(limit, 2000)]
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        connection.close()


@router.get("/sales/flights")
def sales_by_flight(year: int | None = None, month: int | None = None, day: int | None = None,
                    destination: str | None = None, flight_number: str | None = None,
                    page: int | None = PageParam, page_size: int = PageSizeParam,
                    user: dict = Depends(requires("sales_view"))):
    """Every sector in the period with its sales total (7.13): the Sales page
    for Management, Inventory and the administrator. Newest first."""
    connection = get_connection()
    try:
        conditions, params = [], []
        if year:
            conditions.append("substr(f.flight_date,1,4)=?")
            params.append(f"{year:04d}")
        if month:
            conditions.append("substr(f.flight_date,6,2)=?")
            params.append(f"{month:02d}")
        if day:
            conditions.append("substr(f.flight_date,9,2)=?")
            params.append(f"{day:02d}")
        if destination:
            conditions.append("(f.destination=? OR f.origin=?)")
            params.extend([destination.upper(), destination.upper()])
        if flight_number:
            conditions.append("f.flight_number LIKE ?")
            params.append(f"%{flight_number.strip().upper()}%")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT f.id, f.flight_number, f.flight_date, f.origin, f.destination, f.original_destination,
                   f.direction, f.trip_type,
                   f.status, f.aircraft_type, f.passengers, f.trip_completed_at,
                   CASE WHEN f.direction='RETURN' THEN 2 ELSE 1 END AS segment,
                   (SELECT GROUP_CONCAT(a.crew_account, ',') FROM flight_assignments a WHERE a.flight_id=f.id) AS devices,
                   (SELECT COUNT(DISTINCT s.order_ref) FROM sales s WHERE s.flight_id=f.id AND s.sale_type='NORMAL') AS transactions,
                   (SELECT COALESCE(SUM(s.quantity),0) FROM sales s WHERE s.flight_id=f.id AND s.sale_type='NORMAL') AS units,
                   (SELECT COALESCE(SUM(s.npr_amount),0) FROM sales s WHERE s.flight_id=f.id AND s.sale_type='NORMAL') AS revenue_npr,
                   (SELECT COUNT(DISTINCT s.order_ref) FROM sales s WHERE s.flight_id=f.id AND s.sale_type='COMPLEMENTARY') AS complementary,
                   (SELECT COUNT(DISTINCT s.order_ref) FROM sales s WHERE s.flight_id=f.id AND s.sale_type='CANCELLATION') AS cancellations,
                   (SELECT COUNT(*) FROM sale_edit_history h JOIN sales s ON s.id=h.sale_id WHERE s.flight_id=f.id) AS edits,
                   (SELECT COUNT(*) FROM flight_conflicts c WHERE c.flight_id=f.id AND c.resolved_at IS NULL) AS open_conflicts,
                   (SELECT COUNT(*) FROM flight_report_revisions rv JOIN flight_reports r ON r.id=rv.report_id
                     WHERE r.flight_id=f.id AND rv.action='MERGED') AS merged_forms,
                   (SELECT COUNT(*) FROM sector_stock_items si JOIN sector_stock_counts sc ON sc.id=si.count_id
                     WHERE sc.flight_id=f.id AND si.variance<>0) AS stock_variances,
                   (SELECT COUNT(*) FROM reconciliation_items ri JOIN reconciliations r ON r.id=ri.reconciliation_id
                     WHERE r.flight_id=CASE WHEN f.direction='RETURN' THEN f.linked_flight_id ELSE f.id END
                       AND ri.variance<>0 AND ri.override_by IS NULL) AS return_variances
            FROM flights f {where}
            ORDER BY f.flight_date DESC, f.flight_number, f.direction"""
        if page:
            out = paginate_sql(connection, sql, params, page, page_size)
            out["items"] = [_round_flight(r) for r in out["items"]]
            return out
        return [_round_flight(dict(r)) for r in connection.execute(sql, params).fetchall()]
    finally:
        connection.close()


def _round_flight(row):
    row = dict(row)
    row["revenue_npr"] = round(float(row.get("revenue_npr") or 0), 2)
    row["devices"] = [d for d in str(row.get("devices") or "").split(",") if d]
    # 7.15: anything the two iPads disagreed on, or that does not add up, is
    # highlighted on the sector until Management has looked at it.
    row["differences"] = int(row.get("open_conflicts") or 0) + int(row.get("merged_forms") or 0) \
        + int(row.get("stock_variances") or 0) + int(row.get("return_variances") or 0)
    return row


@router.get("/sales/flights/{flight_id}/differences")
def flight_differences(flight_id: int, user: dict = Depends(requires("sales_view"))):
    """What the two iPads disagreed on, and what does not add up, on one sector (7.15)."""
    connection = get_connection()
    try:
        flight = connection.execute("SELECT * FROM flights WHERE id=?", (flight_id,)).fetchone()
        if not flight:
            raise HTTPException(status_code=404, detail="Flight not found.")
        root_id = flight["linked_flight_id"] if flight["direction"] == "RETURN" and flight["linked_flight_id"] else flight["id"]
        conflicts = [dict(r) for r in connection.execute(
            "SELECT * FROM flight_conflicts WHERE flight_id=? ORDER BY resolved_at IS NOT NULL, id DESC", (flight_id,))]
        merged = [dict(r) for r in connection.execute("""
            SELECT rv.id, rv.rev_no, rv.changed_by, rv.changed_at, rv.remarks, r.report_type, r.submitted_by, r.id AS report_id
            FROM flight_report_revisions rv JOIN flight_reports r ON r.id=rv.report_id
            WHERE r.flight_id=? AND rv.action='MERGED' ORDER BY rv.id DESC""", (flight_id,))]
        # Remaining stock after this sector, per iPad, lines that do not add up.
        counts = {r["id"]: dict(r) for r in connection.execute(
            "SELECT * FROM sector_stock_counts WHERE flight_id=?", (flight_id,))}
        stock_lines = {}
        for r in connection.execute("""
                SELECT si.*, p.product_name FROM sector_stock_items si JOIN products p ON p.id=si.product_id
                WHERE si.count_id IN (SELECT id FROM sector_stock_counts WHERE flight_id=?)""", (flight_id,)):
            line = stock_lines.setdefault(r["product_id"], {
                "product_id": r["product_id"], "product_name": r["product_name"],
                "expected_remaining": r["expected_remaining"], "by_device": {}, "declared_total": 0})
            device = counts[r["count_id"]]["device"]
            line["by_device"][device] = r["declared_remaining"]
            line["declared_total"] += int(r["declared_remaining"])
        sector_stock = []
        for line in stock_lines.values():
            line["variance"] = line["declared_total"] - int(line["expected_remaining"])
            if line["variance"] != 0:
                sector_stock.append(line)
        stock_remarks = {c["device"]: c["remarks"] for c in counts.values() if c.get("remarks")}
        # The trip's final stock return, lines that do not add up.
        stock_return = None
        recon = connection.execute("SELECT * FROM reconciliations WHERE flight_id=?", (root_id,)).fetchone()
        if recon:
            from routers.obs import _recon
            full = _recon(connection, recon_id=recon["id"])
            stock_return = {
                "id": full["id"], "status": full["status"], "devices": full.get("devices"),
                "submitted_devices": full.get("submitted_devices"), "device_remarks": full.get("device_remarks"),
                "items": [i for i in full["items"] if i["variance"] != 0 or i.get("overridden")],
            }
    finally:
        connection.close()
    return {
        "flight_id": flight_id, "root_id": root_id,
        "conflicts": conflicts, "merged_forms": merged,
        "sector_stock": sector_stock, "sector_stock_remarks": stock_remarks,
        "stock_return": stock_return,
        "total": len([c for c in conflicts if not c["resolved_at"]]) + len(merged) + len(sector_stock)
                 + (len([i for i in stock_return["items"] if not i.get("overridden")]) if stock_return else 0),
    }


class ResolveIn(BaseModel):
    apply_device_value: bool = False
    remarks: str = Field(min_length=1, max_length=500)


@router.put("/flight-conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: int, data: ResolveIn, user: dict = Depends(requires("sales_edit"))):
    """Settle a PAX / aircraft difference: keep the first iPad's value or apply
    the other iPad's, always with remarks and an audit entry."""
    connection = get_connection()
    try:
        c = connection.execute("SELECT * FROM flight_conflicts WHERE id=?", (conflict_id,)).fetchone()
        if not c:
            raise HTTPException(status_code=404, detail="Difference not found.")
        if c["resolved_at"]:
            raise HTTPException(status_code=400, detail="This difference has already been settled.")
        now = now_iso()
        if data.apply_device_value:
            if c["field"] == "passengers":
                connection.execute("UPDATE flights SET passengers=?, pax_entered_by=?, pax_entered_at=? WHERE id=?",
                                   (int(c["device_value"]), user["employee_id"], now, c["flight_id"]))
            elif c["field"] == "aircraft":
                value = str(c["device_value"] or "")
                aircraft_type = value.split(" (")[0].strip() or None
                aircraft = value[value.index("(") + 1:-1].strip() if "(" in value and value.endswith(")") else None
                connection.execute("UPDATE flights SET aircraft_type=?, aircraft=?, aircraft_entered_by=?, aircraft_entered_at=? WHERE id=?",
                                   (aircraft_type, aircraft, user["employee_id"], now, c["flight_id"]))
        connection.execute(
            "UPDATE flight_conflicts SET resolved_by=?, resolved_at=?, resolution=?, remarks=? WHERE id=?",
            (user["employee_id"], now, "APPLIED_DEVICE_VALUE" if data.apply_device_value else "KEPT",
             data.remarks.strip(), conflict_id))
        connection.commit()
        flight = connection.execute("SELECT flight_number FROM flights WHERE id=?", (c["flight_id"],)).fetchone()
    finally:
        connection.close()
    log_action(user, "FLIGHT", "RESOLVE_DIFFERENCE",
               f"{flight['flight_number']}: {c['field']} difference settled — "
               + (f"applied iPad {c['device']}'s value {c['device_value']}" if data.apply_device_value
                  else f"kept {c['kept_value']} (iPad {c['device']} had {c['device_value']})")
               + f". Remarks: {data.remarks.strip()}", "flight", c["flight_id"],
               metadata={"conflict_id": conflict_id, "apply_device_value": data.apply_device_value})
    return {"ok": True, "conflict_id": conflict_id}


@router.get("/sales/flights/{flight_id}/summary")
def flight_sales_summary(flight_id: int, user: dict = Depends(requires("sales_view"))):
    """One sector's sales broken down by product, by iPad login, by seller
    and by payment currency (7.13)."""
    connection = get_connection()
    try:
        flight = connection.execute("SELECT * FROM flights WHERE id=?", (flight_id,)).fetchone()
        if not flight:
            raise HTTPException(status_code=404, detail="Flight not found.")
        normal = "s.flight_id=? AND s.sale_type='NORMAL'"
        by_product = [dict(r) for r in connection.execute(f"""
            SELECT COALESCE(p.id, -b.id) AS product_id, COALESCE(p.product_name, b.bundle_name) AS product_name,
                   COALESCE(p.category, 'Combo') AS category, s.bundle_id IS NOT NULL AS is_combo,
                   SUM(s.quantity) AS units, COUNT(DISTINCT s.order_ref) AS transactions,
                   ROUND(SUM(s.npr_amount), 2) AS revenue_npr
            FROM sales s LEFT JOIN products p ON p.id=s.product_id LEFT JOIN bundles b ON b.id=s.bundle_id
            WHERE {normal} GROUP BY 1, 2, 3, 4 ORDER BY revenue_npr DESC, product_name""", (flight_id,))]
        by_device = [dict(r) for r in connection.execute(f"""
            SELECT s.crew_id AS device, COUNT(DISTINCT s.order_ref) AS transactions, SUM(s.quantity) AS units,
                   ROUND(SUM(s.npr_amount), 2) AS revenue_npr,
                   MIN(s.transaction_time) AS first_sale, MAX(s.transaction_time) AS last_sale
            FROM sales s WHERE {normal} GROUP BY s.crew_id ORDER BY revenue_npr DESC""", (flight_id,))]
        # 7.18: which login sold which product (Inventory and Management).
        by_device_product = [dict(r) for r in connection.execute(f"""
            SELECT s.crew_id AS device, COALESCE(p.product_name, b.bundle_name) AS product_name,
                   SUM(s.quantity) AS units, ROUND(SUM(s.npr_amount), 2) AS revenue_npr
            FROM sales s LEFT JOIN products p ON p.id=s.product_id LEFT JOIN bundles b ON b.id=s.bundle_id
            WHERE {normal} GROUP BY 1, 2 ORDER BY 1, revenue_npr DESC""", (flight_id,))]
        by_seller = [dict(r) for r in connection.execute(f"""
            SELECT COALESCE(cm.name, 'Not attributed') AS seller_name, cm.id AS crew_member_id,
                   COUNT(DISTINCT s.order_ref) AS transactions, SUM(s.quantity) AS units,
                   ROUND(SUM(s.npr_amount), 2) AS revenue_npr,
                   SUM(CASE WHEN s.bundle_id IS NOT NULL THEN s.quantity ELSE 0 END) AS combo_units
            FROM sales s LEFT JOIN crew_members cm ON cm.id=s.sold_by_crew_member_id
            WHERE {normal} GROUP BY 1, 2 ORDER BY revenue_npr DESC""", (flight_id,))]
        by_currency = [dict(r) for r in connection.execute(f"""
            SELECT s.transaction_currency AS currency, COUNT(DISTINCT s.order_ref) AS transactions,
                   ROUND(SUM(s.transaction_currency_amount), 2) AS amount, ROUND(SUM(s.npr_amount), 2) AS revenue_npr
            FROM sales s WHERE {normal} GROUP BY s.transaction_currency ORDER BY revenue_npr DESC""", (flight_id,))]
        by_type = [dict(r) for r in connection.execute("""
            SELECT s.sale_type, COUNT(DISTINCT s.order_ref) AS transactions, SUM(s.quantity) AS units,
                   ROUND(SUM(s.npr_amount), 2) AS revenue_npr
            FROM sales s WHERE s.flight_id=? GROUP BY s.sale_type""", (flight_id,))]
        totals = connection.execute(f"""
            SELECT COUNT(DISTINCT s.order_ref) AS transactions, COALESCE(SUM(s.quantity),0) AS units,
                   ROUND(COALESCE(SUM(s.npr_amount),0), 2) AS revenue_npr,
                   COUNT(DISTINCT s.seat_number) AS seats
            FROM sales s WHERE {normal}""", (flight_id,)).fetchone()
        edits = connection.execute(
            "SELECT COUNT(*) c FROM sale_edit_history h JOIN sales s ON s.id=h.sale_id WHERE s.flight_id=?",
            (flight_id,)).fetchone()["c"]
        devices = [r["crew_account"] for r in connection.execute(
            "SELECT crew_account FROM flight_assignments WHERE flight_id=? ORDER BY id", (flight_id,))]
        group = connection.execute("SELECT id, label FROM duty_groups WHERE flight_id=?", (flight_id,)).fetchone()
        diversions = [dict(r) for r in connection.execute(
            "SELECT * FROM flight_diversions WHERE flight_id=? ORDER BY id", (flight_id,))]
        crew = [dict(r) for r in connection.execute(
            """SELECT m.crew_code, m.name, m.position FROM duty_group_members dm
               JOIN duty_groups d ON d.id = dm.duty_group_id JOIN crew_members m ON m.id = dm.crew_member_id
               WHERE d.flight_id=? ORDER BY m.name""", (flight_id,))]
    finally:
        connection.close()
    return {
        "flight": {**dict(flight), "devices": devices, "duty_group_label": group["label"] if group else None,
                   "segment": 2 if flight["direction"] == "RETURN" else 1},
        "totals": {**dict(totals), "edits": edits},
        "by_product": by_product, "by_device": by_device, "by_seller": by_seller,
        "by_device_product": by_device_product, "diversions": diversions, "crew": crew,
        "by_currency": by_currency, "by_type": by_type,
    }


@router.get("/my-sales")
def my_sales(date: str | None = None, flight_id: int | None = None,
             page: int | None = PageParam, page_size: int = PageSizeParam,
             user: dict = Depends(requires("sync"))):
    """A cabin iPad sees only its own flights; management sees everything.

    Every role that is not explicitly an oversight role is scoped to its own
    assignments. Inventory holds `sync` for the stock return, and without this
    it would otherwise read the entire sales ledger.
    """
    connection = get_connection()
    try:
        conditions, params = [], []
        if "sales_view" not in permissions_for(user["role"]):
            conditions.append(
                """s.flight_id IN (SELECT flight_id FROM flight_assignments
                                   WHERE crew_account=?)"""
            )
            params.append(user["employee_id"])
        if date:
            conditions.append("substr(s.transaction_time,1,10)=?")
            params.append(validate_date(date, "date"))
        if flight_id:
            conditions.append("s.flight_id=?")
            params.append(flight_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        if page:
            return paginate_sql(connection, f"{SALE_SELECT} {where} ORDER BY s.id DESC", params, page, page_size)
        rows = connection.execute(
            f"{SALE_SELECT} {where} ORDER BY s.id DESC LIMIT 1000", params
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        connection.close()


@router.get("/my-sales-summary")
def my_sales_summary(date: str | None = None, user: dict = Depends(requires("sync"))):
    """Today's group total, the 10% pool, and the per-member share."""
    from routers.analytics import group_summary  # shared implementation

    return group_summary(user, date)


# --------------------------------------------------------------------------- #
# Management corrections
# --------------------------------------------------------------------------- #
@router.get("/sales/{sale_id}/history")
def sale_history(sale_id: int, user: dict = Depends(requires("sales_view"))):
    """Every correction made to one sale, newest first, with names resolved."""
    connection = get_connection()
    try:
        if not connection.execute("SELECT 1 FROM sales WHERE id=?", (sale_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Sale not found.")
        products = {r["id"]: r["product_name"] for r in connection.execute("SELECT id, product_name FROM products")}
        crew = {r["id"]: r["name"] for r in connection.execute("SELECT id, name FROM crew_members")}
        out = []
        for row in connection.execute(
                "SELECT * FROM sale_edit_history WHERE sale_id=? ORDER BY changed_at DESC, id DESC", (sale_id,)):
            before, after = json.loads(row["before_json"]), json.loads(row["after_json"])
            changes = []
            if "lines" in before or "lines" in after:
                # An iPad correction made before upload: whole-sale shape.
                def _lines(d):
                    return ", ".join(f"{products.get(l.get('product_id'), l.get('bundle_name') or l.get('name') or 'combo')} x{l.get('quantity')}"
                                     for l in (d.get("lines") or []))
                if before.get("seat_number") != after.get("seat_number"):
                    changes.append(f"seat: {before.get('seat_number')} \u2192 {after.get('seat_number')}")
                if _lines(before) != _lines(after):
                    changes.append(f"items: {_lines(before)} \u2192 {_lines(after)}")
                out.append({"id": row["id"], "changed_by": row["changed_by"], "changed_at": row["changed_at"],
                            "remarks": row["remarks"], "changes": changes, "on_device": True})
                continue
            for key, label in (("product_id", "product"), ("quantity", "quantity"), ("seat_number", "seat"),
                               ("sold_by_crew_member_id", "sold by"), ("unit_price", "unit price")):
                if before.get(key) != after.get(key):
                    if key == "product_id":
                        a, b = products.get(before.get(key), before.get(key)), products.get(after.get(key), after.get(key))
                    elif key == "sold_by_crew_member_id":
                        a, b = crew.get(before.get(key), before.get(key)), crew.get(after.get(key), after.get(key))
                    else:
                        a, b = before.get(key), after.get(key)
                    changes.append(f"{label}: {a} \u2192 {b}")
            out.append({"id": row["id"], "changed_by": row["changed_by"], "changed_at": row["changed_at"],
                        "remarks": row["remarks"], "changes": changes})
        return out
    finally:
        connection.close()


@router.put("/sales/{sale_id}")
def edit_sale(sale_id: int, data: SaleEdit, user: dict = Depends(requires("sales_edit"))):
    """Correct a mis-keyed sale (wrong product, wrong quantity, wrong seat)."""
    if not data.edit_remarks.strip():
        raise HTTPException(status_code=400, detail="Remarks are mandatory when editing a sale.")

    connection = get_connection()
    try:
        sale = connection.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            raise HTTPException(status_code=404, detail="Sale not found.")
        if sale["bundle_id"]:
            raise HTTPException(
                status_code=400,
                detail="Bundle lines cannot be edited individually. Cancel and re-enter the transaction.",
            )

        new_product_id = data.product_id or sale["product_id"]
        new_quantity = data.quantity or sale["quantity"]
        seat = (data.seat_number or sale["seat_number"]).strip().upper()
        new_seller_id = data.sold_by_crew_member_id or sale["sold_by_crew_member_id"]

        if data.sold_by_crew_member_id:
            # A sale may only be moved to somebody who actually worked that
            # sector, otherwise commission would land on a crew member who was
            # never on board.
            seller = connection.execute(
                """SELECT m.id, m.name FROM duty_group_members dm
                   JOIN crew_members m ON m.id = dm.crew_member_id
                   JOIN duty_groups d ON d.id = dm.duty_group_id
                   WHERE d.flight_id=? AND m.id=?""",
                (sale["flight_id"], data.sold_by_crew_member_id),
            ).fetchone()
            if not seller:
                raise HTTPException(
                    status_code=400,
                    detail="That crew member was not in the duty group working this sector.",
                )

        product = connection.execute(
            "SELECT * FROM products WHERE id=?", (new_product_id,)
        ).fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Replacement product not found.")

        flight = connection.execute(
            "SELECT status, flight_number, aircraft_type FROM flights WHERE id=?",
            (sale["flight_id"],),
        ).fetchone()
        if not valid_seat(seat, flight["aircraft_type"] if flight else None):
            raise HTTPException(status_code=400, detail=f"Seat {seat} does not exist on this aircraft.")
        # Once a flight is reconciled the physical count is settled, so a
        # correction only fixes the books; it must not move stock again.
        stock_settled = flight and flight["status"] != "OPEN"
        counts_stock = sale["sale_type"] in {"NORMAL", "COMPLEMENTARY"} and not stock_settled
        stock_flight_id = trip_root(connection, sale["flight_id"])
        if counts_stock:
            # Give the old units back, then take the new ones.
            connection.execute(
                "UPDATE inventory SET sold_quantity = sold_quantity - ? WHERE flight_id=? AND product_id=?",
                (sale["quantity"], stock_flight_id, sale["product_id"]),
            )
            connection.execute(
                "INSERT OR IGNORE INTO inventory(flight_id,product_id,loaded_quantity) VALUES (?,?,0)",
                (stock_flight_id, new_product_id),
            )
            cur = connection.execute(
                """UPDATE inventory SET sold_quantity = sold_quantity + ?
                   WHERE flight_id=? AND product_id=?
                     AND loaded_quantity - sold_quantity - returned_quantity
                         - wasted_quantity >= ?""",
                (new_quantity, stock_flight_id, new_product_id, new_quantity),
            )
            if cur.rowcount == 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Not enough {product['product_name']} was loaded on that flight to make this correction.",
                )

        # The price the passenger actually paid is history. Only a change of
        # product justifies a new one, and then it is today's price for that
        # product; correcting a seat or a seller must never restate revenue.
        product_changed = new_product_id != sale["product_id"]
        unit_price = (float(product["selling_price"]) if product_changed
                      else float(sale["unit_price"]))
        npr_amount = round(unit_price * new_quantity, 2) if sale["sale_type"] == "NORMAL" else 0.0
        rate = float(sale["exchange_rate_to_npr"] or 1)
        edited_at = now_iso()
        connection.execute(
            """UPDATE sales SET product_id=?, quantity=?, seat_number=?, unit_price=?,
                   sold_by_crew_member_id=?, npr_amount=?, transaction_currency_amount=?,
                   edited_by=?, edited_at=?, edit_remarks=? WHERE id=?""",
            (new_product_id, new_quantity, seat, unit_price, new_seller_id, npr_amount,
             currency_amount(npr_amount, rate, (sale["transaction_currency"] or "NPR").upper()),
             user["employee_id"], edited_at, data.edit_remarks.strip(), sale_id),
        )
        # Every correction is kept line by line (7.10), not only the last one.
        before = {"product_id": sale["product_id"], "quantity": sale["quantity"], "seat_number": sale["seat_number"],
                  "sold_by_crew_member_id": sale["sold_by_crew_member_id"], "unit_price": sale["unit_price"],
                  "npr_amount": sale["npr_amount"]}
        after = {"product_id": new_product_id, "quantity": new_quantity, "seat_number": seat,
                 "sold_by_crew_member_id": new_seller_id, "unit_price": unit_price, "npr_amount": npr_amount}
        connection.execute(
            "INSERT INTO sale_edit_history(sale_id,changed_by,changed_at,remarks,before_json,after_json) VALUES (?,?,?,?,?,?)",
            (sale_id, user["employee_id"], edited_at, data.edit_remarks.strip(), json.dumps(before), json.dumps(after)))
        connection.commit()
        row = connection.execute(f"{SALE_SELECT} WHERE s.id=?", (sale_id,)).fetchone()
    except HTTPException:
        connection.rollback()
        raise
    finally:
        connection.close()

    note = "" if counts_stock else " Flight already reconciled, so onboard stock was not adjusted."
    log_action(user, "SALES", "EDIT_SALE",
               f"Corrected sale #{sale_id} to {product['product_name']} x{new_quantity} "
               f"on seat {seat}. Remarks: {data.edit_remarks.strip()}{note}",
               "sale", sale_id,
               metadata={"previous_product_id": sale["product_id"],
                         "previous_quantity": sale["quantity"],
                         "previous_seat": sale["seat_number"],
                         "previous_seller_id": sale["sold_by_crew_member_id"],
                         "previous_unit_price": sale["unit_price"],
                         "previous_npr_amount": sale["npr_amount"],
                         "stock_adjusted": counts_stock})
    return dict(row)


@router.get("/currencies")
def currencies(user: dict = Depends(current_user)):
    """Active rates for today, used by the crew sales screen."""
    from routers.rates import active_rates

    return active_rates()
