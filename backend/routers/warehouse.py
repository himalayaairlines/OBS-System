"""Main (warehouse) inventory and the purchase-order approval workflow.

Flow: low-stock alert -> inventory officer raises an order request ->
inventory supervisor approves -> approved quantities land in main inventory.
Every movement is written to main_inventory_txn so stock is auditable.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import get_connection
from security import current_user, log_action, now_iso, requires, validate_date

router = APIRouter(tags=["warehouse"])


class OrderItemIn(BaseModel):
    """One order line: either an existing product, or a new one to be created.

    A line proposing a new product leaves `product_id` empty and fills the
    new_* fields. The product is created when the order is approved, not when
    it is requested, so an unapproved request never pollutes the catalogue.
    """
    product_id: int | None = None
    quantity: int = Field(gt=0, description="Always in pieces, so it can post to stock")
    unit_cost: float = Field(ge=0, default=0)
    new_product_code: str | None = None
    new_product_name: str | None = None
    new_product_category: str | None = None
    new_product_unit: str | None = "pcs"
    new_selling_price: float | None = None
    new_reorder_level: int | None = None


class OrderIn(BaseModel):
    items: list[OrderItemIn]
    po_code: str = "ANC"
    fiscal_year: str | None = None
    supply_type: str = "OBS"
    order_placement_date: str | None = None
    expected_delivery_date: str | None = None
    remarks: str | None = None


class OrderReview(BaseModel):
    approve: bool
    review_remarks: str | None = None


class ReceiptItem(BaseModel):
    product_id: int
    received_quantity: int = Field(ge=0)


class OrderReceipt(BaseModel):
    items: list[ReceiptItem]
    received_date: str
    received_location: str
    remarks: str | None = None


class OrderEdit(BaseModel):
    supply_type: str | None = None
    order_placement_date: str | None = None
    expected_delivery_date: str | None = None
    received_location: str | None = None
    remarks: str | None = None
    items: list[OrderItemIn] | None = None
    edit_remarks: str


class StockAdjust(BaseModel):
    product_id: int
    quantity: int
    reason: str = "ADJUSTMENT"
    remarks: str | None = None


class StockReceipt(BaseModel):
    """Add stock straight into the warehouse without a purchase order."""
    product_id: int
    quantity: int = Field(gt=0)
    remarks: str


# --------------------------------------------------------------------------- #
# Stock ledger helper
# --------------------------------------------------------------------------- #
def move_stock(connection, product_id: int, change: int, reason: str, reference: str,
               employee_id: str, stock_date: str | None = None):
    """Apply a signed change to warehouse stock and record it. Never goes negative.

    `stock_date` is the date the movement belongs to in the books; it defaults to
    today but a trolley load carries the flight date instead, so the daily
    register lines up with the flight rather than with data entry.
    """
    connection.execute(
        "INSERT OR IGNORE INTO main_inventory(product_id,quantity,updated_at) VALUES (?,0,?)",
        (product_id, now_iso()),
    )
    row = connection.execute(
        "SELECT quantity FROM main_inventory WHERE product_id=?", (product_id,)
    ).fetchone()
    balance = int(row["quantity"]) + change
    if balance < 0:
        product = connection.execute(
            "SELECT product_name FROM products WHERE id=?", (product_id,)
        ).fetchone()
        name = product["product_name"] if product else f"product {product_id}"
        raise HTTPException(
            status_code=400,
            detail=f"Main inventory has only {row['quantity']} unit(s) of {name}.",
        )
    connection.execute(
        "UPDATE main_inventory SET quantity=?, updated_at=? WHERE product_id=?",
        (balance, now_iso(), product_id),
    )
    connection.execute(
        """INSERT INTO main_inventory_txn
           (product_id,change_qty,balance_after,reason,reference,stock_date,created_by,created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (product_id, change, balance, reason, reference,
         stock_date or now_iso()[:10], employee_id, now_iso()),
    )
    return balance


