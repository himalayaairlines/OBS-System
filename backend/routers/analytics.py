"""Dashboard, top-product analysis, commission ranking and audit logs.

Commission is driven by `sales.sold_by_crew_member_id`: the crew member the
device recorded against each sale. Two models are supported and the choice
lives in database.COMMISSION_MODEL:

  INDIVIDUAL -> a crew member earns COMMISSION_RATE of what they sold
  POOLED     -> the sector's commission is split across its duty group

Because the seller is stored on the sale itself, neither model can be
retrospectively altered by editing a group afterwards.
"""

from collections import defaultdict

from fastapi import APIRouter, Depends
from fastapi import HTTPException

from database import COMMISSION_MODEL, COMMISSION_RATE, get_connection
from paging import PageParam, PageSizeParam, paginate_list, paginate_sql
from security import (
    current_user, date_filters, permissions_for, requires, today_iso, validate_date,
)

router = APIRouter(tags=["analytics"])

TARGET_RPP = 270.0  # NPR of onboard revenue per passenger


def _flight_conditions(year, month, day, destination=None, direction=None):
    conditions, params = date_filters(year, month, day, "f.flight_date")
    if destination:
        conditions.append("f.destination=?")
        params.append(destination.strip().upper())
    if direction:
        conditions.append("f.direction=?")
        params.append(direction.strip().upper())
    return conditions, params


