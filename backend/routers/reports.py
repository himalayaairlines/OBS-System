"""Excel exports.

Accepts the session token either as an `Authorization: Bearer` header or as a
`?token=` query parameter, so a browser download link works as well as a
fetch-and-blob download from the mobile app.
"""

from io import BytesIO

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from database import get_connection
from routers.analytics import COMMISSION_RATE, crew_ranking, dashboard, logs, top_products
from routers.register import obs_register
from routers.sales import list_sales
from routers.warehouse import list_orders
from security import current_user, log_action, permissions_for, requires, today_iso

router = APIRouter(tags=["reports"])

HEADER_FILL = PatternFill("solid", fgColor="0B4F8A")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
MONEY = '#,##0.00'


def _report_user(token: str | None = None, authorization: str | None = None,
                 permission: str = "reports"):
    """Resolve the caller from either transport."""
    header = authorization
    if not header and token:
        header = f"Bearer {token}"
    user = current_user(header)
    if permission not in permissions_for(user["role"]):
        raise HTTPException(status_code=403, detail="You do not have permission to export this.")
    return user


def _workbook_response(workbook, filename, user, description):
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    log_action(user, "REPORT", "EXPORT", description)
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


def _style(sheet, money_columns=()):
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.freeze_panes = "A2"
    for column in sheet.columns:
        letter = get_column_letter(column[0].column)
        width = max((len(str(c.value or "")) for c in column), default=10) + 3
        sheet.column_dimensions[letter].width = min(max(width, 11), 40)
        if letter in money_columns:
            for cell in column[1:]:
                cell.number_format = MONEY
    sheet.auto_filter.ref = sheet.dimensions


