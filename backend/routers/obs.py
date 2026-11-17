"""Per-flight (OBS) inventory and post-flight reconciliation.

Loading a product onto a flight moves stock out of main inventory
automatically. When the crew declare what came back and the inventory
supervisor approves, that quantity returns to main inventory.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import trip_flow
from database import get_connection
from paging import PageParam, PageSizeParam, paginate_list, paginate_sql
from routers.warehouse import move_stock
from security import (
    current_user, log_action, now_iso, permissions_for, requires,
    trip_legs, trip_root,
)

router = APIRouter(tags=["obs"])
logger = logging.getLogger("obs")


class LoadLine(BaseModel):
    product_id: int
    loaded_quantity: int = Field(ge=0)


class LoadRequest(BaseModel):
    flight_id: int
    lines: list[LoadLine]
    remarks: str | None = None


class WastageUpdate(BaseModel):
    wasted_quantity: int = Field(ge=0)
    remarks: str


class ReconLine(BaseModel):
    product_id: int
    declared_remaining: int = Field(ge=0)


class ReconSubmit(BaseModel):
    """The crew's physical count of what came off the aircraft.

    The system knows what *should* be left. What the crew type here is what is
    actually in the trolley, and the difference between the two is the variance
    the inventory supervisor reviews.
    """

    flight_id: int
    lines: list[ReconLine] = []
    remarks: str | None = None


class ReconAdjust(BaseModel):
    product_id: int
    declared_remaining: int = Field(ge=0)
    remarks: str


class ReconReview(BaseModel):
    approve: bool
    review_remarks: str | None = None


def _inventory_rows(connection, flight_id):
    rows = connection.execute(
        """SELECT i.*, p.product_code, p.product_name, p.category, p.unit, p.selling_price,
                  COALESCE(m.quantity,0) AS main_stock
           FROM inventory i
           JOIN products p ON p.id = i.product_id
           LEFT JOIN main_inventory m ON m.product_id = p.id
           WHERE i.flight_id=? ORDER BY p.category, p.product_name""",
        (flight_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["remaining_quantity"] = (
            item["loaded_quantity"] - item["sold_quantity"]
            - item["returned_quantity"] - item["wasted_quantity"]
        )
        result.append(item)
    return result


@router.get("/inventory/{flight_id}")
def flight_inventory(flight_id: int, user: dict = Depends(current_user)):
    connection = get_connection()
    try:
        if not connection.execute("SELECT 1 FROM flights WHERE id=?", (flight_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Flight not found.")
        # Both legs of a round trip read the same trolley.
        return _inventory_rows(connection, trip_root(connection, flight_id))
    finally:
        connection.close()


@router.post("/inventory/load")
def load_flight(data: LoadRequest, user: dict = Depends(requires("inventory"))):
    """Set the loaded quantity per product, moving the delta from main inventory."""
    connection = get_connection()
    try:
        root_id = trip_root(connection, data.flight_id)
        flight = connection.execute("SELECT * FROM flights WHERE id=?", (root_id,)).fetchone()
        if not flight:
            raise HTTPException(status_code=404, detail="Flight not found.")
        if flight["status"] == "CLOSED":
            raise HTTPException(status_code=400, detail="This flight is closed and cannot be reloaded.")

        changes = []
        for line in data.lines:
            product = connection.execute(
                "SELECT * FROM products WHERE id=? AND active=1", (line.product_id,)
            ).fetchone()
            if not product:
                raise HTTPException(status_code=400, detail="A selected product is not active.")

            existing = connection.execute(
                "SELECT * FROM inventory WHERE flight_id=? AND product_id=?",
                (root_id, line.product_id),
            ).fetchone()
            current_loaded = int(existing["loaded_quantity"]) if existing else 0
            consumed = 0
            if existing:
                consumed = (existing["sold_quantity"] + existing["returned_quantity"]
                            + existing["wasted_quantity"])
            if line.loaded_quantity < consumed:
                raise HTTPException(
                    status_code=400,
                    detail=f"{product['product_name']}: {consumed} unit(s) are already accounted for, "
                           f"so the load cannot drop below that.",
                )

            delta = line.loaded_quantity - current_loaded
            if delta:
                # Positive delta leaves the warehouse; negative returns to it.
                move_stock(connection, line.product_id, -delta, "OBS_LOAD",
                           f"Flight {flight['flight_number']} {flight['flight_date']}",
                           user["employee_id"], stock_date=flight["flight_date"])
                changes.append((product["product_name"], delta))

            if existing:
                connection.execute(
                    "UPDATE inventory SET loaded_quantity=?, entered_by=?, entered_at=? WHERE id=?",
                    (line.loaded_quantity, user["employee_id"], now_iso(), existing["id"]),
                )
            else:
                connection.execute(
                    """INSERT INTO inventory(flight_id,product_id,loaded_quantity,entered_by,entered_at)
                       VALUES (?,?,?,?,?)""",
                    (root_id, line.product_id, line.loaded_quantity,
                     user["employee_id"], now_iso()),
                )
        connection.commit()
        result = _inventory_rows(connection, root_id)
    finally:
        connection.close()

    if changes:
        summary = ", ".join(f"{name} {delta:+d}" for name, delta in changes)
        log_action(user, "OBS INVENTORY", "LOAD_FLIGHT",
                   f"Loaded {flight['flight_number']} ({flight['flight_date']}): {summary}.",
                   "flight", data.flight_id, metadata={"remarks": data.remarks})
    return result


@router.put("/inventory/{inventory_id}/wastage")
def record_wastage(inventory_id: int, data: WastageUpdate,
                   user: dict = Depends(requires("inventory"))):
    if not data.remarks.strip():
        raise HTTPException(status_code=400, detail="Remarks are required when recording wastage.")
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM inventory WHERE id=?", (inventory_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Inventory line not found.")
        ceiling = row["loaded_quantity"] - row["sold_quantity"] - row["returned_quantity"]
        if data.wasted_quantity > ceiling:
            raise HTTPException(status_code=400, detail=f"Wastage cannot exceed {ceiling} unit(s).")
        connection.execute(
            "UPDATE inventory SET wasted_quantity=?, entered_by=?, entered_at=? WHERE id=?",
            (data.wasted_quantity, user["employee_id"], now_iso(), inventory_id),
        )
        connection.commit()
    finally:
        connection.close()
    log_action(user, "OBS INVENTORY", "RECORD_WASTAGE",
               f"Recorded {data.wasted_quantity} unit(s) of wastage. Remarks: {data.remarks}",
               "inventory", inventory_id)
    return {"status": "success"}


# --------------------------------------------------------------------------- #
# Reconciliation — one per trip, covering every leg
# --------------------------------------------------------------------------- #
def _trip_devices(connection, root_id):
    """The iPad account(s) that worked the trip.

    One device works the whole trip, so this is normally a single account. It
    stays a list because a device can be swapped mid-trip after a fault.
    """
    accounts = []
    for leg in trip_legs(connection, root_id):
        for row in connection.execute(
                "SELECT crew_account FROM flight_assignments WHERE flight_id=? ORDER BY id", (leg,)):
            if row["crew_account"] and row["crew_account"] not in accounts:
                accounts.append(row["crew_account"])
    return accounts


def _trip_label(connection, root_id):
    rows = connection.execute(
        """SELECT id, flight_number, direction, origin, destination, flight_date, status
           FROM flights WHERE id=? OR linked_flight_id=? ORDER BY direction ASC""",
        (root_id, root_id),
    ).fetchall()
    return [dict(r) for r in rows]


def _recon(connection, recon_id=None, flight_id=None):
    """The trip's leftover position.

    Quantities are worked out by the system: loaded once for the trip, minus
    everything sold on either leg, minus wastage. A supervisor can override a
    line if the physical count disagrees, and that override is kept with its
    remarks rather than replacing the computed figure.
    """
    if recon_id is not None:
        row = connection.execute("SELECT * FROM reconciliations WHERE id=?", (recon_id,)).fetchone()
    else:
        root = trip_root(connection, flight_id)
        row = connection.execute(
            "SELECT * FROM reconciliations WHERE flight_id=?", (root,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Stock return not found.")

    recon = dict(row)
    root_id = recon["flight_id"]
    legs = _trip_label(connection, root_id)
    recon["legs"] = legs
    outbound = legs[0] if legs else {}
    recon["flight_number"] = " / ".join(l["flight_number"] for l in legs)
    recon["flight_date"] = outbound.get("flight_date")
    recon["origin"] = outbound.get("origin")
    recon["destination"] = outbound.get("destination")
    recon["round_trip"] = len(legs) > 1

    recon["devices"] = _trip_devices(connection, root_id)
    device_rows = {r["device"]: dict(r) for r in connection.execute(
        "SELECT * FROM stock_return_devices WHERE reconciliation_id=?", (recon["id"],)).fetchall()}
    recon["submitted_devices"] = [d for d in recon["devices"] if d in device_rows] \
        + [d for d in device_rows if d not in recon["devices"]]
    recon["waiting_for"] = [] if "*" in device_rows else [d for d in recon["devices"] if d not in device_rows]
    recon["device_remarks"] = {d: r.get("remarks") for d, r in device_rows.items()}
    by_device = {}
    for r in connection.execute(
            "SELECT device, product_id, declared_remaining FROM stock_return_device_items WHERE reconciliation_id=?",
            (recon["id"],)):
        by_device.setdefault(r["product_id"], {})[r["device"]] = int(r["declared_remaining"])

    overrides = {
        r["product_id"]: dict(r)
        for r in connection.execute(
            "SELECT * FROM reconciliation_items WHERE reconciliation_id=?", (recon["id"],)
        ).fetchall()
    }

    items = []
    for line in _inventory_rows(connection, root_id):
        override = overrides.get(line["product_id"])
        expected = line["remaining_quantity"]
        # What the crew counted, unless a supervisor has since corrected it.
        if override is not None:
            declared = int(override["declared_remaining"])
        else:
            declared = expected
        items.append({
            "product_id": line["product_id"],
            "product_code": line["product_code"],
            "product_name": line["product_name"],
            "unit": line["unit"],
            "loaded_quantity": line["loaded_quantity"],
            "sold_quantity": line["sold_quantity"],
            "wasted_quantity": line["wasted_quantity"],
            "expected_remaining": expected,
            "declared_remaining": declared,
            "variance": declared - expected,
            "counted": override is not None,
            "overridden": bool(override and override["override_by"]),
            "override_by": override["override_by"] if override else None,
            "override_remarks": override["override_remarks"] if override else None,
            "by_device": by_device.get(line["product_id"], {}),
        })
    recon["items"] = items
    recon["total_expected"] = sum(i["expected_remaining"] for i in items)
    recon["total_declared"] = sum(i["declared_remaining"] for i in items)
    recon["total_variance"] = sum(i["variance"] for i in items)
    recon["has_variance"] = any(i["variance"] != 0 for i in items)
    return recon


@router.get("/reconciliations")
def list_reconciliations(status: str | None = None,
                         page: int | None = PageParam, page_size: int = PageSizeParam,
                         user: dict = Depends(requires("sync"))):
    connection = get_connection()
    try:
        sql = "SELECT r.id FROM reconciliations r"
        params, conditions = [], []
        if status:
            conditions.append("r.status=?")
            params.append(status.strip().upper())
        if "logs" not in permissions_for(user["role"]) and user["role"] == "crew_supervisor":
            conditions.append(
                """r.flight_id IN (SELECT flight_id FROM flight_assignments
                                   WHERE crew_account=?)"""
            )
            params.append(user["employee_id"])
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        if page:
            return paginate_sql(connection, sql + " ORDER BY r.id DESC", params, page, page_size,
                                mapper=lambda r: _recon(connection, recon_id=r["id"]))
        sql += " ORDER BY r.id DESC LIMIT 200"
        return [_recon(connection, recon_id=r["id"])
                for r in connection.execute(sql, params).fetchall()]
    finally:
        connection.close()


@router.get("/reconciliations/pending")
def my_pending_return(user: dict = Depends(requires("sync"))):
    """The trip this device still has to confirm, if any.

    The crew do not pick a flight: a trip falls due once every one of its legs
    has been finished, and there is only ever one waiting.
    """
    connection = get_connection()
    try:
        rows = connection.execute(
            """SELECT DISTINCT a.flight_id FROM flight_assignments a
               JOIN flights f ON f.id = a.flight_id
               WHERE a.crew_account=?
                 AND f.status IN ('OPEN','FLOWN')
                 AND f.flight_date >= date('now','-5 day')""",
            (user["employee_id"],),
        ).fetchall()

        roots = {trip_root(connection, row["flight_id"]) for row in rows}
        if user["role"] == "crew_supervisor":
            # Only the trip this iPad is working now (7.11): the earliest trip of
            # the day not yet finished with "Finish whole flight". Older trips
            # and the next one on the roster stay off the page.
            from routers.flights import _crew_visible_rows
            visible = {trip_root(connection, r["id"]) for r in _crew_visible_rows(connection, user["employee_id"])
                       if not r["my_completed_at"]}
            roots &= visible

        pending = []
        for root_id in roots:
            # One unusable trip must not take the whole page down with it, so
            # each is assembled on its own and logged if it fails.
            try:
                entry = _pending_entry(connection, root_id, user)
            except Exception:  # noqa: BLE001 - reported, not fatal
                logger.exception("Could not build the stock return for flight %s", root_id)
                continue
            if entry:
                pending.append(entry)

        pending.sort(key=lambda r: r["flight_date"] or "", reverse=True)
        return pending
    finally:
        connection.close()


def _pending_entry(connection, root_id, user):
    """One trip's outstanding stock return, or None if it is not due."""
    legs = _trip_label(connection, root_id)
    if not legs:
        return None
    devices = _trip_devices(connection, root_id)
    own = user["employee_id"] if user["employee_id"] in devices else None
    # 7.12: due once THIS iPad has finished every leg (Management: once the leg is FLOWN).
    if any(not trip_flow.leg_finished_for(connection, leg, own) for leg in legs):
        return None  # still flying, or nothing to report

    existing = connection.execute(
        "SELECT id FROM reconciliations WHERE flight_id=?", (root_id,)
    ).fetchone()

    if existing:
        recon = _recon(connection, recon_id=existing["id"])
        if recon["status"] == "APPROVED":
            return None
    else:
        devices = _trip_devices(connection, root_id)
        items = [
            {
                "product_id": line["product_id"],
                "product_code": line["product_code"],
                "product_name": line["product_name"],
                "unit": line["unit"],
                "loaded_quantity": line["loaded_quantity"],
                "sold_quantity": line["sold_quantity"],
                "wasted_quantity": line["wasted_quantity"],
                "expected_remaining": line["remaining_quantity"],
                "declared_remaining": line["remaining_quantity"],
                "variance": 0,
                "counted": False,
                "overridden": False,
            }
            for line in _inventory_rows(connection, root_id)
        ]
        recon = {
            "id": None,
            "flight_id": root_id,
            "status": "NOT_SUBMITTED",
            "legs": legs,
            "round_trip": len(legs) > 1,
            "flight_number": " / ".join(leg["flight_number"] for leg in legs),
            "flight_date": legs[0]["flight_date"],
            "origin": legs[0]["origin"],
            "destination": legs[0]["destination"],
            "devices": devices,
            "submitted_devices": [], "waiting_for": list(devices), "device_remarks": {},
            "review_remarks": None,
            "items": items,
            "total_expected": sum(i["expected_remaining"] for i in items),
            "total_declared": sum(i["expected_remaining"] for i in items),
            "total_variance": 0,
            "has_variance": False,
        }

    recon["submitted"] = recon["status"] not in ("NOT_SUBMITTED", "WAITING")
    recon["my_submitted"] = user["employee_id"] in recon.get("submitted_devices", [])
    return recon