# --------------------------------------------------------------------------- #
# Management dashboard
# --------------------------------------------------------------------------- #
@router.get("/dashboard")
def dashboard(year: int | None = None, month: int | None = None, day: int | None = None,
              destination: str | None = None, user: dict = Depends(requires("dashboard"))):
    conditions, params = _flight_conditions(year, month, day, destination)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    connection = get_connection()
    try:
        flight_rows = connection.execute(
            f"""SELECT f.id, f.flight_number, f.flight_date, f.origin, f.destination,
                       f.direction, f.aircraft, f.passengers, d.label AS duty_group_label,
                       COALESCE(SUM(CASE WHEN s.sale_type='NORMAL' THEN s.npr_amount END),0) AS revenue,
                       COALESCE(SUM(CASE WHEN s.sale_type='NORMAL' THEN s.quantity END),0) AS units,
                       COUNT(DISTINCT CASE WHEN s.sale_type='NORMAL' THEN s.order_ref END) AS transactions
                FROM flights f
                LEFT JOIN sales s ON s.flight_id = f.id
                LEFT JOIN duty_groups d ON d.flight_id = f.id
                {where}
                GROUP BY f.id
                ORDER BY f.flight_date DESC, f.flight_number""",
            params,
        ).fetchall()

        flights, revenue, passengers, units, transactions = [], 0.0, 0, 0, 0
        for row in flight_rows:
            amount = float(row["revenue"] or 0)
            pax = int(row["passengers"] or 0)
            revenue += amount
            passengers += pax
            units += int(row["units"] or 0)
            transactions += int(row["transactions"] or 0)
            flights.append({
                **{k: row[k] for k in (
                    "id", "flight_number", "flight_date", "origin", "destination",
                    "direction", "aircraft", "duty_group_label")},
                "passengers": pax,
                "revenue": round(amount, 2),
                "units": int(row["units"] or 0),
                "transactions": int(row["transactions"] or 0),
                "rpp": round(amount / pax, 2) if pax else 0.0,
            })

        rpp = revenue / passengers if passengers else 0.0

        # Product mix. The sales-side filter is applied inside the join so that
        # products with no sales in the window still appear with a zero.
        flight_scope = f"SELECT f.id FROM flights f {where}"
        product_rows = connection.execute(
            f"""SELECT p.id, p.product_name, p.category,
                       COALESCE(SUM(CASE WHEN s.sale_type='NORMAL' THEN s.quantity END),0) AS units,
                       COALESCE(SUM(CASE WHEN s.sale_type='NORMAL' THEN s.npr_amount END),0) AS revenue,
                       COALESCE(SUM(CASE WHEN s.sale_type='NORMAL'
                                         THEN s.quantity * p.cost END),0) AS cost
                FROM products p
                LEFT JOIN sales s
                       ON s.product_id = p.id
                      AND s.flight_id IN ({flight_scope})
                WHERE p.active = 1
                GROUP BY p.id
                ORDER BY revenue DESC""",
            params,
        ).fetchall()
        products = [{
            "product_id": r["id"], "product_name": r["product_name"], "category": r["category"],
            "units": int(r["units"] or 0), "revenue": round(float(r["revenue"] or 0), 2),
            "margin": round(float(r["revenue"] or 0) - float(r["cost"] or 0), 2),
        } for r in product_rows]

        # Revenue by route, useful for the destination filter.
        # Aggregate per flight first, otherwise the join would multiply PAX by
        # the number of sale lines on that flight.
        route_rows = connection.execute(
            f"""SELECT route, SUM(revenue) AS revenue, SUM(passengers) AS passengers,
                       COUNT(*) AS flights
                FROM (
                    SELECT f.origin || '-' || f.destination AS route,
                           f.passengers AS passengers,
                           COALESCE(SUM(CASE WHEN s.sale_type='NORMAL'
                                             THEN s.npr_amount END),0) AS revenue
                    FROM flights f LEFT JOIN sales s ON s.flight_id = f.id
                    {where}
                    GROUP BY f.id
                )
                GROUP BY route ORDER BY revenue DESC""",
            params,
        ).fetchall()
        routes = [{
            "route": r["route"],
            "revenue": round(float(r["revenue"] or 0), 2),
            "passengers": int(r["passengers"] or 0),
            "flights": int(r["flights"] or 0),
            "rpp": round(float(r["revenue"] or 0) / int(r["passengers"]), 2) if r["passengers"] else 0.0,
        } for r in route_rows]

        # Daily trend for the line chart.
        trend_rows = connection.execute(
            f"""SELECT f.flight_date AS day,
                       COALESCE(SUM(CASE WHEN s.sale_type='NORMAL' THEN s.npr_amount END),0) AS revenue
                FROM flights f LEFT JOIN sales s ON s.flight_id = f.id
                {where}
                GROUP BY f.flight_date ORDER BY f.flight_date""",
            params,
        ).fetchall()
        trend = [{"day": r["day"], "revenue": round(float(r["revenue"] or 0), 2)} for r in trend_rows]

        currency_rows = connection.execute(
            f"""SELECT s.transaction_currency AS currency,
                       COALESCE(SUM(s.npr_amount),0) AS revenue, COUNT(*) AS lines
                FROM sales s JOIN flights f ON f.id = s.flight_id
                {where} {'AND' if conditions else 'WHERE'} s.sale_type='NORMAL'
                GROUP BY s.transaction_currency ORDER BY revenue DESC""",
            params,
        ).fetchall()
        currencies = [{"currency": r["currency"], "revenue": round(float(r["revenue"] or 0), 2),
                       "lines": int(r["lines"])} for r in currency_rows]

        destinations = [r["destination"] for r in connection.execute(
            "SELECT DISTINCT destination FROM flights ORDER BY destination"
        ).fetchall()]
    finally:
        connection.close()

    return {
        "currency": "NPR",
        "target_rpp": TARGET_RPP,
        "rpp": round(rpp, 2),
        "gap": round(rpp - TARGET_RPP, 2),
        "achievement": round(rpp / TARGET_RPP * 100, 2) if TARGET_RPP else 0.0,
        "revenue": round(revenue, 2),
        "passengers": passengers,
        "units": units,
        "transactions": transactions,
        "basket": round(revenue / transactions, 2) if transactions else 0.0,
        # Commission is reported from the same rate the payroll figure uses, so
        # the dashboard cannot drift from the crew ranking.
        "commission_rate": COMMISSION_RATE,
        "commission_model": COMMISSION_MODEL,
        "commission_npr": round(revenue * COMMISSION_RATE, 2),
        "flight_count": len(flights),
        "flights": flights,
        "products": products,
        "routes": routes,
        "trend": trend,
        "currencies": currencies,
        "destinations": destinations,
    }