@router.get("/reports/sales.xlsx")
def sales_report(year: int | None = None, month: int | None = None, day: int | None = None,
                 destination: str | None = None, token: str | None = None,
                 authorization: str | None = Header(default=None)):
    user = _report_user(token, authorization)
    data = dashboard(year=year, month=month, day=day, destination=destination, user=user)
    ranking = crew_ranking(year=year, month=month, day=day, destination=destination, user=user)

    conditions, params = [], []
    if year:
        conditions.append("substr(f.flight_date,1,4)=?")
        params.append(str(year))
    if month:
        conditions.append("substr(f.flight_date,6,2)=?")
        params.append(f"{int(month):02d}")
    if day:
        conditions.append("substr(f.flight_date,9,2)=?")
        params.append(f"{int(day):02d}")
    if destination:
        conditions.append("f.destination=?")
        params.append(destination.strip().upper())
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    connection = get_connection()
    try:
        sales = connection.execute(
            f"""SELECT s.transaction_id, s.order_ref, f.flight_number, f.flight_date, f.direction,
                       f.origin, f.destination, f.aircraft,
                       d.label AS duty_group_label, cm.crew_code AS seller_code,
                       cm.name AS seller_name, s.crew_id,
                       s.seat_number, p.product_name, p.category, b.bundle_name,
                       s.quantity, s.unit_price, s.transaction_currency,
                       s.exchange_rate_to_npr, s.transaction_currency_amount, s.npr_amount,
                       s.sale_type, s.payment_method, s.transaction_time,
                       s.remarks, s.edited_by, s.edited_at, s.edit_remarks
                FROM sales s
                JOIN flights f ON f.id = s.flight_id
                LEFT JOIN products p ON p.id = s.product_id
                LEFT JOIN bundles b ON b.id = s.bundle_id
                LEFT JOIN duty_groups d ON d.id = s.duty_group_id
                LEFT JOIN crew_members cm ON cm.id = s.sold_by_crew_member_id
                {where}
                ORDER BY f.flight_date DESC, f.flight_number, s.id""",
            params,
        ).fetchall()
        recon = connection.execute(
            f"""SELECT f.flight_number, f.flight_date, p.product_name,
                       ri.loaded_quantity, ri.sold_quantity, ri.expected_remaining,
                       ri.declared_remaining, ri.variance, r.status,
                       r.submitted_by, r.reviewed_by, r.review_remarks
                FROM reconciliation_items ri
                JOIN reconciliations r ON r.id = ri.reconciliation_id
                JOIN flights f ON f.id = r.flight_id
                JOIN products p ON p.id = ri.product_id
                {where}
                ORDER BY f.flight_date DESC, f.flight_number, p.product_name""",
            params,
        ).fetchall()
    finally:
        connection.close()

    workbook = Workbook()

    summary = workbook.active
    summary.title = "Summary"
    summary.append(["Metric", "Value"])
    period = "-".join(str(x) for x in [year, month, day] if x) or "All dates"
    for label, value in [
        ("Report generated", today_iso()),
        ("Period", period),
        ("Destination filter", destination or "All"),
        ("Flights", data["flight_count"]),
        ("Passengers", data["passengers"]),
        ("Revenue (NPR)", data["revenue"]),
        ("Units sold", data["units"]),
        ("Transactions", data["transactions"]),
        ("Average basket (NPR)", data["basket"]),
        ("Revenue per passenger (NPR)", data["rpp"]),
        ("Target RPP (NPR)", data["target_rpp"]),
        ("Gap to target (NPR)", data["gap"]),
        ("Target achievement (%)", data["achievement"]),
        ("Commission pool (NPR)", round(data["revenue"] * COMMISSION_RATE, 2)),
    ]:
        summary.append([label, value])
    _style(summary, money_columns=("B",))

    sheet = workbook.create_sheet("Sales")
    sheet.append([
        "Transaction ID", "Order Ref", "Flight", "Date", "Direction", "From", "To",
        "Aircraft", "Duty Group", "Seller Code", "Seller", "Device", "Seat",
        "Product", "Category", "Bundle",
        "Qty", "Unit Price (NPR)", "Currency", "Rate to NPR", "Amount (Currency)",
        "Amount (NPR)", "Sale Type", "Payment", "Time", "Remarks",
        "Edited By", "Edited At", "Edit Remarks",
    ])
    for row in sales:
        sheet.append([
            row["transaction_id"], row["order_ref"], row["flight_number"], row["flight_date"],
            row["direction"], row["origin"], row["destination"], row["aircraft"],
            row["duty_group_label"], row["seller_code"], row["seller_name"],
            row["crew_id"], row["seat_number"], row["product_name"],
            row["category"], row["bundle_name"], row["quantity"], row["unit_price"],
            row["transaction_currency"], row["exchange_rate_to_npr"],
            row["transaction_currency_amount"], row["npr_amount"], row["sale_type"],
            row["payment_method"], row["transaction_time"], row["remarks"],
            row["edited_by"], row["edited_at"], row["edit_remarks"],
        ])
    _style(sheet, money_columns=("R", "T", "U", "V"))

    flights_sheet = workbook.create_sheet("Flights")
    flights_sheet.append([
        "Flight", "Date", "Direction", "From", "To", "Aircraft", "Duty Group",
        "PAX", "Revenue (NPR)", "Units", "Transactions", "RPP (NPR)",
    ])
    for flight in data["flights"]:
        flights_sheet.append([
            flight["flight_number"], flight["flight_date"], flight["direction"],
            flight["origin"], flight["destination"], flight["aircraft"],
            flight["duty_group_label"],
            flight["passengers"], flight["revenue"], flight["units"],
            flight["transactions"], flight["rpp"],
        ])
    _style(flights_sheet, money_columns=("I", "L"))

    products_sheet = workbook.create_sheet("Products")
    products_sheet.append(["Product", "Category", "Units", "Revenue (NPR)", "Margin (NPR)"])
    for product in data["products"]:
        products_sheet.append([
            product["product_name"], product["category"], product["units"],
            product["revenue"], product["margin"],
        ])
    _style(products_sheet, money_columns=("D", "E"))

    commission_sheet = workbook.create_sheet("Commission")
    commission_sheet.append([
        "Rank", "Crew Code", "Crew Member", "Position", "Sectors", "Transactions",
        "Own Sales (NPR)", "Commission (NPR)",
    ])
    for entry in ranking["ranking"]:
        commission_sheet.append([
            entry["rank"], entry["crew_code"], entry["name"], entry.get("position"),
            entry["flights"], entry["transactions"],
            entry["attributed_sales_npr"], entry["commission_npr"],
        ])
    commission_sheet.append([])
    commission_sheet.append([f"Commission model: {ranking['commission_model']}",
                             f"Rate: {ranking['commission_rate'] * 100:.0f}%"])
    commission_sheet.append([])
    commission_sheet.append(["Sector", "Date", "Destination", "Crew", "Revenue (NPR)",
                             "Commission (NPR)"])
    for sector in ranking["sectors"]:
        commission_sheet.append([
            sector["duty_group_label"], sector["flight_date"], sector["destination"],
            sector["member_count"], sector["revenue_npr"], sector["commission_pool_npr"],
        ])
    _style(commission_sheet, money_columns=("G", "H"))

    recon_sheet = workbook.create_sheet("Reconciliation")
    recon_sheet.append([
        "Flight", "Date", "Product", "Loaded", "Sold", "Expected Left",
        "Declared Left", "Variance", "Status", "Submitted By", "Reviewed By", "Review Remarks",
    ])
    for row in recon:
        recon_sheet.append([
            row["flight_number"], row["flight_date"], row["product_name"],
            row["loaded_quantity"], row["sold_quantity"], row["expected_remaining"],
            row["declared_remaining"], row["variance"], row["status"],
            row["submitted_by"], row["reviewed_by"], row["review_remarks"],
        ])
    _style(recon_sheet)

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)

    log_action(user, "REPORT", "EXPORT_SALES",
               f"Exported the sales workbook for {period} / {destination or 'all destinations'}.")

    filename = f"himalaya-obs-{period.replace(' ', '-').lower()}.xlsx"
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.get("/reports/po-register.xlsx")
def po_register_report(status: str | None = None, supply_type: str | None = None,
                       date_from: str | None = None, date_to: str | None = None,
                       token: str | None = None,
                       authorization: str | None = Header(default=None)):
    """The PO register in the same column order as the source workbook."""
    user = _report_user(token, authorization, permission="warehouse")
    orders = list_orders(status=status, supply_type=supply_type,
                         date_from=date_from, date_to=date_to, user=user)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "PO Register"
    sheet.append([
        "PO code", "PO Number", "FY", "PO No.", "Purchase Items", "QTY (pcs)",
        "ORDER PLACEMENT DATE", "Delivery Date", "Order Received QTY (pcs)",
        "Order Received Date", "Order Received Location", "Inflight/OBS",
        "Status", "Remarks", "Edited By", "Edit Remarks",
    ])
    for order in orders:
        sheet.append([
            order["po_code"], order["po_number"], order["fiscal_year"], order["order_no"],
            order["purchase_items"], order["qty_text"], order["order_placement_date"],
            order["delivery_date"], order["received_qty_text"], order["received_date"],
            order["received_location"], order["supply_type"], order["status"],
            order["remarks"], order["edited_by"], order["edit_remarks"],
        ])
    _style(sheet)

    detail = workbook.create_sheet("Line Items")
    detail.append(["PO No.", "Product", "Ordered (pcs)", "Received (pcs)",
                   "Outstanding (pcs)", "Unit Cost", "Line Cost", "Inflight/OBS"])
    for order in orders:
        for item in order["items"]:
            detail.append([
                order["order_no"], item["product_name"], item["quantity"],
                item["received_quantity"], item["quantity"] - item["received_quantity"],
                item["unit_cost"], round(item["quantity"] * float(item["unit_cost"]), 2),
                order["supply_type"],
            ])
    _style(detail, money_columns=("F", "G"))

    return _workbook_response(workbook, "po-register.xlsx", user, "Exported the PO register.")