@router.post("/reconciliations")
def submit_reconciliation(
    data: ReconSubmit,
    user: dict = Depends(requires("reconcile_submit")),
):
    """Declare what physically came off the aircraft at the end of the trip.

    7.11: two iPads work the trip and each holds part of the trolley, so each
    device declares what IT can see; the trip's remaining stock is the sum of
    the devices. The return waits (status WAITING) until every assigned iPad
    has declared, then goes to the Inventory Supervisor (PENDING). A device
    that sends no lines at all confirms the system figure less what the other
    device declared. A supervisor or manager submitting is taken as the final
    total (device "*").
    """
    connection = get_connection()
    try:
        root_id = trip_root(connection, data.flight_id)
        legs = _trip_label(connection, root_id)
        if not legs:
            raise HTTPException(status_code=404, detail="Flight not found.")
        devices = _trip_devices(connection, root_id)
        own_device = user["employee_id"] if user["employee_id"] in devices else None
        for l in legs:
            # 7.12: this iPad must have finished its own selling on every leg;
            # the other iPad's progress does not hold it up.
            if not trip_flow.leg_finished_for(connection, l, own_device):
                raise HTTPException(
                    status_code=400,
                    detail="Finish every sector of the trip before declaring the stock return.",
                )

        if devices and user["employee_id"] not in devices \
                and "reconcile_approve" not in permissions_for(user["role"]):
            raise HTTPException(
                status_code=403, detail="This device did not work that trip.",
            )

        header = connection.execute(
            "SELECT * FROM reconciliations WHERE flight_id=?", (root_id,)
        ).fetchone()
        if header and header["status"] == "APPROVED":
            raise HTTPException(
                status_code=400,
                detail="This stock return has been approved and can no longer be changed.",
            )
        is_device = user["employee_id"] in devices
        device = user["employee_id"] if is_device else "*"
        # Once every iPad has declared it waits for the Inventory Supervisor;
        # the crew count again only if it is sent back.
        if header and header["status"] == "PENDING" \
                and "reconcile_approve" not in permissions_for(user["role"]):
            raise HTTPException(
                status_code=400,
                detail="The stock return has already been submitted and is waiting for approval.",
            )

        now = now_iso()
        if header:
            recon_id = header["id"]
            if header["status"] == "REJECTED":
                # Sent back: every device counts again.
                connection.execute("DELETE FROM stock_return_devices WHERE reconciliation_id=?", (recon_id,))
                connection.execute("DELETE FROM stock_return_device_items WHERE reconciliation_id=?", (recon_id,))
        else:
            cur = connection.execute(
                """INSERT INTO reconciliations(flight_id,submitted_by,submitted_at,status)
                   VALUES (?,?,?,'WAITING')""",
                (root_id, user["employee_id"], now),
            )
            recon_id = cur.lastrowid

        expected_by_product = {
            line["product_id"]: line for line in _inventory_rows(connection, root_id)
        }
        # This device's own lines. No lines at all = confirm the system figure
        # less what the other device(s) already declared.
        others = {}
        for r in connection.execute(
                "SELECT device, product_id, declared_remaining FROM stock_return_device_items "
                "WHERE reconciliation_id=? AND device<>?", (recon_id, device)):
            others[r["product_id"]] = others.get(r["product_id"], 0) + int(r["declared_remaining"])
        if device == "*":
            others = {}
            connection.execute("DELETE FROM stock_return_device_items WHERE reconciliation_id=?", (recon_id,))
            connection.execute("DELETE FROM stock_return_devices WHERE reconciliation_id=?", (recon_id,))
        typed = {line.product_id: line.declared_remaining for line in data.lines}
        connection.execute(
            """INSERT INTO stock_return_devices(reconciliation_id,device,submitted_at,remarks) VALUES (?,?,?,?)
               ON CONFLICT(reconciliation_id,device) DO UPDATE SET submitted_at=excluded.submitted_at,
               remarks=excluded.remarks""",
            (recon_id, device, now, (data.remarks or "").strip() or None))
        for product_id, line in expected_by_product.items():
            expected = line["remaining_quantity"]
            mine = typed.get(product_id, max(0, expected - others.get(product_id, 0)) if not typed else 0)
            connection.execute(
                """INSERT INTO stock_return_device_items(reconciliation_id,device,product_id,declared_remaining)
                   VALUES (?,?,?,?) ON CONFLICT(reconciliation_id,device,product_id)
                   DO UPDATE SET declared_remaining=excluded.declared_remaining""",
                (recon_id, device, product_id, mine))

        # The trip's declared figure is the sum of the devices.
        submitted = {r["device"] for r in connection.execute(
            "SELECT device FROM stock_return_devices WHERE reconciliation_id=?", (recon_id,))}
        complete = "*" in submitted or (bool(devices) and all(d in submitted for d in devices))
        connection.execute(
            """UPDATE reconciliations SET status=?, submitted_by=?, submitted_at=?, review_remarks=NULL WHERE id=?""",
            ("PENDING" if complete else "WAITING", user["employee_id"], now, recon_id))
        totals = {}
        for r in connection.execute(
                "SELECT product_id, SUM(declared_remaining) q FROM stock_return_device_items "
                "WHERE reconciliation_id=? GROUP BY product_id", (recon_id,)):
            totals[r["product_id"]] = int(r["q"] or 0)
        for product_id, line in expected_by_product.items():
            expected = line["remaining_quantity"]
            declared = totals.get(product_id, expected)
            connection.execute(
                """INSERT INTO reconciliation_items
                       (reconciliation_id,product_id,loaded_quantity,sold_quantity,
                        expected_remaining,declared_remaining,variance)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(reconciliation_id,product_id) DO UPDATE SET
                       loaded_quantity=excluded.loaded_quantity,
                       sold_quantity=excluded.sold_quantity,
                       expected_remaining=excluded.expected_remaining,
                       declared_remaining=excluded.declared_remaining,
                       variance=excluded.variance""",
                (recon_id, product_id, line["loaded_quantity"], line["sold_quantity"],
                 expected, declared, declared - expected),
            )
        connection.commit()
        result = _recon(connection, recon_id)
        result["my_submitted"] = True
    finally:
        connection.close()

    variance_note = (f"{result['total_variance']:+d} unit(s) against the system figure."
                     if result["has_variance"] else "No variance.")
    waiting = (f" Waiting for {', '.join(result['waiting_for'])}." if result.get("waiting_for") else "")
    log_action(user, "RECONCILIATION", "DECLARE_STOCK_RETURN",
               f"Stock return declared for {result['flight_number']} by {device if device != '*' else user['employee_id']}: "
               f"{result['total_declared']} unit(s) returning so far. {variance_note}{waiting}"
               + (f" Remarks: {data.remarks.strip()}" if (data.remarks or '').strip() else ""),
               "flight", root_id,
               metadata={"total_declared": result["total_declared"],
                         "total_expected": result["total_expected"],
                         "variance": result["total_variance"],
                         "remarks": (data.remarks or "").strip() or None})
    return result