# --------------------------------------------------------------------------- #
# Main inventory
# --------------------------------------------------------------------------- #
@router.get("/main-inventory")
def main_inventory(user: dict = Depends(requires("warehouse"))):
    connection = get_connection()
    try:
        rows = connection.execute(
            """SELECT p.id AS product_id, p.product_code, p.product_name, p.category, p.unit,
                      p.selling_price, p.cost, p.reorder_level, p.active,
                      COALESCE(m.quantity,0) AS quantity, m.updated_at,
                      COALESCE((SELECT SUM(i.loaded_quantity - i.sold_quantity
                                           - i.returned_quantity - i.wasted_quantity)
                                FROM inventory i JOIN flights f ON f.id = i.flight_id
                                WHERE i.product_id = p.id AND f.status='OPEN'),0) AS on_aircraft
               FROM products p
               LEFT JOIN main_inventory m ON m.product_id = p.id
               WHERE p.active=1
               ORDER BY p.category, p.product_name"""
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["low_stock"] = item["quantity"] <= item["reorder_level"]
            item["stock_value"] = round(item["quantity"] * float(item["cost"] or 0), 2)
            items.append(item)
        return items
    finally:
        connection.close()


@router.get("/warehouse/dashboard")
def warehouse_dashboard(user: dict = Depends(requires("warehouse"))):
    items = main_inventory(user)
    low = [i for i in items if i["low_stock"]]
    out = [i for i in items if i["quantity"] == 0]
    connection = get_connection()
    try:
        pending_orders = connection.execute(
            "SELECT COUNT(*) c FROM purchase_orders WHERE status='PENDING'"
        ).fetchone()["c"]
        pending_recon = connection.execute(
            "SELECT COUNT(*) c FROM reconciliations WHERE status='PENDING'"
        ).fetchone()["c"]
        movements = [dict(r) for r in connection.execute(
            """SELECT t.*, p.product_name, p.product_code
               FROM main_inventory_txn t JOIN products p ON p.id = t.product_id
               ORDER BY t.id DESC LIMIT 25"""
        ).fetchall()]
    finally:
        connection.close()

    return {
        "total_products": len(items),
        "total_units": sum(i["quantity"] for i in items),
        "stock_value": round(sum(i["stock_value"] for i in items), 2),
        "units_on_aircraft": sum(i["on_aircraft"] for i in items),
        "low_stock_count": len(low),
        "out_of_stock_count": len(out),
        "pending_orders": pending_orders,
        "pending_reconciliations": pending_recon,
        "alerts": sorted(low, key=lambda i: i["quantity"]),
        "items": items,
        "recent_movements": movements,
    }


@router.post("/main-inventory/adjust")
def adjust_stock(data: StockAdjust, user: dict = Depends(requires("warehouse_approve"))):
    """Manual correction. Management and inventory supervisors only."""
    if data.quantity == 0:
        raise HTTPException(status_code=400, detail="Adjustment quantity cannot be zero.")
    if not (data.remarks or "").strip():
        raise HTTPException(status_code=400, detail="Remarks are required for a stock adjustment.")
    connection = get_connection()
    try:
        balance = move_stock(connection, data.product_id, data.quantity, "ADJUSTMENT",
                             data.remarks.strip(), user["employee_id"])
        connection.commit()
    finally:
        connection.close()
    log_action(user, "MAIN INVENTORY", "ADJUST_STOCK",
               f"Adjusted stock by {data.quantity:+d}. Remarks: {data.remarks}",
               "product", data.product_id, metadata={"balance_after": balance})
    return {"status": "success", "balance": balance}


# --------------------------------------------------------------------------- #
# Direct stock receipt
# --------------------------------------------------------------------------- #
@router.post("/main-inventory/receive")
def receive_stock(data: StockReceipt, user: dict = Depends(requires("stock_add"))):
    """Add quantity straight into the warehouse, outside the PO flow."""
    if not data.remarks.strip():
        raise HTTPException(status_code=400, detail="Remarks are required when adding stock.")
    connection = get_connection()
    try:
        product = connection.execute(
            "SELECT product_name FROM products WHERE id=? AND active=1", (data.product_id,)
        ).fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found or inactive.")
        balance = move_stock(connection, data.product_id, data.quantity, "DIRECT_RECEIPT",
                             data.remarks.strip(), user["employee_id"])
        connection.commit()
    finally:
        connection.close()
    log_action(user, "MAIN INVENTORY", "RECEIVE_STOCK",
               f"Added {data.quantity} pcs of {product['product_name']}. Remarks: {data.remarks}",
               "product", data.product_id, metadata={"balance_after": balance})
    return {"status": "success", "balance": balance}


# --------------------------------------------------------------------------- #
# PO register
# --------------------------------------------------------------------------- #
SUPPLY_TYPES = {"OBS", "Inflight"}


def _order(connection, order_id):
    row = connection.execute("SELECT * FROM purchase_orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found.")
    order = dict(row)
    order["items"] = []
    for row in connection.execute(
        """SELECT oi.*, p.product_code, p.product_name, p.unit
           FROM purchase_order_items oi
           LEFT JOIN products p ON p.id = oi.product_id
           WHERE oi.order_id=? ORDER BY oi.id""",
        (order_id,),
    ).fetchall():
        item = dict(row)
        item["is_new_product"] = item["product_id"] is None
        # A proposal has no catalogue entry yet, so show what was requested.
        item["product_name"] = item["product_name"] or item["new_product_name"] or "(new product)"
        item["product_code"] = item["product_code"] or item["new_product_code"] or "NEW"
        item["unit"] = item["unit"] or item["new_product_unit"] or "pcs"
        order["items"].append(item)
    order["total_cost"] = round(sum(i["quantity"] * float(i["unit_cost"]) for i in order["items"]), 2)
    order["total_units"] = sum(i["quantity"] for i in order["items"])
    order["total_received"] = sum(i["received_quantity"] for i in order["items"])
    order["outstanding"] = order["total_units"] - order["total_received"]
    # Columns matching the PO register sheet, collapsed to one line each.
    order["purchase_items"] = " / ".join(
        f"{i['product_name']}{' (new)' if i['is_new_product'] else ''}" for i in order["items"])
    order["has_new_products"] = any(i["is_new_product"] for i in order["items"])
    order["qty_text"] = " / ".join(f"{i['quantity']} pcs" for i in order["items"])
    order["received_qty_text"] = " / ".join(
        f"{i['received_quantity']} pcs" for i in order["items"])
    return order


def _fiscal_year(when: str) -> str:
    """Nepali fiscal year label, e.g. 2026-07-17 -> 8384."""
    year, month = int(when[:4]), int(when[5:7])
    bs = year + 57 if month >= 7 else year + 56
    return f"{str(bs)[-2:]}{str(bs + 1)[-2:]}"


@router.get("/orders")
def list_orders(status: str | None = None, supply_type: str | None = None,
                date_from: str | None = None, date_to: str | None = None,
                delivery_from: str | None = None, delivery_to: str | None = None,
                received_from: str | None = None, received_to: str | None = None,
                user: dict = Depends(requires("warehouse"))):
    connection = get_connection()
    try:
        conditions, params = [], []
        if status:
            conditions.append("status=?")
            params.append(status.strip().upper())
        if supply_type:
            conditions.append("supply_type=?")
            params.append(supply_type.strip())
        if date_from:
            conditions.append("order_placement_date >= ?")
            params.append(validate_date(date_from, "from date"))
        if date_to:
            conditions.append("order_placement_date <= ?")
            params.append(validate_date(date_to, "to date"))
        if delivery_from:
            conditions.append("COALESCE(delivery_date, expected_delivery_date) >= ?")
            params.append(validate_date(delivery_from, "delivery from date"))
        if delivery_to:
            conditions.append("COALESCE(delivery_date, expected_delivery_date) <= ?")
            params.append(validate_date(delivery_to, "delivery to date"))
        if received_from:
            conditions.append("received_date >= ?")
            params.append(validate_date(received_from, "received from date"))
        if received_to:
            conditions.append("received_date <= ?")
            params.append(validate_date(received_to, "received to date"))
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = connection.execute(
            f"SELECT id FROM purchase_orders {where} ORDER BY id DESC LIMIT 500", params
        ).fetchall()
        return [_order(connection, r["id"]) for r in rows]
    finally:
        connection.close()


@router.post("/orders")
def create_order(data: OrderIn, user: dict = Depends(requires("warehouse"))):
    if not data.items:
        raise HTTPException(status_code=400, detail="Add at least one product to the order.")
    existing_ids = [i.product_id for i in data.items if i.product_id]
    if len(set(existing_ids)) != len(existing_ids):
        raise HTTPException(status_code=400, detail="The same product cannot be listed twice.")
    if data.supply_type not in SUPPLY_TYPES:
        raise HTTPException(status_code=400, detail="Supply type must be OBS or Inflight.")

    placement = validate_date(data.order_placement_date or now_iso(), "order placement date")
    expected = (validate_date(data.expected_delivery_date, "expected delivery date")
                if data.expected_delivery_date else None)
    if expected and expected < placement:
        raise HTTPException(status_code=400, detail="Delivery date cannot precede the placement date.")

    code = data.po_code.strip().upper() or "ANC"
    fiscal = (data.fiscal_year or _fiscal_year(placement)).strip()

    connection = get_connection()
    try:
        sequence = connection.execute(
            "SELECT COUNT(*) c FROM purchase_orders WHERE po_code=? AND fiscal_year=?",
            (code, fiscal),
        ).fetchone()["c"] + 1
        po_number = f"{sequence:03d}"
        order_no = f"{code}/{po_number}"

        cur = connection.execute(
            """INSERT INTO purchase_orders
                   (order_no,po_code,po_number,fiscal_year,supply_type,order_placement_date,
                    expected_delivery_date,requested_by,requested_at,status,remarks)
               VALUES (?,?,?,?,?,?,?,?,?, 'PENDING', ?)""",
            (order_no, code, po_number, fiscal, data.supply_type, placement, expected,
             user["employee_id"], now_iso(), (data.remarks or "").strip() or None),
        )
        order_id = cur.lastrowid
        for item in data.items:
            if item.product_id:
                product = connection.execute(
                    "SELECT cost FROM products WHERE id=? AND active=1", (item.product_id,)
                ).fetchone()
                if not product:
                    raise HTTPException(status_code=400, detail="A selected product is not active.")
                connection.execute(
                    """INSERT INTO purchase_order_items(order_id,product_id,quantity,unit_cost)
                       VALUES (?,?,?,?)""",
                    (order_id, item.product_id, item.quantity,
                     item.unit_cost or float(product["cost"] or 0)),
                )
                continue

            name = (item.new_product_name or "").strip()
            pcode = (item.new_product_code or "").strip().upper()
            if not name or not pcode:
                raise HTTPException(
                    status_code=400,
                    detail="A new product needs both a code and a name.",
                )
            if connection.execute(
                "SELECT 1 FROM products WHERE product_code=?", (pcode,)
            ).fetchone():
                raise HTTPException(
                    status_code=400,
                    detail=f"Product code {pcode} already exists. Add it as an existing product.",
                )
            connection.execute(
                """INSERT INTO purchase_order_items
                       (order_id,product_id,quantity,unit_cost,new_product_code,new_product_name,
                        new_product_category,new_product_unit,new_selling_price,new_reorder_level)
                   VALUES (?,NULL,?,?,?,?,?,?,?,?)""",
                (order_id, item.quantity, item.unit_cost, pcode, name,
                 (item.new_product_category or "General").strip(),
                 (item.new_product_unit or "pcs").strip(),
                 item.new_selling_price or 0, item.new_reorder_level or 20),
            )
        connection.commit()
        result = _order(connection, order_id)
    finally:
        connection.close()

    log_action(user, "ORDER", "REQUEST_ORDER",
               f"Raised {order_no} ({data.supply_type}) for {result['total_units']} pcs.",
               "purchase_order", order_id, metadata={"items": [i.model_dump() for i in data.items]})
    return result


@router.put("/orders/{order_id}/review")
def review_order(order_id: int, data: OrderReview, user: dict = Depends(requires("warehouse_approve"))):
    """Approve the order. Stock is added when it is received, not when approved."""
    connection = get_connection()
    try:
        order = _order(connection, order_id)
        if order["status"] != "PENDING":
            raise HTTPException(status_code=400, detail=f"This order is already {order['status'].lower()}.")
        if order["requested_by"] == user["employee_id"] and user["role"] != "management":
            raise HTTPException(status_code=403, detail="You cannot approve an order you raised yourself.")
        if not data.approve and not (data.review_remarks or "").strip():
            raise HTTPException(status_code=400, detail="Remarks are required when rejecting an order.")

        status = "APPROVED" if data.approve else "REJECTED"
        created = []
        if data.approve:
            # Proposed products become real catalogue entries only now.
            for item in order["items"]:
                if not item["is_new_product"]:
                    continue
                cur = connection.execute(
                    """INSERT INTO products
                           (product_code,product_name,category,unit,selling_price,cost,
                            reorder_level,active)
                       VALUES (?,?,?,?,?,?,?,1)""",
                    (item["new_product_code"], item["new_product_name"],
                     item["new_product_category"] or "General",
                     item["new_product_unit"] or "pcs",
                     float(item["new_selling_price"] or 0), float(item["unit_cost"] or 0),
                     int(item["new_reorder_level"] or 20)),
                )
                product_id = cur.lastrowid
                connection.execute(
                    "INSERT OR IGNORE INTO main_inventory(product_id,quantity,updated_at) "
                    "VALUES (?,0,?)",
                    (product_id, now_iso()),
                )
                connection.execute(
                    "UPDATE purchase_order_items SET product_id=? WHERE id=?",
                    (product_id, item["id"]),
                )
                created.append(item["new_product_name"])

        connection.execute(
            "UPDATE purchase_orders SET status=?, reviewed_by=?, reviewed_at=?, review_remarks=? WHERE id=?",
            (status, user["employee_id"], now_iso(), (data.review_remarks or "").strip() or None, order_id),
        )
        connection.commit()
        result = _order(connection, order_id)
    finally:
        connection.close()

    log_action(user, "ORDER", f"{status}_ORDER",
               f"{status.title()} order {order['order_no']}."
               + (f" Created product(s): {', '.join(created)}." if created else "")
               + (f" Remarks: {data.review_remarks}" if data.review_remarks else ""),
               "purchase_order", order_id)
    return result


@router.put("/orders/{order_id}/receive")
def receive_order(order_id: int, data: OrderReceipt,
                  user: dict = Depends(requires("warehouse_approve"))):
    """Record what actually arrived. Received pieces post to main inventory."""
    received_date = validate_date(data.received_date, "received date")
    location = data.received_location.strip()
    if not location:
        raise HTTPException(status_code=400, detail="Received location is required.")

    connection = get_connection()
    try:
        order = _order(connection, order_id)
        if any(i["is_new_product"] for i in order["items"]):
            raise HTTPException(
                status_code=400,
                detail="This order still has unapproved new products. Approve it first.",
            )
        if order["status"] not in {"APPROVED", "PARTIAL"}:
            raise HTTPException(
                status_code=400,
                detail="Only an approved order can be received.",
            )
        by_product = {i["product_id"]: i for i in order["items"]}
        moved = []
        for entry in data.items:
            item = by_product.get(entry.product_id)
            if not item:
                raise HTTPException(status_code=400, detail="A product is not on this order.")
            delta = entry.received_quantity - item["received_quantity"]
            if delta == 0:
                continue
            if entry.received_quantity > item["quantity"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"{item['product_name']}: received {entry.received_quantity} pcs "
                           f"exceeds the ordered {item['quantity']} pcs.",
                )
            move_stock(connection, entry.product_id, delta, "ORDER_RECEIPT",
                       f"{order['order_no']} @ {location}", user["employee_id"])
            connection.execute(
                "UPDATE purchase_order_items SET received_quantity=? WHERE order_id=? AND product_id=?",
                (entry.received_quantity, order_id, entry.product_id),
            )
            moved.append(f"{item['product_name']} {delta:+d}")

        totals = connection.execute(
            "SELECT SUM(quantity) q, SUM(received_quantity) r FROM purchase_order_items WHERE order_id=?",
            (order_id,),
        ).fetchone()
        status = "RECEIVED" if int(totals["r"] or 0) >= int(totals["q"] or 0) else "PARTIAL"
        connection.execute(
            """UPDATE purchase_orders SET status=?, received_date=?, received_location=?,
                   delivery_date=?, remarks=COALESCE(?, remarks) WHERE id=?""",
            # The actual delivery date is the date the goods were received.
            (status, received_date, location, received_date,
             (data.remarks or "").strip() or None, order_id),
        )
        connection.commit()
        result = _order(connection, order_id)
    finally:
        connection.close()

    log_action(user, "ORDER", "RECEIVE_ORDER",
               f"{order['order_no']} received at {location} on {received_date}: "
               f"{', '.join(moved) or 'no change'}. Status {status}.",
               "purchase_order", order_id)
    return result


@router.put("/orders/{order_id}")
def edit_order(order_id: int, data: OrderEdit, user: dict = Depends(requires("warehouse_approve"))):
    """Correct a PO register row. Inventory Supervisor only, remarks mandatory."""
    if not data.edit_remarks.strip():
        raise HTTPException(status_code=400, detail="Remarks are mandatory when editing a PO.")

    connection = get_connection()
    try:
        order = _order(connection, order_id)
        if data.supply_type and data.supply_type not in SUPPLY_TYPES:
            raise HTTPException(status_code=400, detail="Supply type must be OBS or Inflight.")

        connection.execute(
            """UPDATE purchase_orders SET
                   supply_type=COALESCE(?,supply_type),
                   order_placement_date=COALESCE(?,order_placement_date),
                   expected_delivery_date=COALESCE(?,expected_delivery_date),
                   received_location=COALESCE(?,received_location),
                   remarks=COALESCE(?,remarks),
                   edited_by=?, edited_at=?, edit_remarks=?
               WHERE id=?""",
            (data.supply_type,
             validate_date(data.order_placement_date, "placement date") if data.order_placement_date else None,
             validate_date(data.expected_delivery_date, "delivery date")
             if data.expected_delivery_date else None,
             (data.received_location or "").strip() or None,
             (data.remarks or "").strip() or None,
             user["employee_id"], now_iso(), data.edit_remarks.strip(), order_id),
        )

        if data.items is not None:
            for entry in data.items:
                existing = next((i for i in order["items"] if i["product_id"] == entry.product_id), None)
                if existing and entry.quantity < existing["received_quantity"]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{existing['product_name']}: {existing['received_quantity']} pcs are "
                               f"already received, so the order cannot drop below that.",
                    )
                if existing:
                    connection.execute(
                        "UPDATE purchase_order_items SET quantity=?, unit_cost=? WHERE id=?",
                        (entry.quantity, entry.unit_cost or existing["unit_cost"], existing["id"]),
                    )
                else:
                    connection.execute(
                        "INSERT INTO purchase_order_items(order_id,product_id,quantity,unit_cost) "
                        "VALUES (?,?,?,?)",
                        (order_id, entry.product_id, entry.quantity, entry.unit_cost),
                    )
        connection.commit()
        result = _order(connection, order_id)
    finally:
        connection.close()

    log_action(user, "ORDER", "EDIT_ORDER",
               f"Edited {order['order_no']}. Remarks: {data.edit_remarks.strip()}",
               "purchase_order", order_id, status="EDITED")
    return result


@router.get("/main-inventory/movements")
def movements(product_id: int | None = None, limit: int = 200,
              user: dict = Depends(requires("warehouse"))):
    connection = get_connection()
    try:
        sql = """SELECT t.*, p.product_code, p.product_name
                 FROM main_inventory_txn t JOIN products p ON p.id = t.product_id"""
        params = []
        if product_id:
            sql += " WHERE t.product_id=?"
            params.append(product_id)
        sql += " ORDER BY t.id DESC LIMIT ?"
        params.append(min(limit, 500))
        return [dict(r) for r in connection.execute(sql, params).fetchall()]
    finally:
        connection.close()
