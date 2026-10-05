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
from security import current_user, log_action, permissions_for, today_iso

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