@router.put("/reconciliations/{recon_id}/adjust")
def adjust_reconciliation(recon_id: int, data: ReconAdjust,
                          user: dict = Depends(requires("reconcile_approve"))):
    """Correct a system figure that does not match the physical count."""
    if not data.remarks.strip():
        raise HTTPException(status_code=400, detail="Remarks are mandatory when changing a figure.")
    connection = get_connection()
    try:
        recon = _recon(connection, recon_id=recon_id)
        if recon["status"] == "APPROVED":
            raise HTTPException(status_code=400, detail="Already approved.")
        line = next((i for i in recon["items"] if i["product_id"] == data.product_id), None)
        if not line:
            raise HTTPException(status_code=404, detail="That product was not loaded on this trip.")

        connection.execute(
            """INSERT INTO reconciliation_items
                   (reconciliation_id,product_id,loaded_quantity,sold_quantity,
                    expected_remaining,declared_remaining,variance,
                    override_by,override_at,override_remarks)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(reconciliation_id,product_id) DO UPDATE SET
                   declared_remaining=excluded.declared_remaining,
                   expected_remaining=excluded.expected_remaining,
                   variance=excluded.variance,
                   override_by=excluded.override_by, override_at=excluded.override_at,
                   override_remarks=excluded.override_remarks""",
            (recon_id, data.product_id, line["loaded_quantity"], line["sold_quantity"],
             line["expected_remaining"], data.declared_remaining,
             data.declared_remaining - line["expected_remaining"],
             user["employee_id"], now_iso(), data.remarks.strip()),
        )
        connection.commit()
        result = _recon(connection, recon_id=recon_id)
    finally:
        connection.close()

    log_action(user, "RECONCILIATION", "ADJUST_STOCK_RETURN",
               f"{line['product_name']}: system said {line['expected_remaining']}, "
               f"recorded {data.declared_remaining}. Remarks: {data.remarks.strip()}",
               "flight", recon["flight_id"], status="EDITED")
    return result