@router.get("/top-products")
def top_products(year: int | None = None, month: int | None = None, day: int | None = None,
                 destination: str | None = None, limit: int = 10,
                 user: dict = Depends(requires("dashboard"))):
    conditions, params = _flight_conditions(year, month, day, destination)
    conditions.append("s.sale_type='NORMAL'")
    where = "WHERE " + " AND ".join(conditions)

    connection = get_connection()
    try:
        rows = connection.execute(
            f"""SELECT p.id, p.product_code, p.product_name, p.category,
                       SUM(s.quantity) AS units,
                       SUM(s.npr_amount) AS revenue,
                       SUM(s.quantity * p.cost) AS cost,
                       COUNT(DISTINCT s.flight_id) AS flights
                FROM sales s
                JOIN products p ON p.id = s.product_id
                JOIN flights f ON f.id = s.flight_id
                {where}
                GROUP BY p.id
                ORDER BY revenue DESC
                LIMIT ?""",
            [*params, min(limit, 50)],
        ).fetchall()
        return [{
            "product_id": r["id"], "product_code": r["product_code"],
            "product_name": r["product_name"], "category": r["category"],
            "units": int(r["units"] or 0), "revenue": round(float(r["revenue"] or 0), 2),
            "margin": round(float(r["revenue"] or 0) - float(r["cost"] or 0), 2),
            "flights": int(r["flights"] or 0),
        } for r in rows]
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# Commission
# --------------------------------------------------------------------------- #
def _seller_rows(connection, conditions, params):
    """NORMAL revenue per crew member per sector, from the recorded seller.

    This is the whole basis of commission now. A sale carries the crew member
    who made it, so nothing has to be inferred from group membership and
    editing a group later cannot rewrite what somebody earned.
    """
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return connection.execute(
        f"""SELECT f.id AS flight_id, f.flight_number, f.flight_date, f.destination,
                   d.id AS duty_group_id, d.label AS duty_group_label,
                   m.id AS crew_member_id, m.crew_code, m.name, m.position,
                   COALESCE(SUM(s.npr_amount),0) AS revenue,
                   COUNT(DISTINCT s.order_ref) AS transactions
            FROM sales s
            JOIN flights f ON f.id = s.flight_id
            JOIN crew_members m ON m.id = s.sold_by_crew_member_id
            LEFT JOIN duty_groups d ON d.id = s.duty_group_id
            {where}{' AND ' if where else 'WHERE '}s.sale_type='NORMAL'
            GROUP BY f.id, m.id
            HAVING revenue > 0""",
        params,
    ).fetchall()


def _sector_members(connection, flight_ids):
    """Everyone rostered on each sector, including those who sold nothing.

    The pooled model pays them, and even under the individual model a crew
    member who sold nothing should still appear on the sector rather than
    vanish from the record.
    """
    if not flight_ids:
        return {}
    placeholders = ",".join("?" * len(flight_ids))
    rows = connection.execute(
        f"""SELECT d.flight_id, m.id, m.crew_code, m.name, m.position
            FROM duty_groups d
            JOIN duty_group_members dm ON dm.duty_group_id = d.id
            JOIN crew_members m ON m.id = dm.crew_member_id
            WHERE d.flight_id IN ({placeholders})""",
        tuple(flight_ids),
    ).fetchall()
    by_flight = defaultdict(list)
    for row in rows:
        by_flight[row["flight_id"]].append(dict(row))
    return by_flight


