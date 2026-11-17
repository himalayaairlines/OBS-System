"""Remaining stock after each sector (7.9, per iPad since 7.11).

After a sector is finished, each iPad confirms what is left in its own part
of the trolley before the next sector starts (the last sector ends with the
final stock return instead). The figures are calculated for the whole trip:
loaded, minus everything sold up to and including this sector, minus wastage.
Each device types what it can see; the sum of the devices is the sector's
remaining stock, and the system figure is kept beside it. Management and the
administrator read every count.
"""

from fastapi import HTTPException

SCHEMA = """
CREATE TABLE IF NOT EXISTS sector_stock_counts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id INTEGER NOT NULL,
    device TEXT NOT NULL DEFAULT '',
    submitted_by TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    remarks TEXT,
    UNIQUE(flight_id, device),
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
    # 7.9 kept one count per sector; 7.11 keeps one per iPad. Rebuild with the
    # new shape (create new, copy, drop old, rename) and never RENAME the old
    # table: SQLite rewrites foreign keys in other tables to follow a rename,
    # which left sector_stock_items pointing at a table that was then dropped.
    cols = [r[1] for r in connection.execute("PRAGMA table_info('sector_stock_counts')").fetchall()]
    items_sql = (connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sector_stock_items'").fetchone() or [""])[0] or ""
    broken_items = "sector_stock_counts_old" in items_sql
    if "device" not in cols or broken_items:
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            if "device" not in cols:
                connection.executescript("""
                    CREATE TABLE sector_stock_counts_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        flight_id INTEGER NOT NULL,
                        device TEXT NOT NULL DEFAULT '',
                        submitted_by TEXT NOT NULL,
                        submitted_at TEXT NOT NULL,
                        remarks TEXT,
                        UNIQUE(flight_id, device),
                        FOREIGN KEY(flight_id) REFERENCES flights(id) ON DELETE CASCADE
                    );
                    INSERT INTO sector_stock_counts_new(id,flight_id,device,submitted_by,submitted_at,remarks)
                        SELECT id,flight_id,submitted_by,submitted_by,submitted_at,remarks FROM sector_stock_counts;
                    DROP TABLE sector_stock_counts;
                    ALTER TABLE sector_stock_counts_new RENAME TO sector_stock_counts;
                """)
            if broken_items:
                # Repair a database migrated by the first 7.11 build.
                connection.executescript("""
                    CREATE TABLE sector_stock_items_new (
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
                    INSERT INTO sector_stock_items_new SELECT * FROM sector_stock_items;
                    DROP TABLE sector_stock_items;
                    ALTER TABLE sector_stock_items_new RENAME TO sector_stock_items;
                """)
            leftover = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sector_stock_counts_old'").fetchone()
            if leftover:
                # An interrupted first-build migration: bring the rows across before dropping it.
                connection.executescript("""
                    INSERT OR IGNORE INTO sector_stock_counts(id,flight_id,device,submitted_by,submitted_at,remarks)
                        SELECT id,flight_id,submitted_by,submitted_by,submitted_at,remarks FROM sector_stock_counts_old;
                    DROP TABLE sector_stock_counts_old;
                """)
        finally:
            connection.execute("PRAGMA foreign_keys = ON")


def devices_for(connection, flight_id: int) -> list[str]:
    """The iPad accounts assigned to this leg (one or two)."""
    return [r["crew_account"] for r in connection.execute(
        "SELECT crew_account FROM flight_assignments WHERE flight_id=? ORDER BY id", (flight_id,))]


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

    devices = devices_for(connection, leg_id)
    headers = {r["device"]: dict(r) for r in connection.execute(
        "SELECT * FROM sector_stock_counts WHERE flight_id=?", (leg_id,)).fetchall()}
    # declared per product = the sum of what each iPad that has confirmed typed in.
    saved = {}          # product_id -> summed declared
    per_device = {}     # product_id -> {device: declared}
    for device, header in headers.items():
        for r in connection.execute("SELECT * FROM sector_stock_items WHERE count_id=?", (header["id"],)):
            saved[r["product_id"]] = saved.get(r["product_id"], 0) + int(r["declared_remaining"])
            per_device.setdefault(r["product_id"], {})[device] = int(r["declared_remaining"])
    submitted_devices = [d for d in devices if d in headers] + [d for d in headers if d not in devices]
    all_in = bool(devices) and all(d in headers for d in devices)

    items = []
    for row in connection.execute(
            """SELECT i.product_id, i.loaded_quantity, i.wasted_quantity, p.product_code, p.product_name, p.unit
               FROM inventory i JOIN products p ON p.id = i.product_id
               WHERE i.flight_id=? ORDER BY p.category, p.product_name""", (root["id"],)).fetchall():
        pid = row["product_id"]
        before = sold_before.get(pid, 0)
        here = sold_here.get(pid, 0)
        expected = int(row["loaded_quantity"]) - before - here - int(row["wasted_quantity"] or 0)
        declared = expected if not headers else saved.get(pid, 0)
        items.append({
            "product_id": pid, "product_code": row["product_code"], "product_name": row["product_name"],
            "unit": row["unit"], "loaded_quantity": int(row["loaded_quantity"]),
            "sold_before": before, "sold_quantity": here, "wasted_quantity": int(row["wasted_quantity"] or 0),
            "expected_remaining": expected, "declared_remaining": declared, "variance": declared - expected,
            "by_device": per_device.get(pid, {}),
        })
    last = max(headers.values(), key=lambda h: h["submitted_at"]) if headers else None
    return {
        "flight_id": leg_id, "segment": index + 1, "segments": len(legs),
        "flight_number": leg["flight_number"], "origin": leg["origin"], "destination": leg["destination"],
        "flight_date": leg["flight_date"],
        "devices": devices, "submitted_devices": submitted_devices,
        "waiting_for": [d for d in devices if d not in headers],
        "submitted": all_in, "submitted_by": last["submitted_by"] if last else None,
        "submitted_at": last["submitted_at"] if last else None,
        "remarks": "; ".join(f"{d}: {h['remarks']}" for d, h in headers.items() if h.get("remarks")) or None,
        "device_remarks": {d: h.get("remarks") for d, h in headers.items()},
        "items": items,
        "total_expected": sum(i["expected_remaining"] for i in items),
        "total_declared": sum(i["declared_remaining"] for i in items),
        "total_variance": sum(i["variance"] for i in items),
        "has_variance": any(i["variance"] for i in items),
    }


def save_sector_count(connection, legs, leg_id, lines: dict, remarks, by, now, device=None) -> dict:
    """Store one iPad's confirmation. May be re-submitted until the sector locks.

    `lines` holds what THIS device can see. A product it leaves out counts as 0
    on this device once any line is typed; with no lines at all the device
    confirms the system figure (less what the other device already declared).
    """
    device = device or by
    position = sector_count(connection, legs, leg_id)
    header = connection.execute("SELECT id FROM sector_stock_counts WHERE flight_id=? AND device=?",
                                (leg_id, device)).fetchone()
    if header:
        count_id = header["id"]
        connection.execute("UPDATE sector_stock_counts SET submitted_by=?, submitted_at=?, remarks=? WHERE id=?",
                           (by, now, remarks, count_id))
        connection.execute("DELETE FROM sector_stock_items WHERE count_id=?", (count_id,))
    else:
        count_id = connection.execute(
            "INSERT INTO sector_stock_counts(flight_id,device,submitted_by,submitted_at,remarks) VALUES (?,?,?,?,?)",
            (leg_id, device, by, now, remarks)).lastrowid
    others = [d for d in position["submitted_devices"] if d != device]
    for item in position["items"]:
        other_total = sum(v for d, v in item["by_device"].items() if d != device)
        default = item["expected_remaining"] - other_total if others else item["expected_remaining"]
        declared = int(lines.get(item["product_id"], max(0, default) if not lines else 0))
        if declared < 0:
            raise HTTPException(status_code=400, detail="A remaining quantity cannot be negative.")
        connection.execute(
            """INSERT INTO sector_stock_items(count_id,product_id,loaded_quantity,sold_before,sold_quantity,
                   wasted_quantity,expected_remaining,declared_remaining,variance) VALUES (?,?,?,?,?,?,?,?,?)""",
            (count_id, item["product_id"], item["loaded_quantity"], item["sold_before"], item["sold_quantity"],
             item["wasted_quantity"], item["expected_remaining"], declared, declared - item["expected_remaining"]))
    connection.commit()
    return sector_count(connection, legs, leg_id)


def sector_count_submitted(connection, leg_id: int, device: str | None = None) -> bool:
    """Every assigned iPad has confirmed (or this one, when `device` is given)."""
    if device:
        return bool(connection.execute("SELECT 1 FROM sector_stock_counts WHERE flight_id=? AND device=?",
                                       (leg_id, device)).fetchone())
    devices = devices_for(connection, leg_id)
    done = {r["device"] for r in connection.execute(
        "SELECT device FROM sector_stock_counts WHERE flight_id=?", (leg_id,))}
    return bool(devices) and all(d in done for d in devices) if devices else bool(done)