@router.get("/reports/obs-register.xlsx")
def obs_register_report(month: str | None = None, token: str | None = None,
                        authorization: str | None = Header(default=None)):
    """The monthly OBS stock register laid out day by day."""
    user = _report_user(token, authorization, permission="inventory")
    data = obs_register(month=month, user=user)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = data["month"]

    # Two header rows: dates spanning three columns each, then the sub-headers.
    top = ["SNO.", "Product", "Opening balance_Qty"]
    sub = ["", "", ""]
    for day in data["dates"]:
        top += [day, "", ""]
        sub += ["Received Qty.", "Sales", "Closing balance_Qty"]
    sheet.append(top)
    sheet.append(sub)

    for index, column in enumerate(range(4, 4 + len(data["dates"]) * 3, 3)):
        sheet.merge_cells(start_row=1, start_column=column, end_row=1, end_column=column + 2)
    for column in range(1, 4):
        sheet.merge_cells(start_row=1, start_column=column, end_row=2, end_column=column)

    for row in data["rows"]:
        line = [row["sno"], row["product_name"], row["opening_qty"]]
        for cell in row["days"]:
            line += [
                cell["received_qty"] or None,
                cell["sales_qty"],
                cell["closing_qty"],
            ]
        sheet.append(line)

    for cell in list(sheet[1]) + list(sheet[2]):
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.freeze_panes = "D3"
    sheet.column_dimensions["B"].width = 26
    sheet.column_dimensions["C"].width = 14

    edits = workbook.create_sheet("Manual Edits")
    edits.append(["Date", "Product", "Received Qty.", "Sales", "Closing balance_Qty",
                  "Computed Received", "Computed Sales", "Edited By", "Remarks"])
    for row in data["rows"]:
        for cell in row["days"]:
            if cell["adjusted"]:
                edits.append([
                    cell["date"], row["product_name"], cell["received_qty"], cell["sales_qty"],
                    cell["closing_qty"], cell["computed_received"], cell["computed_sales"],
                    cell["edited_by"], cell["remarks"],
                ])
    _style(edits)

    return _workbook_response(
        workbook, f"obs-register-{data['month']}.xlsx", user,
        f"Exported the OBS stock register for {data['month']}.")


def _filters(year, month, day, destination):
    parts = [str(x) for x in (day, month, year) if x]
    return "-".join(reversed(parts)) or "All dates", destination or "All destinations"


