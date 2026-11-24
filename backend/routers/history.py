"""Uploaded sales history (7.16).

Years before OBS went live exist only as the monthly "OBS consumption
report" workbook: one sheet per month, each holding one or more blocks headed
" Sales Report / Mon dd-dd, yyyy" with SL.No, product, cost price, quantity
sold, total cost, sales price and (sometimes) total sold amount. An
administrator uploads that workbook here; the figures then appear on the
Revenue Dashboard whenever a year or month with no live OBS data (or
alongside it) is chosen.

Only the administrator uploads. A block that covers a whole month replaces
the half-month blocks of the same sheet, so nothing is counted twice, and
uploading the same period again replaces the earlier figures.
"""

import re
from datetime import date, datetime
from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from openpyxl import load_workbook

from database import get_connection
from security import current_user, log_action, permissions_for, requires

router = APIRouter()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sales_history_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    uploaded_by TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    periods INTEGER NOT NULL DEFAULT 0,
    rows INTEGER NOT NULL DEFAULT 0,
    revenue REAL NOT NULL DEFAULT 0,
    first_period TEXT, last_period TEXT
);
CREATE TABLE IF NOT EXISTS sales_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER NOT NULL REFERENCES sales_history_uploads(id) ON DELETE CASCADE,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    sheet TEXT,
    product_name TEXT NOT NULL,
    cost_price REAL,
    quantity INTEGER NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0,
    sales_price REAL,
    revenue REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sales_history_period ON sales_history(year, month);