@router.put("/reconciliations/{recon_id}/review")
def review_reconciliation(recon_id: int, data: ReconReview,
                          user: dict = Depends(requires("reconcile_approve"))):
    connection = get_connection()
    try:
        recon = _recon(connection, recon_id=recon_id)
        if recon["status"] in {"APPROVED", "REJECTED"}:
            raise HTTPException(status_code=400, detail=f"Already {recon['status'].lower()}.")
        if not data.approve and not (data.review_remarks or "").strip():
            raise HTTPException(status_code=400, detail="Remarks are required when rejecting.")
        if data.approve and recon["total_variance"] > 0 and not (data.review_remarks or "").strip():
            raise HTTPException(
                status_code=400,
                detail="More stock was counted than the system expected. Explain the difference "
                       "in the remarks before approving.",
            )

        status = "APPROVED" if data.approve else "REJECTED"
        root_id = recon["flight_id"]

        # Claim the review first, in one statement. Without this two
        # supervisors approving at the same moment would both pass the status
        # check above and both credit the warehouse.
        claimed = connection.execute(
            """UPDATE reconciliations SET status=?, reviewed_by=?, reviewed_at=?, review_remarks=?
               WHERE id=? AND status='PENDING'""",
            (status, user["employee_id"], now_iso(),
             (data.review_remarks or "").strip() or None, recon_id),
        )
        if claimed.rowcount == 0:
            connection.rollback()
            raise HTTPException(
                status_code=409,
                detail="This stock return has just been reviewed by someone else.",
            )

        if data.approve:
            reference = f"Trip {recon['flight_number']} {recon['flight_date']}"
            for item in recon["items"]:
                connection.execute(
                    """INSERT INTO reconciliation_items
                           (reconciliation_id,product_id,loaded_quantity,sold_quantity,
                            expected_remaining,declared_remaining,variance)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(reconciliation_id,product_id) DO UPDATE SET
                           loaded_quantity=excluded.loaded_quantity,
                           sold_quantity=excluded.sold_quantity,
                           expected_remaining=excluded.expected_remaining,
                           declared_remaining=excluded.declared_remaining,
                           variance=excluded.variance""",
                    (recon_id, item["product_id"], item["loaded_quantity"], item["sold_quantity"],
                     item["expected_remaining"], item["declared_remaining"], item["variance"]),
                )
                # Never credit back more than physically left the warehouse on
                # this trip: anything above that is a counting error, not stock.
                quantity = max(0, min(int(item["declared_remaining"]),
                                      int(item["expected_remaining"])))
                if quantity > 0:
                    connection.execute(
                        """UPDATE inventory SET returned_quantity = returned_quantity + ?
                           WHERE flight_id=? AND product_id=?""",
                        (quantity, root_id, item["product_id"]),
                    )
                    # Writes to main_inventory_txn, so every returned unit is
                    # traceable in the warehouse movement ledger.
                    move_stock(connection, item["product_id"], quantity, "OBS_RETURN",
                               reference, user["employee_id"], stock_date=recon["flight_date"])

            for leg in trip_legs(connection, root_id):
                connection.execute("UPDATE flights SET status='CLOSED' WHERE id=?", (leg,))
            connection.execute(
                "UPDATE inventory SET inventory_locked=1 WHERE flight_id=?", (root_id,))

        connection.commit()
        result = _recon(connection, recon_id=recon_id)
    except HTTPException:
        connection.rollback()
        raise
    finally:
        connection.close()

    log_action(user, "RECONCILIATION", f"{status}_STOCK_RETURN",
               f"{status.title()} the stock return for {recon['flight_number']}: "
               f"{recon['total_declared']} unit(s) back to main inventory."
               + (f" Remarks: {data.review_remarks}" if data.review_remarks else ""),
               "flight", recon["flight_id"])
    return result
