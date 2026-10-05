"""OBS daily stock register.

Reproduces the monthly sheet: one row per product, an opening balance, then
Received / Sales / Closing for each day of the month, where

    closing = opening + received - sales

and each day's closing becomes the next day's opening.

Received and Sales are derived from what actually happened — trolley loads out
of main inventory and sales recorded on board — so the register cannot silently
disagree with the rest of the system. An Inventory Supervisor can override a
day's figures, but only with mandatory remarks, and the override is kept
separately so the computed value is never lost.
"""

from calendar import monthrange
from datetime import date, datetime
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from openpyxl import load_workbook
from pydantic import BaseModel, Field

from database import get_connection
from security import current_user, log_action, now_iso, requires

router = APIRouter(tags=["register"])


class OpeningIn(BaseModel):
    product_id: int
    month: str
    quantity: int = Field(ge=0)


class AdjustmentIn(BaseModel):
    stock_date: str
    product_id: int
    received_qty: int | None = Field(default=None, ge=0)
    sales_qty: int | None = Field(default=None, ge=0)
    remarks: str


def _month_bounds(month: str):
    try:
        year, mon = (int(part) for part in month.split("-"))
        days = monthrange(year, mon)[1]
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Month must be in the YYYY-MM format.")
    return (
        date(year, mon, 1).isoformat(),
        date(year, mon, days).isoformat(),
        [date(year, mon, d).isoformat() for d in range(1, days + 1)],
    )


@router.get("/obs-register")
def obs_register(month: str | None = None, user: dict = Depends(requires("inventory"))):
    month = month or date.today().strftime("%Y-%m")
    first, last, days = _month_bounds(month)

    connection = get_connection()
    try:
        products = connection.execute(
            "SELECT id, product_code, product_name, category, unit FROM products WHERE active=1 "
            "ORDER BY id"
        ).fetchall()

        # Received into OBS = positive trolley loads recorded against the ledger.
        received = {}
        for row in connection.execute(
            """SELECT COALESCE(stock_date, substr(created_at,1,10)) AS day,
                      product_id, SUM(-change_qty) AS qty
               FROM main_inventory_txn
               WHERE reason='OBS_LOAD' AND change_qty < 0
                 AND COALESCE(stock_date, substr(created_at,1,10)) BETWEEN ? AND ?
               GROUP BY day, product_id""",
            (first, last),
        ).fetchall():
            received[(row["day"], row["product_id"])] = int(row["qty"] or 0)

        # Sales = units that left the trolley against a passenger.
        sales = {}
        for row in connection.execute(
            """SELECT substr(transaction_time,1,10) AS day, product_id, SUM(quantity) AS qty
               FROM sales
               WHERE sale_type IN ('NORMAL','COMPLEMENTARY')
                 AND substr(transaction_time,1,10) BETWEEN ? AND ?
               GROUP BY day, product_id""",
            (first, last),
        ).fetchall():
            sales[(row["day"], row["product_id"])] = int(row["qty"] or 0)

        openings = {
            row["product_id"]: int(row["quantity"])
            for row in connection.execute(
                "SELECT product_id, quantity FROM obs_register_opening WHERE month=?", (month,)
            ).fetchall()
        }

        adjustments = {
            (row["stock_date"], row["product_id"]): dict(row)
            for row in connection.execute(
                "SELECT * FROM obs_register_adjustment WHERE stock_date BETWEEN ? AND ?",
                (first, last),
            ).fetchall()
        }
    finally:
        connection.close()

    rows = []
    for index, product in enumerate(products, start=1):
        opening = openings.get(product["id"], 0)
        balance = opening
        cells = []
        for day in days:
            key = (day, product["id"])
            adjustment = adjustments.get(key)
            computed_received = received.get(key, 0)
            computed_sales = sales.get(key, 0)

            day_received = computed_received
            day_sales = computed_sales
            if adjustment:
                if adjustment["received_qty"] is not None:
                    day_received = int(adjustment["received_qty"])
                if adjustment["sales_qty"] is not None:
                    day_sales = int(adjustment["sales_qty"])

            balance = balance + day_received - day_sales
            cells.append({
                "date": day,
                "received_qty": day_received,
                "sales_qty": day_sales,
                "closing_qty": balance,
                "adjusted": bool(adjustment),
                "computed_received": computed_received,
                "computed_sales": computed_sales,
                "remarks": adjustment["remarks"] if adjustment else None,
                "edited_by": adjustment["edited_by"] if adjustment else None,
            })

        rows.append({
            "sno": index,
            "product_id": product["id"],
            "product_code": product["product_code"],
            "product_name": product["product_name"],
            "category": product["category"],
            "unit": product["unit"],
            "opening_qty": opening,
            "closing_qty": balance,
            "total_received": sum(c["received_qty"] for c in cells),
            "total_sales": sum(c["sales_qty"] for c in cells),
            "days": cells,
        })

    return {"month": month, "dates": days, "rows": rows}