@router.get("/reports/crew-ranking.xlsx")
def crew_ranking_report(year: int | None = None, month: int | None = None, day: int | None = None,
                        destination: str | None = None, token: str | None = None,
                        authorization: str | None = Header(default=None)):
    user = _report_user(token, authorization, permission="logs")
    data = crew_ranking(year=year, month=month, day=day, destination=destination, user=user)
    period, place = _filters(year, month, day, destination)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Crew Ranking"
    sheet.append(["Rank", "Crew Code", "Crew Member", "Position", "Sectors",
                  "Transactions", "Own Sales (NPR)", "Commission (NPR)"])
    for entry in data["ranking"]:
        sheet.append([entry["rank"], entry["crew_code"], entry["name"], entry.get("position"),
                      entry["flights"], entry["transactions"],
                      entry["attributed_sales_npr"], entry["commission_npr"]])
    _style(sheet, money_columns=("G", "H"))

    groups = workbook.create_sheet("By Sector")
    groups.append(["Sector", "Date", "Destination", "Crew", "Sales (NPR)", "Commission (NPR)"])
    for sector in data["sectors"]:
        groups.append([sector["duty_group_label"], sector["flight_date"], sector["destination"],
                       sector["member_count"], sector["revenue_npr"],
                       sector["commission_pool_npr"]])
    groups.append([])
    groups.append(["Commission model", data["commission_model"]])
    groups.append(["Period", period])
    groups.append(["Destination", place])
    groups.append(["Commission rate", f"{data['commission_rate'] * 100:.0f}%"])
    _style(groups, money_columns=("E", "F"))

    return _workbook_response(workbook, "crew-ranking.xlsx", user,
                              f"Exported the crew ranking for {period} / {place}.")


@router.get("/reports/top-products.xlsx")
def top_products_report(year: int | None = None, month: int | None = None, day: int | None = None,
                        destination: str | None = None, limit: int = 50,
                        token: str | None = None,
                        authorization: str | None = Header(default=None)):
    user = _report_user(token, authorization, permission="logs")
    rows = top_products(year=year, month=month, day=day, destination=destination,
                        limit=limit, user=user)
    period, place = _filters(year, month, day, destination)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Top Products"
    sheet.append(["Rank", "Code", "Product", "Category", "Units",
                  "Revenue (NPR)", "Margin (NPR)", "Flights"])
    for index, row in enumerate(rows, start=1):
        sheet.append([index, row["product_code"], row["product_name"], row["category"],
                      row["units"], row["revenue"], row["margin"], row["flights"]])
    sheet.append([])
    sheet.append(["Period", period])
    sheet.append(["Destination", place])
    _style(sheet, money_columns=("F", "G"))

    return _workbook_response(workbook, "top-products.xlsx", user,
                              f"Exported top products for {period} / {place}.")


@router.get("/reports/audit-log.xlsx")
def audit_log_report(year: int | None = None, month: int | None = None, day: int | None = None,
                     employee_id: str | None = None, module: str | None = None,
                     status: str | None = None, limit: int = 2000,
                     token: str | None = None,
                     authorization: str | None = Header(default=None)):
    user = _report_user(token, authorization, permission="logs")
    rows = logs(year=year, month=month, day=day, employee_id=employee_id,
                module=module, status=status, limit=limit, user=user)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Audit Log"
    sheet.append(["Timestamp", "Employee", "Role", "Module", "Action",
                  "Target Type", "Target ID", "Description", "Status", "Details"])
    for row in rows:
        sheet.append([row["timestamp"], row["employee_id"], row["role"], row["module"],
                      row["action"], row["target_type"], row["target_id"],
                      row["description"], row["status"], row["metadata"]])
    _style(sheet)

    period, _ = _filters(year, month, day, None)
    return _workbook_response(workbook, "audit-log.xlsx", user,
                              f"Exported {len(rows)} audit log entries for {period}.")