def _commission_entries(connection, conditions, params):
    """Per crew member totals under whichever commission model is configured.

    Returns (entries_by_member_id, sectors).
    """
    seller_rows = [dict(r) for r in _seller_rows(connection, conditions, params)]

    sectors = {}
    for row in seller_rows:
        sector = sectors.setdefault(row["flight_id"], {
            "flight_id": row["flight_id"],
            "flight_number": row["flight_number"],
            "flight_date": row["flight_date"],
            "destination": row["destination"],
            "duty_group_id": row["duty_group_id"],
            "duty_group_label": row["duty_group_label"] or row["flight_number"],
            "revenue_npr": 0.0,
            "sellers": [],
        })
        sector["revenue_npr"] += float(row["revenue"] or 0)
        sector["sellers"].append(row)

    members_by_flight = _sector_members(connection, list(sectors))

    entries = {}

    def entry_for(member):
        return entries.setdefault(member["id"], {
            "crew_member_id": member["id"],
            "crew_code": member["crew_code"],
            "name": member["name"],
            "position": member["position"],
            "sales_npr": 0.0,
            "commission_npr": 0.0,
            "transactions": 0,
            "sectors": set(),
        })

    for sector in sectors.values():
        roster = members_by_flight.get(sector["flight_id"], [])
        sector["member_count"] = len(roster)

        if COMMISSION_MODEL == "POOLED" and roster:
            # Everyone on the sector shares the pool regardless of who rang it up.
            pool = sector["revenue_npr"] * COMMISSION_RATE
            share = pool / len(roster)
            sold_by = {r["crew_member_id"]: r for r in sector["sellers"]}
            for member in roster:
                entry = entry_for(member)
                entry["commission_npr"] += share
                own = sold_by.get(member["id"])
                if own:
                    entry["sales_npr"] += float(own["revenue"] or 0)
                    entry["transactions"] += int(own["transactions"] or 0)
                entry["sectors"].add(sector["duty_group_label"])
        else:
            # INDIVIDUAL: a crew member earns on what they personally sold.
            for row in sector["sellers"]:
                entry = entry_for({
                    "id": row["crew_member_id"], "crew_code": row["crew_code"],
                    "name": row["name"], "position": row["position"],
                })
                revenue = float(row["revenue"] or 0)
                entry["sales_npr"] += revenue
                entry["commission_npr"] += revenue * COMMISSION_RATE
                entry["transactions"] += int(row["transactions"] or 0)
                entry["sectors"].add(sector["duty_group_label"])

        sector.pop("sellers_raw", None)

    return entries, sectors


def _render_sector(sector):
    return {
        "flight_id": sector["flight_id"],
        "flight_number": sector["flight_number"],
        "flight_date": sector["flight_date"],
        "destination": sector["destination"],
        "duty_group_id": sector["duty_group_id"],
        "duty_group_label": sector["duty_group_label"],
        "member_count": sector.get("member_count", 0),
        "revenue_npr": round(sector["revenue_npr"], 2),
        "commission_pool_npr": round(sector["revenue_npr"] * COMMISSION_RATE, 2),
        "sellers": sorted(
            [{
                "crew_member_id": r["crew_member_id"],
                "crew_code": r["crew_code"],
                "name": r["name"],
                "sales_npr": round(float(r["revenue"] or 0), 2),
                "transactions": int(r["transactions"] or 0),
            } for r in sector["sellers"]],
            key=lambda r: r["sales_npr"], reverse=True,
        ),
    }


@router.get("/crew-ranking")
def crew_ranking(year: int | None = None, month: int | None = None, day: int | None = None,
                 destination: str | None = None, all_crew: bool = False,
                 user: dict = Depends(requires("logs"))):
    """Individual performance, built from the seller recorded on each sale."""
    conditions, params = _flight_conditions(year, month, day, destination)
    connection = get_connection()
    try:
        entries, sectors = _commission_entries(connection, conditions, params)
    finally:
        connection.close()

    ranking = sorted(entries.values(), key=lambda e: (-e["commission_npr"], e["name"]))
    # Everyone on the active roster appears, including those with nothing this
    # period, so the table is the whole crew rather than only the earners.
    if all_crew:
        connection = get_connection()
        try:
            for m in connection.execute(
                    "SELECT id, crew_code, name, position FROM crew_members WHERE active=1 ORDER BY name").fetchall():
                if m["id"] not in entries:
                    ranking.append({"crew_member_id": m["id"], "crew_code": m["crew_code"], "name": m["name"],
                                    "position": m["position"], "sales_npr": 0.0, "commission_npr": 0.0,
                                    "transactions": 0, "sectors": set()})
        finally:
            connection.close()
    result = [{
        "rank": position,
        "crew_member_id": entry["crew_member_id"],
        "crew_code": entry["crew_code"],
        "name": entry["name"],
        "position": entry["position"],
        "sectors": sorted(entry["sectors"]),
        "flights": len(entry["sectors"]),
        "transactions": entry["transactions"],
        "attributed_sales_npr": round(entry["sales_npr"], 2),
        "commission_npr": round(entry["commission_npr"], 2),
    } for position, entry in enumerate(ranking, start=1)]

    return {
        "commission_rate": COMMISSION_RATE,
        "commission_model": COMMISSION_MODEL,
        "ranking": result,
        "sectors": sorted(
            (_render_sector(s) for s in sectors.values()),
            key=lambda s: s["revenue_npr"], reverse=True,
        ),
    }