@router.get("/obs-register/day")
def obs_register_day(stock_date: str, user: dict = Depends(requires("inventory"))):
    """A single day pulled out of the register, for the date filter."""
    month = stock_date[:7]
    register = obs_register(month=month, user=user)
    return {
        "date": stock_date,
        "rows": [
            {
                **{k: row[k] for k in ("sno", "product_id", "product_code", "product_name", "unit")},
                **next((c for c in row["days"] if c["date"] == stock_date), {}),
                "opening_qty": next(
                    (c["closing_qty"] - c["received_qty"] + c["sales_qty"]
                     for c in row["days"] if c["date"] == stock_date), row["opening_qty"]),
            }
            for row in register["rows"]
        ],
    }


@router.post("/obs-register/opening")
def set_opening(data: OpeningIn, user: dict = Depends(requires("reconcile_approve"))):
    """Seed the opening balance for a month. Inventory Supervisor / management only."""
    connection = get_connection()
    try:
        connection.execute(
            """INSERT INTO obs_register_opening(product_id,month,quantity,set_by,set_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(product_id,month) DO UPDATE SET
                   quantity=excluded.quantity, set_by=excluded.set_by, set_at=excluded.set_at""",
            (data.product_id, data.month, data.quantity, user["employee_id"], now_iso()),
        )
        connection.commit()
    finally:
        connection.close()
    log_action(user, "OBS REGISTER", "SET_OPENING",
               f"Opening balance for {data.month} set to {data.quantity}.",
               "product", data.product_id)
    return {"status": "success"}


@router.post("/obs-register/adjust")
def adjust_register(data: AdjustmentIn, user: dict = Depends(requires("reconcile_approve"))):
    """Override one product's figures for one day. Remarks are mandatory."""
    if not data.remarks.strip():
        raise HTTPException(status_code=400, detail="Remarks are mandatory when editing the register.")
    if data.received_qty is None and data.sales_qty is None:
        raise HTTPException(status_code=400, detail="Provide a received or sales quantity to override.")

    connection = get_connection()
    try:
        product = connection.execute(
            "SELECT product_name FROM products WHERE id=?", (data.product_id,)
        ).fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found.")
        connection.execute(
            """INSERT INTO obs_register_adjustment
                   (stock_date,product_id,received_qty,sales_qty,remarks,edited_by,edited_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(stock_date,product_id) DO UPDATE SET
                   received_qty=excluded.received_qty, sales_qty=excluded.sales_qty,
                   remarks=excluded.remarks, edited_by=excluded.edited_by,
                   edited_at=excluded.edited_at""",
            (data.stock_date, data.product_id, data.received_qty, data.sales_qty,
             data.remarks.strip(), user["employee_id"], now_iso()),
        )
        connection.commit()
    finally:
        connection.close()

    log_action(user, "OBS REGISTER", "ADJUST_REGISTER",
               f"{product['product_name']} on {data.stock_date}: "
               f"received={data.received_qty if data.received_qty is not None else 'auto'}, "
               f"sales={data.sales_qty if data.sales_qty is not None else 'auto'}. "
               f"Remarks: {data.remarks.strip()}",
               "product", data.product_id, status="EDITED")
    return {"status": "success"}


@router.delete("/obs-register/adjust")
def clear_adjustment(stock_date: str, product_id: int,
                     user: dict = Depends(requires("reconcile_approve"))):
    connection = get_connection()
    try:
        connection.execute(
            "DELETE FROM obs_register_adjustment WHERE stock_date=? AND product_id=?",
            (stock_date, product_id),
        )
        connection.commit()
    finally:
        connection.close()
    log_action(user, "OBS REGISTER", "CLEAR_ADJUSTMENT",
               f"Reverted the manual override on {stock_date}.", "product", product_id)
    return {"status": "success"}