@router.get("/reports/sales-detail.xlsx")
def sales_detail_report(flight_id: int | None = None, date: str | None = None,
                        duty_group_id: int | None = None, crew_member_id: int | None = None,
                        limit: int = 2000,
                        token: str | None = None,
                        authorization: str | None = Header(default=None)):
    """Line-level sales with the full correction trail, for the Edit sales tab."""
    user = _report_user(token, authorization, permission="sales_view")
    rows = list_sales(flight_id=flight_id, date=date, duty_group_id=duty_group_id,
                      crew_member_id=crew_member_id, limit=limit, user=user)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sales"
    sheet.append(["ID", "Transaction ID", "Order Ref", "Flight", "Date", "Direction",
                  "From", "To", "Aircraft", "Duty Group", "Seller Code", "Seller",
                  "Device", "Seat",
                  "Product", "Category", "Bundle", "Qty", "Unit Price (NPR)",
                  "Currency", "Rate to NPR", "Amount (Currency)", "Amount (NPR)",
                  "Sale Type", "Payment", "Time", "Remarks",
                  "Edited By", "Edited At", "Edit Remarks"])
    for row in rows:
        sheet.append([
            row["id"], row["transaction_id"], row["order_ref"], row["flight_number"],
            row["flight_date"], row["direction"], row["origin"], row["destination"],
            row["aircraft"], row["duty_group_label"], row["seller_code"],
            row["seller_name"], row["crew_id"], row["seat_number"],
            row["product_name"], row["category"], row["bundle_name"], row["quantity"],
            row["unit_price"], row["transaction_currency"], row["exchange_rate_to_npr"],
            row["transaction_currency_amount"], row["npr_amount"], row["sale_type"],
            row["payment_method"], row["transaction_time"], row["remarks"],
            row["edited_by"], row["edited_at"], row["edit_remarks"],
        ])
    _style(sheet, money_columns=("S", "U", "V", "W"))

    edited = workbook.create_sheet("Corrections")
    edited.append(["ID", "Flight", "Date", "Seat", "Seller", "Product now", "Qty",
                   "Amount (NPR)", "Edited By", "Edited At", "Edit Remarks"])
    for row in rows:
        if row["edited_by"]:
            edited.append([row["id"], row["flight_number"], row["flight_date"],
                           row["seat_number"], row["seller_name"], row["product_name"],
                           row["quantity"],
                           row["npr_amount"], row["edited_by"], row["edited_at"],
                           row["edit_remarks"]])
    _style(edited, money_columns=("G",))

    return _workbook_response(workbook, "sales-detail.xlsx", user,
                              f"Exported {len(rows)} sales lines.")


# --------------------------------------------------------------------------- #
# Onboard Sales Report (7.17): the standard period report, on screen and as a
# formatted workbook anyone can read.
# --------------------------------------------------------------------------- #
def _period_label(year, month, day):
    from datetime import date as _date
    if year and month and day:
        return _date(int(year), int(month), int(day)).strftime("%d %B %Y")
    if year and month:
        return _date(int(year), int(month), 1).strftime("%B %Y")
    if year:
        return f"Year {year}"
    return "All periods"