@router.get("/crew-members/{member_id}/history")
def crew_history(member_id: int, year: int | None = None, month: int | None = None, day: int | None = None,
                 destination: str | None = None, user: dict = Depends(requires("logs"))):
    """Everything one crew member sold, sector by sector, with what they earned.

    Without a year the whole history is returned.
    """
    connection = get_connection()
    try:
        member = connection.execute("SELECT * FROM crew_members WHERE id=?", (member_id,)).fetchone()
        if not member:
            raise HTTPException(status_code=404, detail="Crew member not found.")
        conditions, params = _flight_conditions(year, month, day, destination)
        entries, sectors = _commission_entries(connection, conditions, params)
        mine = entries.get(member_id)
        where = " AND ".join(conditions + ["s.sold_by_crew_member_id=?"])
        lines = [dict(r) for r in connection.execute(
            f"""SELECT s.id, s.order_ref, s.transaction_time, s.seat_number, s.quantity, s.unit_price,
                       s.npr_amount, s.transaction_currency, s.transaction_currency_amount, s.sale_type,
                       s.remarks, s.edited_by, s.edit_remarks,
                       f.id AS flight_id, f.flight_number, f.flight_date, f.origin, f.destination,
                       COALESCE(p.product_name, b.bundle_name) AS item_name
                FROM sales s
                JOIN flights f ON f.id = s.flight_id
                LEFT JOIN products p ON p.id = s.product_id
                LEFT JOIN bundles b ON b.id = s.bundle_id
                WHERE {where}
                ORDER BY s.transaction_time DESC, s.id DESC""",
            (*params, member_id)).fetchall()]
        # Sectors they worked, including ones where they sold nothing.
        worked = [dict(r) for r in connection.execute(
            f"""SELECT f.id AS flight_id, f.flight_number, f.flight_date, f.origin, f.destination,
                       d.label AS duty_group_label
                FROM duty_group_members dm
                JOIN duty_groups d ON d.id = dm.duty_group_id
                JOIN flights f ON f.id = d.flight_id
                WHERE dm.crew_member_id=? {('AND ' + ' AND '.join(conditions)) if conditions else ''}
                ORDER BY f.flight_date DESC, f.flight_number""",
            (member_id, *params)).fetchall()]
    finally:
        connection.close()

    roster_size = {fid: s.get("member_count", 0) for fid, s in sectors.items()}
    by_flight = defaultdict(lambda: {"sales_npr": 0.0, "items": 0, "transactions": set()})
    for line in lines:
        if line["sale_type"] != "NORMAL":
            continue
        agg = by_flight[line["flight_id"]]
        agg["sales_npr"] += float(line["npr_amount"] or 0)
        agg["items"] += int(line["quantity"] or 0)
        agg["transactions"].add(line["order_ref"])
    sector_rows = []
    for w in worked:
        agg = by_flight.get(w["flight_id"], {"sales_npr": 0.0, "items": 0, "transactions": set()})
        pool = sectors.get(w["flight_id"], {}).get("revenue_npr", 0.0) * COMMISSION_RATE
        size = roster_size.get(w["flight_id"]) or 0
        commission = (pool / size if size else 0.0) if COMMISSION_MODEL == "POOLED" \
            else agg["sales_npr"] * COMMISSION_RATE
        sector_rows.append({**w, "sales_npr": round(agg["sales_npr"], 2), "items": agg["items"],
                            "transactions": len(agg["transactions"]), "commission_npr": round(commission, 2)})
    return {
        "crew_member": {"id": member["id"], "crew_code": member["crew_code"], "name": member["name"],
                        "position": member["position"], "active": bool(member["active"])},
        "totals": {
            "sales_npr": round(mine["sales_npr"], 2) if mine else 0.0,
            "commission_npr": round(mine["commission_npr"], 2) if mine else 0.0,
            "transactions": mine["transactions"] if mine else 0,
            "sectors": len(worked),
            "items": sum(int(l["quantity"] or 0) for l in lines if l["sale_type"] == "NORMAL"),
        },
        "commission_model": COMMISSION_MODEL, "commission_rate": COMMISSION_RATE,
        "sectors": sector_rows,
        "sales": lines,
    }