# --------------------------------------------------------------------------- #
# Import from the existing spreadsheet
# --------------------------------------------------------------------------- #
def _normalise(name: str) -> str:
    return " ".join(str(name or "").split()).strip().lower()


@router.post("/obs-register/import")
async def import_register(
    file: UploadFile = File(...),
    month: str | None = Form(default=None),
    replace: bool = Form(default=True),
    user: dict = Depends(requires("reconcile_approve")),
):
    """Load a month of history from the existing OBS stock workbook.

    Expects the layout already in use: row 1 carries the dates, each spanning
    three columns; row 2 carries Received Qty. / Sales / Closing balance_Qty;
    column B is the product and column C the opening balance.

    Products are matched by name. Anything unmatched is reported back rather
    than guessed at, so nothing lands against the wrong line.
    """
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Upload an .xlsx workbook.")

    try:
        workbook = load_workbook(BytesIO(await file.read()), data_only=True)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as-is
        raise HTTPException(status_code=400, detail=f"Could not read the workbook: {exc}")

    sheet = workbook.worksheets[0]
    header = list(sheet.iter_rows(min_row=1, max_row=2, values_only=True))
    if len(header) < 2:
        raise HTTPException(status_code=400, detail="The sheet has no header rows.")

    # Map each date to the column index its three-column block starts at.
    blocks: list[tuple[str, int]] = []
    for index, value in enumerate(header[0]):
        if value is None or index < 3:
            continue
        if isinstance(value, datetime):
            blocks.append((value.date().isoformat(), index))
        elif isinstance(value, date):
            blocks.append((value.isoformat(), index))
        else:
            try:
                blocks.append((datetime.fromisoformat(str(value).strip()).date().isoformat(), index))
            except ValueError:
                continue
    if not blocks:
        raise HTTPException(
            status_code=400,
            detail="No dates found in the first row. Check that this is the stock register sheet.",
        )

    target_month = month or blocks[0][0][:7]

    connection = get_connection()
    try:
        products = {
            _normalise(row["product_name"]): row["id"]
            for row in connection.execute(
                "SELECT id, product_name FROM products"
            ).fetchall()
        }

        imported_rows, unmatched, cells = 0, [], 0
        for row in sheet.iter_rows(min_row=3, values_only=True):
            name = row[1] if len(row) > 1 else None
            if not name or not str(name).strip():
                continue
            product_id = products.get(_normalise(name))
            if product_id is None:
                unmatched.append(str(name).strip())
                continue

            opening = row[2] if len(row) > 2 else 0
            connection.execute(
                """INSERT INTO obs_register_opening(product_id,month,quantity,set_by,set_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(product_id,month) DO UPDATE SET
                       quantity=excluded.quantity, set_by=excluded.set_by, set_at=excluded.set_at""",
                (product_id, target_month, int(opening or 0), user["employee_id"], now_iso()),
            )

            for stock_date, column in blocks:
                if stock_date[:7] != target_month:
                    continue
                received = row[column] if len(row) > column else None
                sales = row[column + 1] if len(row) > column + 1 else None
                if received in (None, "") and sales in (None, ""):
                    continue
                connection.execute(
                    """INSERT INTO obs_register_adjustment
                           (stock_date,product_id,received_qty,sales_qty,remarks,edited_by,edited_at)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(stock_date,product_id) DO UPDATE SET
                           received_qty=excluded.received_qty, sales_qty=excluded.sales_qty,
                           remarks=excluded.remarks, edited_by=excluded.edited_by,
                           edited_at=excluded.edited_at""",
                    (stock_date, product_id, int(received or 0), int(sales or 0),
                     f"Imported from {file.filename}", user["employee_id"], now_iso()),
                )
                cells += 1
            imported_rows += 1

        connection.commit()
    finally:
        connection.close()

    log_action(user, "OBS REGISTER", "IMPORT_REGISTER",
               f"Imported {imported_rows} product row(s) and {cells} day(s) for {target_month} "
               f"from {file.filename}."
               + (f" Unmatched: {', '.join(unmatched)}." if unmatched else ""),
               status="SUCCESS" if not unmatched else "PARTIAL")

    return {
        "status": "success",
        "month": target_month,
        "dates_found": len(blocks),
        "products_imported": imported_rows,
        "days_written": cells,
        "unmatched_products": unmatched,
        "note": "Imported figures are stored as manual entries, so the live totals "
                "they replace are still recorded underneath.",
    }