def build_sales_report(year, month, day, destination, user) -> dict:
    data = dashboard(year=year, month=month, day=day, destination=destination, user=user)
    conditions, params = [], []
    if year:
        conditions.append("substr(f.flight_date,1,4)=?")
        params.append(str(year))
    if month:
        conditions.append("substr(f.flight_date,6,2)=?")
        params.append(f"{int(month):02d}")
    if day:
        conditions.append("substr(f.flight_date,9,2)=?")
        params.append(f"{int(day):02d}")
    if destination:
        conditions.append("f.destination=?")
        params.append(destination.strip().upper())
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    connection = get_connection()
    try:
        by_type = [dict(r) for r in connection.execute(f"""
            SELECT s.sale_type, COUNT(DISTINCT s.order_ref) AS transactions, COALESCE(SUM(s.quantity),0) AS units,
                   ROUND(COALESCE(SUM(s.npr_amount),0),2) AS revenue
            FROM sales s JOIN flights f ON f.id=s.flight_id {where} GROUP BY s.sale_type""", params)]
        by_device = [dict(r) for r in connection.execute(f"""
            SELECT s.crew_id AS device, COUNT(DISTINCT s.flight_id) AS sectors, COUNT(DISTINCT s.order_ref) AS transactions,
                   COALESCE(SUM(CASE WHEN s.sale_type='NORMAL' THEN s.quantity END),0) AS units,
                   ROUND(COALESCE(SUM(CASE WHEN s.sale_type='NORMAL' THEN s.npr_amount END),0),2) AS revenue
            FROM sales s JOIN flights f ON f.id=s.flight_id {where} GROUP BY s.crew_id ORDER BY revenue DESC""", params)]
        by_category = [dict(r) for r in connection.execute(f"""
            SELECT COALESCE(p.category, 'Combo') AS category, COALESCE(SUM(s.quantity),0) AS units,
                   ROUND(COALESCE(SUM(s.npr_amount),0),2) AS revenue,
                   ROUND(COALESCE(SUM(s.npr_amount),0) - COALESCE(SUM(s.quantity * COALESCE(p.cost,0)),0),2) AS margin
            FROM sales s JOIN flights f ON f.id=s.flight_id LEFT JOIN products p ON p.id=s.product_id
            {where + (' AND ' if where else 'WHERE ')} s.sale_type='NORMAL' GROUP BY 1 ORDER BY revenue DESC""", params)]
        sectors = [dict(r) for r in connection.execute(f"""
            SELECT f.id, f.flight_date, f.flight_number, f.origin, f.destination, f.direction, f.aircraft_type, f.aircraft,
                   f.passengers, f.status, f.trip_type,
                   (SELECT GROUP_CONCAT(a.crew_account, ' + ') FROM flight_assignments a WHERE a.flight_id=f.id) AS devices,
                   d.label AS duty_group_label,
                   (SELECT COUNT(*) FROM duty_group_members m WHERE m.duty_group_id=d.id) AS crew_count,
                   COUNT(DISTINCT CASE WHEN s.sale_type='NORMAL' THEN s.order_ref END) AS transactions,
                   COALESCE(SUM(CASE WHEN s.sale_type='NORMAL' THEN s.quantity END),0) AS units,
                   ROUND(COALESCE(SUM(CASE WHEN s.sale_type='NORMAL' THEN s.npr_amount END),0),2) AS revenue,
                   COUNT(DISTINCT CASE WHEN s.sale_type='COMPLEMENTARY' THEN s.order_ref END) AS complementary,
                   COUNT(DISTINCT CASE WHEN s.sale_type='CANCELLATION' THEN s.order_ref END) AS cancellations,
                   (SELECT r.status FROM reconciliations r WHERE r.flight_id=CASE WHEN f.direction='RETURN' THEN f.linked_flight_id ELSE f.id END) AS stock_return
            FROM flights f LEFT JOIN sales s ON s.flight_id=f.id LEFT JOIN duty_groups d ON d.flight_id=f.id
            {where} GROUP BY f.id ORDER BY f.flight_date, f.flight_number, f.direction""", params)]
    finally:
        connection.close()
    for sec in sectors:
        pax = int(sec["passengers"] or 0)
        sec["rpp"] = round(float(sec["revenue"]) / pax, 2) if pax else 0.0
        sec["route"] = f"{sec['origin']}-{sec['destination']}"
        sec["vs_target"] = round(sec["rpp"] - data["target_rpp"], 2)
    return {
        "title": "Himalaya Airlines — Onboard Sales Report",
        "period": _period_label(year, month, day), "destination": destination or "All destinations",
        "generated_at": today_iso(), "generated_by": user["employee_id"], "currency": "NPR",
        "summary": {
            "revenue": data["revenue"], "passengers": data["passengers"], "flights": data["flight_count"],
            "units": data["units"], "transactions": data["transactions"], "basket": data["basket"],
            "rpp": data["rpp"], "target_rpp": data["target_rpp"], "achievement": data["achievement"],
            "gap": data["gap"], "commission_rate": data["commission_rate"], "commission_npr": data["commission_npr"],
            "revenue_at_target": round(data["passengers"] * data["target_rpp"], 2),
            "sectors_at_target": sum(1 for s in sectors if s["passengers"] and s["rpp"] >= data["target_rpp"]),
            "sectors_below_target": sum(1 for s in sectors if s["passengers"] and 0 < s["rpp"] < data["target_rpp"]),
            "sectors_no_sales": sum(1 for s in sectors if not s["revenue"]),
        },
        "by_type": by_type, "sectors": sectors, "routes": data["routes"], "products": data["products"],
        "categories": by_category, "devices": by_device, "currencies": data["currencies"], "daily": data["trend"],
    }


@router.get("/reports/sales-report")
def sales_report_json(year: int | None = None, month: int | None = None, day: int | None = None,
                      destination: str | None = None, user: dict = Depends(requires("sales_report"))):
    return build_sales_report(year, month, day, destination, user)