"""

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
TITLE = re.compile(r"sales\s+report\s*\n?\s*([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{1,2})\s*[-–]\s*(\d{1,2})\s*,?\s*(\d{4})", re.I)


def initialize_sales_history(connection):
    connection.executescript(SCHEMA)
    connection.commit()


def _num(value):
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _days_in_month(year, month):
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (nxt - date(year, month, 1)).days


def parse_workbook(workbook) -> list[dict]:
    """Every sales block in the workbook as {period_start, period_end, year,
    month, sheet, lines:[...]}, with half-month blocks dropped where the same
    sheet also carries the whole month."""
    blocks = []
    for ws in workbook.worksheets:
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
        found = []
        for r, row in enumerate(grid):
            for c, cell in enumerate(row):
                if not isinstance(cell, str):
                    continue
                m = TITLE.search(cell)
                if not m:
                    continue
                month = MONTHS.get(m.group(1).lower()[:3])
                if not month:
                    continue
                year = int(m.group(4))
                d1, d2 = int(m.group(2)), min(int(m.group(3)), _days_in_month(year, month))
                # The header row sits right under the title; the products follow.
                header_row = r + 1
                if header_row >= len(grid):
                    continue
                header = grid[header_row]
                col = {}
                for k in range(c, min(c + 8, len(header))):
                    h = str(header[k] or "").lower()
                    if not h:
                        continue
                    if "sl" in h and "no" in h:
                        col["sl"] = k
                    elif "name" in h:
                        col["name"] = k
                    elif "cost price" in h:
                        col["cost_price"] = k
                    elif "qty" in h:
                        col["qty"] = k
                    elif "total cost" in h:
                        col["total_cost"] = k
                    elif "sales price" in h:
                        col["sales_price"] = k
                    elif "sold amount" in h or "total sold" in h:
                        col["revenue"] = k
                if "name" not in col or "qty" not in col:
                    continue
                lines = []
                for rr in range(header_row + 1, len(grid)):
                    row2 = grid[rr]
                    name = row2[col["name"]] if col["name"] < len(row2) else None
                    sl = row2[col.get("sl", c)] if col.get("sl", c) < len(row2) else None
                    if name is None or str(name).strip() == "" or str(name).strip().lower() in ("total", "note", "product name", "items name"):
                        if str(sl or "").strip().lower() in ("total", "note") or name is None:
                            break
                    if not isinstance(sl, (int, float)):
                        break
                    qty = int(_num(row2[col["qty"]])) if col["qty"] < len(row2) else 0
                    cost_price = _num(row2[col["cost_price"]]) if "cost_price" in col and col["cost_price"] < len(row2) else None
                    sales_price = _num(row2[col["sales_price"]]) if "sales_price" in col and col["sales_price"] < len(row2) else None
                    total_cost = _num(row2[col["total_cost"]]) if "total_cost" in col and col["total_cost"] < len(row2) else (cost_price or 0) * qty
                    revenue = _num(row2[col["revenue"]]) if "revenue" in col and col["revenue"] < len(row2) else (sales_price or 0) * qty
                    lines.append({
                        "product_name": str(name).strip(), "cost_price": cost_price, "quantity": qty,
                        "total_cost": round(total_cost, 2), "sales_price": sales_price, "revenue": round(revenue, 2),
                    })
                if lines:
                    found.append({
                        "period_start": date(year, month, d1).isoformat(), "period_end": date(year, month, d2).isoformat(),
                        "year": year, "month": month, "sheet": ws.title, "lines": lines,
                        "whole_month": d1 == 1 and d2 >= _days_in_month(year, month),
                    })
        # A whole-month block is the sum of the half-month blocks on the same
        # sheet: keep the whole month only, for each (year, month) it covers.
        whole = {(b["year"], b["month"]) for b in found if b["whole_month"]}
        blocks.extend(b for b in found if b["whole_month"] or (b["year"], b["month"]) not in whole)
    return blocks


# --------------------------------------------------------------------------- #
# Upload (administrator)
# --------------------------------------------------------------------------- #
@router.post("/sales-history/upload")
async def upload_history(file: UploadFile = File(...), user: dict = Depends(requires("users"))):
    """Load a consumption-report workbook. Periods already on file are replaced."""
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Upload an .xlsx workbook.")
    try:
        workbook = load_workbook(BytesIO(await file.read()), data_only=True)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as-is
        raise HTTPException(status_code=400, detail=f"Could not read the workbook: {exc}")
    blocks = parse_workbook(workbook)
    if not blocks:
        raise HTTPException(
            status_code=400,
            detail="No \"Sales Report <Mon dd-dd, yyyy>\" blocks were found. Use the OBS consumption report layout.")

    connection = get_connection()
    try:
        now = datetime.now().replace(microsecond=0).isoformat()
        periods = sorted({(b["period_start"], b["period_end"]) for b in blocks})
        # Same period uploaded again: the new figures replace the old ones.
        for start, end in periods:
            connection.execute("DELETE FROM sales_history WHERE period_start=? AND period_end=?", (start, end))
        # Half-month blocks on file give way to a whole month now uploaded, and vice versa.
        for b in blocks:
            connection.execute("DELETE FROM sales_history WHERE year=? AND month=? AND NOT (period_start=? AND period_end=?) "
                               "AND (period_start<=? AND period_end>=?)",
                               (b["year"], b["month"], b["period_start"], b["period_end"], b["period_end"], b["period_start"]))
        upload_id = connection.execute(
            "INSERT INTO sales_history_uploads(filename,uploaded_by,uploaded_at,periods,rows,revenue,first_period,last_period) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (file.filename, user["employee_id"], now, len(periods), 0, 0, periods[0][0], periods[-1][1])).lastrowid
        rows = 0
        revenue = 0.0
        for b in blocks:
            for line in b["lines"]:
                connection.execute(
                    "INSERT INTO sales_history(upload_id,period_start,period_end,year,month,sheet,product_name,cost_price,quantity,total_cost,sales_price,revenue) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (upload_id, b["period_start"], b["period_end"], b["year"], b["month"], b["sheet"],
                     line["product_name"], line["cost_price"], line["quantity"], line["total_cost"], line["sales_price"], line["revenue"]))
                rows += 1
                revenue += line["revenue"]
        connection.execute("UPDATE sales_history_uploads SET rows=?, revenue=? WHERE id=?", (rows, round(revenue, 2), upload_id))
        # Uploads whose every row was replaced are gone from the list.
        connection.execute("DELETE FROM sales_history_uploads WHERE id<>? AND NOT EXISTS (SELECT 1 FROM sales_history h WHERE h.upload_id=sales_history_uploads.id)", (upload_id,))
        connection.commit()
    finally:
        connection.close()
    months = sorted({(b["year"], b["month"]) for b in blocks})
    log_action(user, "DASHBOARD", "UPLOAD_HISTORY",
               f"Uploaded sales history {file.filename}: {len(periods)} period(s), {rows} line(s), NPR {revenue:,.0f} "
               f"({months[0][0]}-{months[0][1]:02d} to {months[-1][0]}-{months[-1][1]:02d}).", "sales_history_upload", upload_id)
    return {"upload_id": upload_id, "periods": len(periods), "rows": rows, "revenue": round(revenue, 2),
            "months": [f"{y}-{m:02d}" for y, m in months],
            "sheets": sorted({b["sheet"] for b in blocks})}


@router.get("/sales-history/uploads")
def list_uploads(user: dict = Depends(requires("dashboard"))):
    connection = get_connection()
    try:
        return [dict(r) for r in connection.execute(
            "SELECT * FROM sales_history_uploads ORDER BY id DESC").fetchall()]
    finally:
        connection.close()


@router.delete("/sales-history/uploads/{upload_id}")
def delete_upload(upload_id: int, user: dict = Depends(requires("users"))):
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM sales_history_uploads WHERE id=?", (upload_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Upload not found.")
        connection.execute("DELETE FROM sales_history WHERE upload_id=?", (upload_id,))
        connection.execute("DELETE FROM sales_history_uploads WHERE id=?", (upload_id,))
        connection.commit()
    finally:
        connection.close()
    log_action(user, "DASHBOARD", "DELETE_HISTORY", f"Removed uploaded sales history {row['filename']}.",
               "sales_history_upload", upload_id)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Reading: the dashboard
# --------------------------------------------------------------------------- #
@router.get("/sales-history/summary")
def history_summary(year: int | None = None, month: int | None = None, user: dict = Depends(current_user)):
    if not permissions_for(user["role"]) & {"dashboard", "logs", "reports"}:
        raise HTTPException(status_code=403, detail="You do not have permission to perform this action.")
    connection = get_connection()
    try:
        years = [r["year"] for r in connection.execute("SELECT DISTINCT year FROM sales_history ORDER BY year DESC")]
        if year is None:
            return {"years": years, "available": False}
        cond, params = ["year=?"], [year]
        if month:
            cond.append("month=?")
            params.append(month)
        where = "WHERE " + " AND ".join(cond)
        totals = connection.execute(
            f"SELECT COALESCE(SUM(revenue),0) revenue, COALESCE(SUM(total_cost),0) cost, COALESCE(SUM(quantity),0) units, "
            f"COUNT(DISTINCT period_start||period_end) periods FROM sales_history {where}", params).fetchone()
        months = [dict(r) for r in connection.execute(
            f"SELECT month, ROUND(SUM(revenue),2) revenue, ROUND(SUM(total_cost),2) cost, SUM(quantity) units, "
            f"COUNT(DISTINCT period_start||period_end) periods, MIN(period_start) first_day, MAX(period_end) last_day "
            f"FROM sales_history {where} GROUP BY month ORDER BY month", params)]
        products = [dict(r) for r in connection.execute(
            f"SELECT product_name, SUM(quantity) units, ROUND(SUM(revenue),2) revenue, ROUND(SUM(total_cost),2) cost, "
            f"ROUND(SUM(revenue)-SUM(total_cost),2) margin, MAX(sales_price) sales_price, MAX(cost_price) cost_price "
            f"FROM sales_history {where} GROUP BY product_name ORDER BY revenue DESC", params)]
        periods = [dict(r) for r in connection.execute(
            f"SELECT period_start, period_end, sheet, ROUND(SUM(revenue),2) revenue, SUM(quantity) units, COUNT(*) lines "
            f"FROM sales_history {where} GROUP BY period_start, period_end, sheet ORDER BY period_start", params)]
        revenue = float(totals["revenue"] or 0)
        cost = float(totals["cost"] or 0)
        return {
            "years": years, "available": bool(periods), "year": year, "month": month,
            "revenue": round(revenue, 2), "cost": round(cost, 2), "margin": round(revenue - cost, 2),
            "margin_pct": round((revenue - cost) / revenue * 100, 1) if revenue else 0.0,
            "units": int(totals["units"] or 0), "periods": periods, "months": months, "products": products,
        }
    finally:
        connection.close()
