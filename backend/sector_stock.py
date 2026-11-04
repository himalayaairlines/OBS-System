"""Remaining stock after each sector (7.9).

After a sector is finished and its forms are saved, the crew confirm what is
left in the trolley before the next sector starts (or before the final stock
return on a one-way flight / the last sector). The figures are calculated:
loaded for the trip, minus everything sold up to and including this sector,
minus wastage. The crew may correct a line to what they can actually see;
the system figure is kept beside it. Management and the administrator read
every count. The final stock return (reconciliations) is unchanged.
"""

from fastapi import HTTPException

SCHEMA = """
CREATE TABLE IF NOT EXISTS sector_stock_counts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id INTEGER NOT NULL UNIQUE,
    submitted_by TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    remarks TEXT,
    FOREIGN KEY(flight_id) REFERENCES flights(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS sector_stock_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    count_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    loaded_quantity INTEGER NOT NULL DEFAULT 0,
    sold_before INTEGER NOT NULL DEFAULT 0,
    sold_quantity INTEGER NOT NULL DEFAULT 0,
    wasted_quantity INTEGER NOT NULL DEFAULT 0,
    expected_remaining INTEGER NOT NULL DEFAULT 0,
    declared_remaining INTEGER NOT NULL DEFAULT 0,
    variance INTEGER NOT NULL DEFAULT 0,
    UNIQUE(count_id, product_id),
    FOREIGN KEY(count_id) REFERENCES sector_stock_counts(id) ON DELETE CASCADE
);
"""


def initialize_sector_stock(connection):
    connection.executescript(SCHEMA)


def _sold_by_product(connection, flight_ids):
    if not flight_ids:
        return {}
    marks = ",".join("?" * len(flight_ids))
    rows = connection.execute(
        f"""SELECT product_id, SUM(quantity) q FROM sales
            WHERE flight_id IN ({marks}) AND product_id IS NOT NULL
              AND sale_type IN ('NORMAL','COMPLEMENTARY')
            GROUP BY product_id""", tuple(flight_ids)).fetchall()
    return {r["product_id"]: int(r["q"] or 0) for r in rows}


def sector_count(connection, legs: list[dict], leg_id: int) -> dict:
    """The remaining-stock position after `leg_id`, with the saved count if any."""
    index = next((i for i, l in enumerate(legs) if l["id"] == leg_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="Flight not found.")
    leg = legs[index]
    root = legs[0]
    before_ids = [l["id"] for l in legs[:index]]
    sold_before = _sold_by_product(connection, before_ids)
    sold_here = _sold_by_product(connection, [leg_id])

    header = connection.execute("SELECT * FROM sector_stock_counts WHERE flight_id=?", (leg_id,)).fetchone()
    saved = {}
    if header:
        saved = {r["product_id"]: dict(r) for r in connection.execute(
            "SELECT * FROM sector_stock_items WHERE count_id=?", (header["id"],)).fetchall()}

    items = []
    for row in connection.execute(
            """SELECT i.product_id, i.loaded_quantity, i.wasted_quantity, p.product_code, p.product_name, p.unit
               FROM inventory i JOIN products p ON p.id = i.product_id
               WHERE i.flight_id=? ORDER BY p.category, p.product_name""", (root["id"],)).fetchall():
        pid = row["product_id"]
        before = sold_before.get(pid, 0)
        here = sold_here.get(pid, 0)
        expected = int(row["loaded_quantity"]) - before - here - int(row["wasted_quantity"] or 0)
        line = saved.get(pid)
        declared = int(line["declared_remaining"]) if line else expected
        items.append({
            "product_id": pid, "product_code": row["product_code"], "product_name": row["product_name"],
            "unit": row["unit"], "loaded_quantity": int(row["loaded_quantity"]),
            "sold_before": before, "sold_quantity": here, "wasted_quantity": int(row["wasted_quantity"] or 0),
            "expected_remaining": expected, "declared_remaining": declared, "variance": declared - expected,
        })
    return {
        "flight_id": leg_id, "segment": index + 1, "segments": len(legs),
        "flight_number": leg["flight_number"], "origin": leg["origin"], "destination": leg["destination"],
        "flight_date": leg["flight_date"],
        "submitted": bool(header), "submitted_by": header["submitted_by"] if header else None,
        "submitted_at": header["submitted_at"] if header else None,
        "remarks": header["remarks"] if header else None,
        "items": items,
        "total_expected": sum(i["expected_remaining"] for i in items),
        "total_declared": sum(i["declared_remaining"] for i in items),
        "total_variance": sum(i["variance"] for i in items),
        "has_variance": any(i["variance"] for i in items),
    }


def save_sector_count(connection, legs, leg_id, lines: dict, remarks, by, now) -> dict:
    """Store the crew's confirmation. May be re-submitted until the sector's forms lock."""
    position = sector_count(connection, legs, leg_id)
    header = connection.execute("SELECT id FROM sector_stock_counts WHERE flight_id=?", (leg_id,)).fetchone()
    if header:
        count_id = header["id"]
        connection.execute("UPDATE sector_stock_counts SET submitted_by=?, submitted_at=?, remarks=? WHERE id=?",
                           (by, now, remarks, count_id))
        connection.execute("DELETE FROM sector_stock_items WHERE count_id=?", (count_id,))
    else:
        count_id = connection.execute(
            "INSERT INTO sector_stock_counts(flight_id,submitted_by,submitted_at,remarks) VALUES (?,?,?,?)",
            (leg_id, by, now, remarks)).lastrowid
    for item in position["items"]:
        declared = int(lines.get(item["product_id"], item["expected_remaining"]))
        if declared < 0:
            raise HTTPException(status_code=400, detail="A remaining quantity cannot be negative.")
        connection.execute(
            """INSERT INTO sector_stock_items(count_id,product_id,loaded_quantity,sold_before,sold_quantity,
                   wasted_quantity,expected_remaining,declared_remaining,variance) VALUES (?,?,?,?,?,?,?,?,?)""",
            (count_id, item["product_id"], item["loaded_quantity"], item["sold_before"], item["sold_quantity"],
             item["wasted_quantity"], item["expected_remaining"], declared, declared - item["expected_remaining"]))
    connection.commit()
    return sector_count(connection, legs, leg_id)


def sector_count_submitted(connection, leg_id: int) -> bool:
    return bool(connection.execute("SELECT 1 FROM sector_stock_counts WHERE flight_id=?", (leg_id,)).fetchone())