@router.get("/reports/sales-report.xlsx")
def sales_report_xlsx(year: int | None = None, month: int | None = None, day: int | None = None,
                      destination: str | None = None, token: str | None = None,
                      authorization: str | None = Header(default=None)):
    from openpyxl.styles import Border, Side
    user = _report_user(token, authorization, permission="sales_report")
    rep = build_sales_report(year, month, day, destination, user)
    s = rep["summary"]
    wb = Workbook()
    title_font = Font(bold=True, size=16, color="0B4F8A")
    sub_font = Font(size=11, color="555555")
    section_font = Font(bold=True, size=12, color="0B4F8A")
    bold = Font(bold=True)
    thin = Side(style="thin", color="D0D7E2")
    box = Border(top=thin, bottom=thin, left=thin, right=thin)
    total_fill = PatternFill("solid", fgColor="EAF1FA")
    INT = '#,##0'

    def header(ws, row, labels):
        for i, label in enumerate(labels, start=1):
            c = ws.cell(row=row, column=i, value=label)
            c.fill, c.font, c.border = HEADER_FILL, HEADER_FONT, box
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[row].height = 30

    def write_rows(ws, start, rows, money_cols=(), int_cols=(), total=None):
        r = start
        for values in rows:
            for i, v in enumerate(values, start=1):
                c = ws.cell(row=r, column=i, value=v)
                c.border = box
                if i in money_cols:
                    c.number_format = MONEY
                elif i in int_cols:
                    c.number_format = INT
            r += 1
        if total:
            for i, v in enumerate(total, start=1):
                c = ws.cell(row=r, column=i, value=v)
                c.font, c.fill, c.border = bold, total_fill, box
                if i in money_cols:
                    c.number_format = MONEY
                elif i in int_cols:
                    c.number_format = INT
            r += 1
        return r

    def widths(ws, spec):
        for letter, w in spec.items():
            ws.column_dimensions[letter].width = w

    def banner(ws, subtitle):
        ws["A1"] = rep["title"]
        ws["A1"].font = title_font
        ws["A2"] = f"{subtitle} · Period: {rep['period']} · {rep['destination']} · Currency: NPR"
        ws["A2"].font = sub_font
        ws["A3"] = f"Generated {rep['generated_at']} by {rep['generated_by']} from the OBS system"
        ws["A3"].font = sub_font

    # ---- 1. Summary
    ws = wb.active
    ws.title = "Summary"
    banner(ws, "Executive summary")
    r = 5
    ws.cell(row=r, column=1, value="Key figures").font = section_font
    r += 1
    rows = [
        ("Total onboard revenue (normal sales)", s["revenue"], MONEY),
        ("Passengers carried", s["passengers"], INT),
        ("Sectors flown (legs)", s["flights"], INT),
        ("Transactions", s["transactions"], INT),
        ("Units sold", s["units"], INT),
        ("Average basket (revenue per transaction)", s["basket"], MONEY),
        ("Revenue per passenger (RPP)", s["rpp"], MONEY),
        ("Target RPP", s["target_rpp"], MONEY),
        ("Achievement against target (%)", s["achievement"], '0.0'),
        ("Difference per passenger", s["gap"], MONEY),
        ("Revenue at target", s["revenue_at_target"], MONEY),
        ("Shortfall / surplus against target", round(s["revenue"] - s["revenue_at_target"], 2), MONEY),
        (f"Crew commission pool ({int(s['commission_rate'] * 100)}% of normal sales)", s["commission_npr"], MONEY),
        ("Sectors at or above target RPP", s["sectors_at_target"], INT),
        ("Sectors below target RPP", s["sectors_below_target"], INT),
        ("Sectors with no sales", s["sectors_no_sales"], INT),
    ]
    for label, value, fmt in rows:
        a = ws.cell(row=r, column=1, value=label)
        b = ws.cell(row=r, column=2, value=value)
        a.border = b.border = box
        b.number_format = fmt
        b.alignment = Alignment(horizontal="right")
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="Transactions by type").font = section_font
    r += 1
    header(ws, r, ["Type", "Transactions", "Units", "Revenue (NPR)"])
    r = write_rows(ws, r + 1, [(t["sale_type"].title(), t["transactions"], t["units"], t["revenue"]) for t in rep["by_type"]],
                   money_cols=(4,), int_cols=(2, 3))
    r += 1
    ws.cell(row=r, column=1, value="Revenue by category").font = section_font
    r += 1
    header(ws, r, ["Category", "Units", "Revenue (NPR)", "Gross margin (NPR)"])
    cats = rep["categories"]
    r = write_rows(ws, r + 1, [(c["category"], c["units"], c["revenue"], c["margin"]) for c in cats],
                   money_cols=(3, 4), int_cols=(2,),
                   total=("Total", sum(c["units"] for c in cats), round(sum(c["revenue"] for c in cats), 2), round(sum(c["margin"] for c in cats), 2)))
    widths(ws, {"A": 46, "B": 18, "C": 18, "D": 20})

    # ---- 2. By sector
    ws = wb.create_sheet("By sector")
    banner(ws, "Sector-wise sales")
    header(ws, 5, ["Date", "Flight", "Route", "Direction", "Aircraft", "Crew (duty group)", "Crew", "iPads", "PAX",
                   "Transactions", "Units", "Revenue (NPR)", "RPP (NPR)", "Target RPP", "vs target", "Complementary", "Cancelled", "Stock return"])
    rows = [(x["flight_date"], x["flight_number"], x["route"], x["direction"].title(),
             f"{x['aircraft_type'] or ''} {x['aircraft'] or ''}".strip(), x["duty_group_label"] or "", x["crew_count"] or 0,
             x["devices"] or "", x["passengers"] or 0, x["transactions"], x["units"], x["revenue"], x["rpp"], s["target_rpp"],
             x["vs_target"], x["complementary"], x["cancellations"], x["stock_return"] or "not submitted") for x in rep["sectors"]]
    total = ("Total", "", "", "", "", "", "", "", s["passengers"], s["transactions"], s["units"], s["revenue"], s["rpp"], s["target_rpp"], s["gap"],
             sum(x["complementary"] for x in rep["sectors"]), sum(x["cancellations"] for x in rep["sectors"]), "")
    write_rows(ws, 6, rows, money_cols=(12, 13, 14, 15), int_cols=(7, 9, 10, 11, 16, 17), total=total)
    ws.freeze_panes = "A6"
    ws.auto_filter.ref = f"A5:R{5 + len(rows)}"
    widths(ws, {"A": 12, "B": 10, "C": 11, "D": 11, "E": 12, "F": 22, "G": 7, "H": 20, "I": 7, "J": 12, "K": 8, "L": 15, "M": 12, "N": 12, "O": 11, "P": 13, "Q": 10, "R": 14})

    # ---- 3. By route
    ws = wb.create_sheet("By route")
    banner(ws, "Route performance")
    header(ws, 5, ["Route", "Sectors", "PAX", "Revenue (NPR)", "RPP (NPR)", "Share of revenue (%)"])
    tot = s["revenue"] or 1
    rows = [(x["route"], x["flights"], x["passengers"], x["revenue"], x["rpp"], round(x["revenue"] / tot * 100, 1)) for x in rep["routes"]]
    write_rows(ws, 6, rows, money_cols=(4, 5), int_cols=(2, 3), total=("Total", s["flights"], s["passengers"], s["revenue"], s["rpp"], 100.0))
    widths(ws, {"A": 14, "B": 10, "C": 10, "D": 16, "E": 12, "F": 18})

    # ---- 4. By product
    ws = wb.create_sheet("By product")
    banner(ws, "Product performance")
    header(ws, 5, ["#", "Product", "Category", "Units", "Revenue (NPR)", "Gross margin (NPR)", "Margin (%)", "Share of revenue (%)", "Sectors sold on"])
    prods = [p for p in rep["products"] if p["revenue"] > 0 or p["units"] > 0]
    rows = [(i + 1, p["product_name"], p["category"], p["units"], p["revenue"], p["margin"],
             round(p["margin"] / p["revenue"] * 100, 1) if p["revenue"] else 0.0, round(p["revenue"] / tot * 100, 1), p.get("flights", "")) for i, p in enumerate(prods)]
    write_rows(ws, 6, rows, money_cols=(5, 6), int_cols=(4,),
               total=("", "Total", "", sum(p["units"] for p in prods), round(sum(p["revenue"] for p in prods), 2), round(sum(p["margin"] for p in prods), 2), "", 100.0, ""))
    ws.freeze_panes = "A6"
    widths(ws, {"A": 5, "B": 40, "C": 16, "D": 9, "E": 16, "F": 18, "G": 11, "H": 18, "I": 14})

    # ---- 5. By iPad login
    ws = wb.create_sheet("By iPad")
    banner(ws, "Sales recorded per OBS iPad login")
    header(ws, 5, ["iPad login", "Sectors worked", "Transactions", "Units", "Revenue (NPR)", "Share (%)"])
    rows = [(d["device"], d["sectors"], d["transactions"], d["units"], d["revenue"], round(d["revenue"] / tot * 100, 1)) for d in rep["devices"]]
    write_rows(ws, 6, rows, money_cols=(5,), int_cols=(2, 3, 4))
    widths(ws, {"A": 16, "B": 14, "C": 13, "D": 10, "E": 16, "F": 10})

    # ---- 6. Currency & daily
    ws = wb.create_sheet("Currency & daily")
    banner(ws, "Payment currency mix and daily revenue")
    header(ws, 5, ["Currency", "Sale lines", "Revenue in NPR"])
    r = write_rows(ws, 6, [(c["currency"], c["lines"], c["revenue"]) for c in rep["currencies"]], money_cols=(3,), int_cols=(2,))
    r += 1
    header(ws, r, ["Day", "Revenue (NPR)"])
    write_rows(ws, r + 1, [(d["day"], d["revenue"]) for d in rep["daily"]], money_cols=(2,),
               total=("Total", round(sum(d["revenue"] for d in rep["daily"]), 2)))
    widths(ws, {"A": 14, "B": 14, "C": 16})

    for sheet in wb.worksheets:
        sheet.sheet_view.showGridLines = False
    stamp = rep["period"].replace(" ", "-")
    return _workbook_response(wb, f"H9-Onboard-Sales-Report-{stamp}.xlsx", user,
                              f"Exported the onboard sales report for {rep['period']} ({rep['destination']}).")