def group_summary(user: dict, date: str | None = None):
    """Sync-page summary: today's sector, who sold what, and what it earns."""
    target = validate_date(date, "date") if date else today_iso()
    device_scope = "logs" not in permissions_for(user["role"])

    connection = get_connection()
    try:
        conditions = ["f.flight_date=?"]
        params = [target]
        if device_scope:
            conditions.append(
                """f.id IN (SELECT flight_id FROM flight_assignments
                            WHERE crew_account=?)"""
            )
            params.append(user["employee_id"])

        entries, sectors = _commission_entries(connection, conditions, params)

        where = " AND ".join(conditions)
        transactions = connection.execute(
            f"""SELECT COUNT(DISTINCT s.order_ref) c FROM sales s
                JOIN flights f ON f.id = s.flight_id
                WHERE {where} AND s.sale_type='NORMAL'""",
            params,
        ).fetchone()["c"]
    finally:
        connection.close()

    rendered = sorted(
        (_render_sector(s) for s in sectors.values()),
        key=lambda s: s["revenue_npr"], reverse=True,
    )
    crew = sorted(
        ({
            "crew_member_id": e["crew_member_id"],
            "crew_code": e["crew_code"],
            "name": e["name"],
            "sales_npr": round(e["sales_npr"], 2),
            "commission_npr": round(e["commission_npr"], 2),
            "transactions": e["transactions"],
        } for e in entries.values()),
        key=lambda e: e["commission_npr"], reverse=True,
    )

    return {
        "date": target,
        "scope": "device" if device_scope else "all",
        "commission_rate": COMMISSION_RATE,
        "commission_model": COMMISSION_MODEL,
        "total_sales_npr": round(sum(s["revenue_npr"] for s in rendered), 2),
        "total_commission_npr": round(sum(e["commission_npr"] for e in crew), 2),
        "transactions": transactions,
        "sectors": rendered,
        "crew": crew,
    }


# --------------------------------------------------------------------------- #
# Audit logs
# --------------------------------------------------------------------------- #
@router.get("/logs")
def logs(year: int | None = None, month: int | None = None, day: int | None = None,
         employee_id: str | None = None, module: str | None = None,
         status: str | None = None, limit: int = 500,
         page: int | None = PageParam, page_size: int = PageSizeParam,
         user: dict = Depends(requires("logs"))):
    connection = get_connection()
    try:
        conditions, params = date_filters(year, month, day, "timestamp")
        if employee_id:
            conditions.append("employee_id=?")
            params.append(employee_id.strip().upper())
        if module:
            conditions.append("module=?")
            params.append(module.strip().upper())
        if status:
            conditions.append("status=?")
            params.append(status.strip().upper())
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        if page:
            return paginate_sql(connection, f"SELECT * FROM audit_logs {where} ORDER BY id DESC",
                                params, page, page_size)
        rows = connection.execute(
            f"SELECT * FROM audit_logs {where} ORDER BY id DESC LIMIT ?",
            [*params, min(limit, 2000)],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        connection.close()


@router.get("/logs/modules")
def log_modules(user: dict = Depends(requires("logs"))):
    connection = get_connection()
    try:
        return [r["module"] for r in connection.execute(
            "SELECT DISTINCT module FROM audit_logs ORDER BY module"
        ).fetchall()]
    finally:
        connection.close()
