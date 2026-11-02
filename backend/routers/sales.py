"""Sales capture, offline sync and management corrections.

A passenger transaction is one `order_ref` covering one seat. It may hold
several lines, each a product or a bundle. Bundle revenue is allocated across
its components pro rata so product-level reporting stays accurate.

Sale types:
  NORMAL        -> revenue recorded, stock consumed
  COMPLEMENTARY -> no revenue, stock consumed (it left the trolley)
  CANCELLATION  -> no revenue, no stock consumed (the sale did not happen)
"""

import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import AIRCRAFT_TYPES, COMMISSION_RATE, get_connection
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


class SaleTransaction(BaseModel):
    order_ref: str = Field(min_length=6, max_length=64)
    flight_id: int
    seat_number: str
    # Which member of the sector's duty group made this sale. Commission and
    # the performance ranking are built from it, so it is not optional.
    sold_by_crew_member_id: int
    lines: list[SaleLine]
    payment_currency: str = "NPR"
    payment_method: str = "Cash"
    sale_type: str = "NORMAL"
    remarks: str | None = None
    transaction_time: str


class SyncRequest(BaseModel):
    transactions: list[SaleTransaction]


class SaleEdit(BaseModel):
    product_id: int | None = None
    quantity: int | None = Field(default=None, gt=0)
    seat_number: str | None = None
    # Management may reattribute a sale recorded against the wrong crew member.
    sold_by_crew_member_id: int | None = None
    edit_remarks: str


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


def _record(connection, transaction: SaleTransaction, user: dict):
    """Write one passenger transaction. Raises PermanentError for unretryable input."""
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
    if flight["status"] == "FLOWN":
        raise PermanentError(
            "Selling has been finished on this leg. Switch to the return flight."
        )
    if flight["status"] != "OPEN":
        raise PermanentError("This flight has been closed and reconciled.")
    if not trip_flow.leg_started(flight):
        raise PermanentError("Start the second flight on the Sales page before selling on it.")
    if not flight["passengers"]:
        raise PermanentError("The PAX load has not been entered for this flight.")
    if not valid_seat(seat, flight["aircraft_type"]):
        raise PermanentError(
            f"Seat {seat} does not exist on a {flight['aircraft_type'] or 'this'} aircraft."
        )

    assignment = connection.execute(
        "SELECT * FROM flight_assignments WHERE flight_id=?", (transaction.flight_id,)
    ).fetchone()
    if "sell_any_flight" not in permissions_for(user["role"]):
        if not assignment:
            raise PermanentError("No OBS device is assigned to this flight.")
        if user["employee_id"] != assignment["crew_account"]:
            raise PermanentError("This device is not assigned to that flight.")

    # The seller must be one of the people the crew put in this sector's duty
    # group. Without a group there is nobody to attribute a sale to, so selling
    # cannot start until the names have been entered.
    duty_group = connection.execute(
        "SELECT * FROM duty_groups WHERE flight_id=?", (transaction.flight_id,)
    ).fetchone()
    if not duty_group:
        raise PermanentError(
            "No duty group has been formed for this sector. Enter the crew names before selling."
        )
    seller = connection.execute(
        """SELECT m.id, m.name FROM duty_group_members dm
           JOIN crew_members m ON m.id = dm.crew_member_id
           WHERE dm.duty_group_id=? AND dm.crew_member_id=?""",
        (duty_group["id"], transaction.sold_by_crew_member_id),
    ).fetchone()
    if not seller:
        raise PermanentError("The selected crew member is not in this sector's duty group.")

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

    total_npr = 0.0
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
                raise PermanentError(f"Insufficient stock on board for {name}.")

        npr_amount = line["npr_amount"] if records_revenue else 0.0
        total_npr += npr_amount
        connection.execute(
            """INSERT INTO sales
               (transaction_id,order_ref,flight_id,duty_group_id,sold_by_crew_member_id,
                product_id,bundle_id,crew_id,
                seat_number,quantity,unit_price,payment_method,transaction_time,
                transaction_currency,exchange_rate_to_npr,transaction_currency_amount,
                npr_amount,sale_type,remarks)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"{transaction.order_ref}-{index + 1}", transaction.order_ref,
                transaction.flight_id, duty_group["id"], seller["id"], line["product_id"],
                line["bundle_id"], user["employee_id"], seat, line["quantity"],
                line["unit_price"], transaction.payment_method, transaction.transaction_time,
                transaction.payment_currency.strip().upper(), rate,
                round(npr_amount / rate, 2) if rate else 0, npr_amount, sale_type, remarks,
            ),
        )

    return {
        "status": "success",
        "order_ref": transaction.order_ref,
        "sold_by": seller["name"],
        "sale_type": sale_type,
        "total_npr": round(total_npr, 2),
        "total_currency": round(total_npr / rate, 2) if rate else 0,
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
            outcome = _record(connection, transaction, user)
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
       s.edited_by, s.edited_at, s.edit_remarks
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
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        if page:
            return paginate_sql(connection, f"{SALE_SELECT} {where} ORDER BY s.id DESC", params, page, page_size)
        rows = connection.execute(
            f"{SALE_SELECT} {where} ORDER BY s.id DESC LIMIT ?", [*params, min(limit, 2000)]
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        connection.close()


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
        connection.execute(
            """UPDATE sales SET product_id=?, quantity=?, seat_number=?, unit_price=?,
                   sold_by_crew_member_id=?, npr_amount=?, transaction_currency_amount=?,
                   edited_by=?, edited_at=?, edit_remarks=? WHERE id=?""",
            (new_product_id, new_quantity, seat, unit_price, new_seller_id, npr_amount,
             round(npr_amount / rate, 2) if rate else 0,
             user["employee_id"], now_iso(), data.edit_remarks.strip(), sale_id),
        )
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
