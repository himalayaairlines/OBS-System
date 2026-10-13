"""SQLite schema, migrations and seed data for the Himalaya Airlines OBS system."""

from datetime import date
import os
from pathlib import Path
import sqlite3

from crew_seed import CABIN_CREW, CREW_POSITIONS

BASE_DIR = Path(__file__).resolve().parent
# OBS_DB_PATH lets tests and alternative deployments point at another file.
DB_PATH = Path(os.getenv("OBS_DB_PATH") or BASE_DIR / "obs.db")

ROLES = (
    "crew_supervisor",
    "inventory",
    "inventory_supervisor",
    "management",
    "finance",
    "admin",
)

# The ordinary `crew` role was retired: a sector is worked from one iPad, so
# there is a single cabin account and it carries supervisory authority.
# Any legacy crew account is migrated to crew_supervisor on startup.
RETIRED_ROLES = ("crew",)

# Permission keys drive both the API guards and the mobile navigation.
# Every role can read its own in-app notifications; that needs no key.
#   comms_view       Communication Manager: dashboard, alerts, rules (read)
#   comms_rules      create and edit stock-alert rules they own
#   comms_rules_all  edit, disable or delete anyone's rule
#   comms_groups     recipient groups and escalation policies
#   comms_admin      delivery logs, retry/cancel, communication settings
#   comms_templates  email templates
#   smtp_view / smtp_manage / smtp_test   SMTP settings (manage = credentials)
ROLE_PERMISSIONS = {
    # The OBS iPad account. Forms the duty group, sells, finishes the sector,
    # declares leftover stock and files the post-flight cabin reports.
    "crew_supervisor": {"crew", "sync", "reconcile_submit", "flight_reports"},
    # `groups` is read-only visibility of the duty groups the crew formed.
    # Nobody on the ground builds them any more.
    "inventory": {"inventory", "warehouse", "groups", "sync",
                  "comms_view", "comms_rules"},
    # The inventory supervisor reconciles what the crew sold against what came
    # back, so they read the sales ledger and may correct a mis-keyed line.
    "inventory_supervisor": {
        "inventory", "warehouse", "warehouse_approve", "bundles",
        "groups", "stock_add", "reconcile_approve", "sync",
        "sales_view", "sales_edit",
        "comms_view", "comms_rules", "comms_groups", "comms_item_settings",
    },
    # Management oversee and correct; they do not sell, so no crew sales screen.
    # `groups_manage` is the post-lock amendment of a duty group, with remarks.
    "management": {
        "inventory", "warehouse", "bundles", "groups", "groups_manage",
        "stock_add", "dashboard", "sync", "logs", "rates", "rates_edit",
        "sales_view", "sales_edit", "reports", "warehouse_approve",
        "reconcile_approve", "reconcile_submit", "flight_reports", "reports_review",
        "comms_view", "comms_rules", "comms_rules_all", "comms_groups", "comms_admin", "comms_item_settings",
        "comms_templates", "smtp_view",
    },
    # Finance set rates and read performance. No sales, commission or sync.
    "finance": {"rates", "rates_edit", "dashboard", "reports"},
    # The administrator owns system configuration, including SMTP credentials.
    "admin": {"users", "crew_members", "smtp_view", "smtp_manage", "smtp_test",
              "comms_admin", "comms_templates",
              # Stock Alert Settings: see and configure alerts (7.4).
              "comms_view", "comms_rules", "comms_rules_all", "comms_groups", "comms_item_settings"},
}

# --------------------------------------------------------------------------- #
# Commission
# --------------------------------------------------------------------------- #
COMMISSION_RATE = 0.10

# INDIVIDUAL : each crew member earns COMMISSION_RATE of the sales they
#              personally recorded. Ranking is true individual performance.
# POOLED     : the sector's commission is pooled and split equally across the
#              duty group, and the seller is kept for traceability only.
#
# Pending the department decision; flipping this constant is the only change
# required, because both models read the same `sales.sold_by_crew_member_id`.
COMMISSION_MODEL = "INDIVIDUAL"

# A duty group is the crew working one sector: exactly four cabin crew.
DUTY_GROUP_SIZE = 4

# The crew may correct the duty group once after it has been formed, with
# mandatory remarks. After that the membership stands, because it is the basis
# of that sector's commission. Management can still correct it.
DUTY_GROUP_AMEND_LIMIT = 1

# --------------------------------------------------------------------------- #
# Sign-in protection
# --------------------------------------------------------------------------- #
# Consecutive wrong passwords before the account is locked. Only an
# administrator can unlock it; the count resets on a successful sign-in.
LOGIN_ATTEMPT_LIMIT = 5

# --------------------------------------------------------------------------- #
# Post-flight cabin reports
# --------------------------------------------------------------------------- #
# A submitted report may be pulled back by the crew this many times. One is
# deliberate: it covers the honest mistake spotted straight after submitting,
# without turning a filed report into a document that can be rewritten at will.
REPORT_UNSUBMIT_LIMIT = 1


def get_connection():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 8000")
    return connection


def _columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(connection, table, column, definition):
    if table in _tables(connection) and column not in _columns(connection, table):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _tables(connection):
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'crew',
    active INTEGER NOT NULL DEFAULT 1,
    -- Consecutive wrong passwords. Cleared by a successful sign-in, by an
    -- administrator unlocking the account, or by a password reset.
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Roster of physical crew people. Four of them share one iPad login account.
CREATE TABLE IF NOT EXISTS crew_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crew_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    position TEXT NOT NULL DEFAULT 'Cabin Crew',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- The crew actually working ONE SECTOR, chosen on the iPad by the crew
-- themselves at the start of that sector. It belongs to a single leg and is
-- never reused: the return leg of a round trip forms its own, even when the
-- same people are picked again.
--
-- `client_ref` is the reference the iPad generated offline. Creation is
-- idempotent on it, so a replayed sync cannot produce a second group.
--
-- `locked` is set by the first sale. After that the membership is the
-- historical record behind that sector's commission and may only be amended
-- with remarks, which is why the amendment columns exist.
CREATE TABLE IF NOT EXISTS duty_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id INTEGER NOT NULL UNIQUE,
    client_ref TEXT UNIQUE,
    label TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    locked INTEGER NOT NULL DEFAULT 0,
    locked_at TEXT,
    amend_count INTEGER NOT NULL DEFAULT 0,
    amended_by TEXT,
    amended_at TEXT,
    amend_remarks TEXT,
    FOREIGN KEY(flight_id) REFERENCES flights(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS duty_group_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    duty_group_id INTEGER NOT NULL,
    crew_member_id INTEGER NOT NULL,
    UNIQUE(duty_group_id, crew_member_id),
    FOREIGN KEY(duty_group_id) REFERENCES duty_groups(id) ON DELETE CASCADE,
    FOREIGN KEY(crew_member_id) REFERENCES crew_members(id)
);

CREATE TABLE IF NOT EXISTS flights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_number TEXT NOT NULL,
    flight_date TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    trip_type TEXT NOT NULL DEFAULT 'ONE_WAY',
    direction TEXT NOT NULL DEFAULT 'OUTBOUND',
    linked_flight_id INTEGER,
    aircraft_type TEXT,
    aircraft TEXT,
    passengers INTEGER NOT NULL DEFAULT 0,
    aircraft_locked INTEGER NOT NULL DEFAULT 0,
    pax_locked INTEGER NOT NULL DEFAULT 0,
    aircraft_entered_by TEXT,
    aircraft_entered_at TEXT,
    pax_entered_by TEXT,
    pax_entered_at TEXT,
    -- OPEN   : selling
    -- FLOWN  : crew pressed "finish flight"; sales are closed, stock not yet returned
    -- CLOSED : leftover stock declared and approved
    status TEXT NOT NULL DEFAULT 'OPEN',
    finished_by TEXT,
    finished_at TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(flight_number, flight_date, direction)
);

-- Inventory assigns the OBS iPad account to a leg. Crew names are not part of
-- this any more: the people are chosen on board, in duty_groups.
CREATE TABLE IF NOT EXISTS flight_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id INTEGER NOT NULL UNIQUE,
    crew_account TEXT NOT NULL,
    assigned_by TEXT,
    assigned_at TEXT,
    FOREIGN KEY(flight_id) REFERENCES flights(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_code TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'pcs',
    selling_price REAL NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    reorder_level INTEGER NOT NULL DEFAULT 20,
    active INTEGER NOT NULL DEFAULT 1
);

-- Central warehouse stock. OBS flight inventory is drawn from here.
CREATE TABLE IF NOT EXISTS main_inventory (
    product_id INTEGER PRIMARY KEY,
    quantity INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS main_inventory_txn (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    change_qty INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    reason TEXT NOT NULL,
    reference TEXT,
    -- The date the movement belongs to in the books. For a trolley load or a
    -- post-flight return that is the FLIGHT date, which is not necessarily the
    -- day the clerk keyed it in.
    stock_date TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL UNIQUE,
    po_code TEXT NOT NULL DEFAULT 'ANC',
    po_number TEXT,
    fiscal_year TEXT,
    supply_type TEXT NOT NULL DEFAULT 'OBS',
    order_placement_date TEXT,
    expected_delivery_date TEXT,   -- what was promised
    delivery_date TEXT,            -- what actually happened, set from the receipt
    received_date TEXT,
    received_location TEXT,
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    remarks TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_remarks TEXT,
    edited_by TEXT,
    edited_at TEXT,
    edit_remarks TEXT
);

CREATE TABLE IF NOT EXISTS purchase_order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    -- NULL while the line proposes a product that does not exist yet. The
    -- product is created only when the order is approved.
    product_id INTEGER,
    new_product_code TEXT,
    new_product_name TEXT,
    new_product_category TEXT,
    new_product_unit TEXT,
    new_selling_price REAL,
    new_reorder_level INTEGER,
    quantity INTEGER NOT NULL,
    received_quantity INTEGER NOT NULL DEFAULT 0,
    unit_cost REAL NOT NULL DEFAULT 0,
    FOREIGN KEY(order_id) REFERENCES purchase_orders(id) ON DELETE CASCADE,
    FOREIGN KEY(product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS bundles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_code TEXT NOT NULL UNIQUE,
    bundle_name TEXT NOT NULL,
    bundle_price REAL NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bundle_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    UNIQUE(bundle_id, product_id),
    FOREIGN KEY(bundle_id) REFERENCES bundles(id) ON DELETE CASCADE,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    loaded_quantity INTEGER NOT NULL DEFAULT 0,
    sold_quantity INTEGER NOT NULL DEFAULT 0,
    returned_quantity INTEGER NOT NULL DEFAULT 0,
    wasted_quantity INTEGER NOT NULL DEFAULT 0,
    entered_by TEXT,
    entered_at TEXT,
    inventory_locked INTEGER NOT NULL DEFAULT 0,
    UNIQUE(flight_id, product_id),
    FOREIGN KEY(flight_id) REFERENCES flights(id) ON DELETE CASCADE,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL UNIQUE,
    order_ref TEXT,
    flight_id INTEGER NOT NULL,
    duty_group_id INTEGER,
    -- The crew member who made this sale. This is what commission and the
    -- performance ranking are built from.
    sold_by_crew_member_id INTEGER,
    product_id INTEGER,
    bundle_id INTEGER,
    -- The device account that transmitted the sale, kept for traceability.
    -- It identifies an iPad, not a salesperson.
    crew_id TEXT NOT NULL,
    seat_number TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL DEFAULT 0,
    payment_method TEXT NOT NULL DEFAULT 'Cash',
    transaction_time TEXT NOT NULL,
    transaction_currency TEXT NOT NULL DEFAULT 'NPR',
    exchange_rate_to_npr REAL NOT NULL DEFAULT 1,
    transaction_currency_amount REAL NOT NULL DEFAULT 0,
    npr_amount REAL NOT NULL DEFAULT 0,
    sale_type TEXT NOT NULL DEFAULT 'NORMAL',
    remarks TEXT,
    edited_by TEXT,
    edited_at TEXT,
    edit_remarks TEXT,
    FOREIGN KEY(flight_id) REFERENCES flights(id),
    FOREIGN KEY(duty_group_id) REFERENCES duty_groups(id),
    FOREIGN KEY(sold_by_crew_member_id) REFERENCES crew_members(id),
    FOREIGN KEY(product_id) REFERENCES products(id),
    FOREIGN KEY(bundle_id) REFERENCES bundles(id)
);

-- Post-trip declaration of leftover stock.
--
-- The trolley is loaded once for the whole trip and worked from one iPad, so
-- there is ONE count, taken after the final sector. `flight_id` therefore
-- holds the trip root (the outbound leg), the same leg that owns the stock.
--
-- status: PENDING -> declared by the crew, waiting on the inventory supervisor
--         APPROVED / REJECTED
CREATE TABLE IF NOT EXISTS reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id INTEGER NOT NULL UNIQUE,
    submitted_by TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_remarks TEXT,
    amended_by TEXT,
    amended_at TEXT,
    amend_remarks TEXT,
    FOREIGN KEY(flight_id) REFERENCES flights(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reconciliation_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reconciliation_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    loaded_quantity INTEGER NOT NULL DEFAULT 0,
    sold_quantity INTEGER NOT NULL DEFAULT 0,
    expected_remaining INTEGER NOT NULL DEFAULT 0,
    -- Defaults to the system figure. A supervisor may override it, with remarks.
    declared_remaining INTEGER NOT NULL DEFAULT 0,
    variance INTEGER NOT NULL DEFAULT 0,
    override_by TEXT,
    override_at TEXT,
    override_remarks TEXT,
    UNIQUE(reconciliation_id, product_id),
    FOREIGN KEY(reconciliation_id) REFERENCES reconciliations(id) ON DELETE CASCADE,
    FOREIGN KEY(product_id) REFERENCES products(id)
);

-- Opening balance that seeds the first day of a month in the OBS daily register.
CREATE TABLE IF NOT EXISTS obs_register_opening (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    month TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    set_by TEXT,
    set_at TEXT,
    UNIQUE(product_id, month),
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);

-- Supervisor overrides for a single cell-row of the daily register.
-- A NULL column means "use the computed value".
CREATE TABLE IF NOT EXISTS obs_register_adjustment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_date TEXT NOT NULL,
    product_id INTEGER NOT NULL,
    received_qty INTEGER,
    sales_qty INTEGER,
    remarks TEXT NOT NULL,
    edited_by TEXT NOT NULL,
    edited_at TEXT NOT NULL,
    UNIQUE(stock_date, product_id),
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS exchange_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    currency_code TEXT NOT NULL,
    -- Rates are quoted per `unit` of foreign currency: INR is quoted per 100,
    -- JPY per 10, most others per 1. NPR per single unit = rate_to_npr / unit.
    unit REAL NOT NULL DEFAULT 1,
    rate_to_npr REAL NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    updated_by TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- The controlled cabin forms completed after a sector: the security check,
-- the cabin safety report, the voyage report and lost and found.
--
-- One set belongs to one duty group on one sector, so on a round trip the
-- outbound crew and the returning crew each file their own.
--
-- `issue_no` runs serially per form type across the airline and `rev_no`
-- counts the revisions of this particular report, so the footer of a printed
-- copy reads the way the paper form does. `report_date` is the day it was
-- filled, not the day the form template was issued.
CREATE TABLE IF NOT EXISTS flight_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id INTEGER NOT NULL,
    duty_group_id INTEGER,
    report_type TEXT NOT NULL,
    form_code TEXT NOT NULL,
    issue_no INTEGER NOT NULL,
    rev_no INTEGER NOT NULL DEFAULT 0,
    report_date TEXT NOT NULL,
    -- DRAFT     : being filled, or pulled back by the crew
    -- SUBMITTED : filed; visible to management
    status TEXT NOT NULL DEFAULT 'DRAFT',
    payload TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    submitted_by TEXT,
    submitted_at TEXT,
    unsubmit_count INTEGER NOT NULL DEFAULT 0,
    edited_by TEXT,
    edited_at TEXT,
    edit_remarks TEXT,
    -- The reference the iPad generated. Filing is idempotent on it, so a
    -- replayed offline queue cannot produce a second report.
    client_ref TEXT UNIQUE,
    UNIQUE(flight_id, report_type),
    FOREIGN KEY(flight_id) REFERENCES flights(id) ON DELETE CASCADE,
    FOREIGN KEY(duty_group_id) REFERENCES duty_groups(id)
);

-- Every version of a report, kept so a correction never erases what was filed.
CREATE TABLE IF NOT EXISTS flight_report_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    rev_no INTEGER NOT NULL,
    payload TEXT NOT NULL,
    action TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    remarks TEXT,
    FOREIGN KEY(report_id) REFERENCES flight_reports(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    employee_id TEXT,
    role TEXT,
    module TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'SUCCESS',
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_sales_flight ON sales(flight_id);
CREATE INDEX IF NOT EXISTS idx_sales_duty_group ON sales(duty_group_id);
CREATE INDEX IF NOT EXISTS idx_sales_seller ON sales(sold_by_crew_member_id);
CREATE INDEX IF NOT EXISTS idx_sales_time ON sales(transaction_time);
-- The offline queue looks a transaction up by this on every sync attempt.
-- Not unique: one passenger order writes one row per product line.
CREATE INDEX IF NOT EXISTS idx_sales_order_ref ON sales(order_ref);
CREATE INDEX IF NOT EXISTS idx_inventory_flight ON inventory(flight_id);
CREATE INDEX IF NOT EXISTS idx_logs_time ON audit_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_reports_flight ON flight_reports(flight_id);
CREATE INDEX IF NOT EXISTS idx_reports_status ON flight_reports(status);
"""


def _has_unique_index(connection, table, columns):
    """True if `table` has a UNIQUE index covering exactly `columns`."""
    if table not in _tables(connection):
        return True  # will be created fresh with the constraint
    for index in connection.execute(f"PRAGMA index_list({table})").fetchall():
        if not index["unique"]:
            continue
        cols = [r["name"] for r in connection.execute(f"PRAGMA index_info({index['name']})")]
        if [c.lower() for c in cols] == [c.lower() for c in columns]:
            return True
    return False


def _rebuild_reconciliation_items(connection):
    """Add the UNIQUE(reconciliation_id, product_id) that ON CONFLICT relies on.

    Databases created before that constraint existed fail with
    "ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint"
    the moment a supervisor approves or corrects a stock return. SQLite cannot
    add a constraint in place, so the table is rebuilt and its rows carried
    across, keeping the newest row per (reconciliation, product).
    """
    if _has_unique_index(connection, "reconciliation_items", ["reconciliation_id", "product_id"]):
        return
    columns = _columns(connection, "reconciliation_items")
    carried = [c for c in (
        "reconciliation_id", "product_id", "loaded_quantity", "sold_quantity",
        "expected_remaining", "declared_remaining", "variance",
        "override_by", "override_at", "override_remarks",
    ) if c in columns]
    names = ", ".join(carried)

    # SQLite checks foreign keys while the table is swapped, so they are off for
    # the duration. It only takes effect outside a transaction.
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("ALTER TABLE reconciliation_items RENAME TO reconciliation_items_old")
    connection.execute("""
        CREATE TABLE reconciliation_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reconciliation_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            loaded_quantity INTEGER NOT NULL DEFAULT 0,
            sold_quantity INTEGER NOT NULL DEFAULT 0,
            expected_remaining INTEGER NOT NULL DEFAULT 0,
            declared_remaining INTEGER NOT NULL DEFAULT 0,
            variance INTEGER NOT NULL DEFAULT 0,
            override_by TEXT,
            override_at TEXT,
            override_remarks TEXT,
            UNIQUE(reconciliation_id, product_id),
            FOREIGN KEY(reconciliation_id) REFERENCES reconciliations(id) ON DELETE CASCADE,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    """)
    connection.execute(
        f"""INSERT INTO reconciliation_items({names})
            SELECT {names} FROM reconciliation_items_old
            WHERE id IN (SELECT MAX(id) FROM reconciliation_items_old
                         GROUP BY reconciliation_id, product_id)"""
    )
    connection.execute("DROP TABLE reconciliation_items_old")
    connection.commit()
    connection.execute("PRAGMA foreign_keys = ON")


def _migrate_legacy(connection):
    """Bring a v4 prototype database up to the v5 shape without losing data."""
    tables = _tables(connection)
    if "flights" in tables:
        for column, definition in (
            ("trip_type", "TEXT NOT NULL DEFAULT 'ONE_WAY'"),
            ("direction", "TEXT NOT NULL DEFAULT 'OUTBOUND'"),
            ("linked_flight_id", "INTEGER"),
            ("aircraft_locked", "INTEGER NOT NULL DEFAULT 0"),
            ("aircraft_entered_by", "TEXT"),
            ("aircraft_entered_at", "TEXT"),
            ("status", "TEXT NOT NULL DEFAULT 'OPEN'"),
            ("aircraft_type", "TEXT"), ("finished_by", "TEXT"), ("finished_at", "TEXT"),
        ):
            _add_column(connection, "flights", column, definition)
    if "purchase_orders" in tables:
        for column, definition in (
            ("po_code", "TEXT NOT NULL DEFAULT 'ANC'"), ("po_number", "TEXT"),
            ("fiscal_year", "TEXT"), ("supply_type", "TEXT NOT NULL DEFAULT 'OBS'"),
            ("order_placement_date", "TEXT"), ("delivery_date", "TEXT"),
            ("expected_delivery_date", "TEXT"),
            ("received_date", "TEXT"), ("received_location", "TEXT"),
            ("edited_by", "TEXT"), ("edited_at", "TEXT"), ("edit_remarks", "TEXT"),
        ):
            _add_column(connection, "purchase_orders", column, definition)
    if "purchase_order_items" in tables:
        _add_column(connection, "purchase_order_items", "received_quantity",
                    "INTEGER NOT NULL DEFAULT 0")
    if "products" in tables:
        _add_column(connection, "products", "unit", "TEXT NOT NULL DEFAULT 'pcs'")
        _add_column(connection, "products", "reorder_level", "INTEGER NOT NULL DEFAULT 20")
    if "sales" in tables:
        for column, definition in (
            ("order_ref", "TEXT"),
            ("group_id", "INTEGER"),
            ("bundle_id", "INTEGER"),
            ("duty_group_id", "INTEGER"),
            ("sold_by_crew_member_id", "INTEGER"),
            ("edited_by", "TEXT"),
            ("edited_at", "TEXT"),
            ("edit_remarks", "TEXT"),
        ):
            _add_column(connection, "sales", column, definition)
    if "reconciliations" in tables:
        # The per-device wait is gone: one iPad works the trip, so a
        # declaration is ready for review the moment it arrives.
        connection.execute(
            "UPDATE reconciliations SET status='PENDING' WHERE status='AWAITING_DEVICES'"
        )
    if "users" in tables:
        # The ordinary crew role was retired; the account keeps working as the
        # single cabin login rather than being locked out mid-operation.
        connection.execute(
            "UPDATE users SET role='crew_supervisor' WHERE role='crew'"
        )
    if "duty_groups" in tables:
        _add_column(connection, "duty_groups", "amend_count", "INTEGER NOT NULL DEFAULT 0")
    _rebuild_reconciliation_items(connection)
    if "exchange_rates" in tables:
        _add_column(connection, "exchange_rates", "unit", "REAL NOT NULL DEFAULT 1")
    if "reconciliations" in tables:
        for column in ("amended_by", "amended_at", "amend_remarks"):
            _add_column(connection, "reconciliations", column, "TEXT")
    if "purchase_order_items" in tables:
        for column, definition in (
            ("new_product_code", "TEXT"), ("new_product_name", "TEXT"),
            ("new_product_category", "TEXT"), ("new_product_unit", "TEXT"),
            ("new_selling_price", "REAL"), ("new_reorder_level", "INTEGER"),
        ):
            _add_column(connection, "purchase_order_items", column, definition)
    if "main_inventory_txn" in tables:
        if "stock_date" not in _columns(connection, "main_inventory_txn"):
            _add_column(connection, "main_inventory_txn", "stock_date", "TEXT")
            connection.execute(
                "UPDATE main_inventory_txn SET stock_date=substr(created_at,1,10) "
                "WHERE stock_date IS NULL"
            )
    if "crew_members" in tables:
        _add_column(connection, "crew_members", "position", "TEXT NOT NULL DEFAULT 'Cabin Crew'")
    if "users" in tables:
        _add_column(connection, "users", "active", "INTEGER NOT NULL DEFAULT 1")
        _add_column(connection, "users", "created_at", "TEXT")
        _add_column(connection, "users", "failed_attempts", "INTEGER NOT NULL DEFAULT 0")
        _add_column(connection, "users", "locked_at", "TEXT")
        # v4 used a CHECK constraint that predates the finance role.
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if row and "CHECK" in (row[0] or ""):
            connection.execute("ALTER TABLE users RENAME TO users_legacy")
            connection.executescript(SCHEMA.split("-- Roster")[0])
            connection.execute(
                """
                INSERT OR IGNORE INTO users(id, employee_id, name, password, role, active)
                SELECT id, employee_id, name, password,
                       CASE role
                           WHEN 'supervisor' THEN 'crew_supervisor'
                           WHEN 'obs' THEN 'inventory_supervisor'
                           ELSE role
                       END,
                       1
                FROM users_legacy
                """
            )
            connection.execute("DROP TABLE users_legacy")


DEMO_USERS = [
    # One account per OBS iPad. Both carry supervisory authority because a
    # sector is worked from a single device.
    ("CREW001", "OBS iPad 01", "1234", "crew_supervisor"),
    ("CSUP001", "OBS iPad 02", "1234", "crew_supervisor"),
    ("INV001", "Inventory Officer", "1234", "inventory"),
    ("ISUP001", "Inventory Supervisor", "1234", "inventory_supervisor"),
    ("MGT001", "Management User", "1234", "management"),
    ("FIN001", "Finance Officer", "1234", "finance"),
    ("ADMIN001", "Administrator", "1234", "admin"),
]

# Aircraft in the fleet. The seat map is derived from rows x layout.
AIRCRAFT_TYPES = {
    "A320": {"rows": 30, "layout": [["A", "B", "C"], ["D", "E", "F"]]},
    "A319": {"rows": 24, "layout": [["A", "B", "C"], ["D", "E", "F"]]},
}

# Catalogue mirrors the onboard product list. Quantities are always in pieces.
DEMO_PRODUCTS = [
    ("P001", "Gorkha", "Beverage", "pcs", 600.00, 240.00, 300),
    ("P002", "Coke", "Beverage", "pcs", 405.00, 121.50, 150),
    ("P003", "Water", "Beverage", "pcs", 270.00, 67.50, 200),
    ("P004", "Vodka", "Beverage", "pcs", 950.00, 420.00, 100),
    ("P005", "Sprite", "Beverage", "pcs", 405.00, 121.50, 150),
    ("P006", "Red Bull", "Beverage", "pcs", 675.00, 280.00, 250),
    ("P007", "Khukri Rum", "Beverage", "pcs", 900.00, 400.00, 100),
    ("P008", "Cup Noodles (Wai Wai)", "Food", "pcs", 405.00, 150.00, 120),
    ("P009", "Cup Noodles (Current)", "Food", "pcs", 405.00, 150.00, 60),
    ("P010", "Nescafe", "Beverage", "pcs", 472.50, 135.00, 150),
    ("P011", "Potato Chips", "Food", "pcs", 405.00, 145.00, 300),
    ("P012", "Chicken Pickle", "Food", "pcs", 540.00, 210.00, 250),
    ("P013", "Party Snacks", "Food", "pcs", 405.00, 145.00, 120),
    ("P014", "Fish Sittan", "Food", "pcs", 675.00, 280.00, 100),
    ("P015", "Buff Sittan", "Food", "pcs", 675.00, 280.00, 200),
    ("P016", "G & G Whisky", "Beverage", "pcs", 950.00, 430.00, 120),
    ("P017", "OKF Can Juice 340 ml", "Beverage", "pcs", 445.00, 160.00, 60),
    ("P018", "OKF Coffee 240 ml", "Beverage", "pcs", 445.00, 160.00, 60),
    ("P019", "OKF Smoothie 350 ml", "Beverage", "pcs", 540.00, 200.00, 60),
    ("P020", "Tong Garden Cashewnuts", "Food", "pcs", 675.00, 290.00, 60),
    ("P021", "Cards", "Merchandise", "pcs", 405.00, 150.00, 20),
    ("P022", "Rudrakshya Bracelet", "Merchandise", "pcs", 1350.00, 500.00, 40),
    ("P023", "Boddhichitta Bracelet", "Merchandise", "pcs", 1350.00, 500.00, 40),
]

# Exchange rates as published, quoted per `unit` of foreign currency.
# (code, unit, NPR per unit)
DEFAULT_RATES = [
    ("INR", 100, 160.00),   # Indian Rupee, quoted per 100
    ("USD", 1, 151.00),
    ("EUR", 1, 174.00),
    ("GBP", 1, 204.00),
    ("AUD", 1, 106.00),
    ("SGD", 1, 117.00),
    ("JPY", 10, 9.40),      # Japanese Yen, quoted per 10
    ("CNY", 1, 22.40),
    ("SAR", 1, 40.00),
    ("QAR", 1, 41.00),
    ("AED", 1, 40.90),
    ("MYR", 1, 36.80),
    ("KWD", 1, 490.00),
]


def initialize_database():
    from security import hash_password, is_hashed  # local import avoids a cycle

    connection = get_connection()
    try:
        _migrate_legacy(connection)
        connection.executescript(SCHEMA)

        for employee_id, name, password, role in DEMO_USERS:
            connection.execute(
                "INSERT OR IGNORE INTO users(employee_id,name,password,role,active) VALUES (?,?,?,?,1)",
                (employee_id, name, hash_password(password), role),
            )

        # Upgrade any plaintext password left over from the prototype.
        for row in connection.execute("SELECT id, password FROM users").fetchall():
            if not is_hashed(row["password"]):
                connection.execute(
                    "UPDATE users SET password=? WHERE id=?",
                    (hash_password(row["password"]), row["id"]),
                )

        for code, name, position in CABIN_CREW:
            connection.execute(
                "INSERT OR IGNORE INTO crew_members(crew_code,name,position,active) "
                "VALUES (?,?,?,1)",
                (code, name, position),
            )

        for code, name, category, unit, price, cost, reorder in DEMO_PRODUCTS:
            connection.execute(
                """INSERT OR IGNORE INTO products
                   (product_code,product_name,category,unit,selling_price,cost,reorder_level,active)
                   VALUES (?,?,?,?,?,?,?,1)""",
                (code, name, category, unit, price, cost, reorder),
            )

        # Seed warehouse stock so the workflow is demonstrable out of the box.
        now = date.today().isoformat()
        for row in connection.execute("SELECT id FROM products").fetchall():
            connection.execute(
                "INSERT OR IGNORE INTO main_inventory(product_id,quantity,updated_at) VALUES (?,?,?)",
                (row["id"], 500, now),
            )

        connection.execute(
            """INSERT OR IGNORE INTO exchange_rates
               (currency_code,unit,rate_to_npr,effective_from,effective_to,updated_by,active)
               VALUES ('NPR',1,1,'2000-01-01',NULL,'SYSTEM',1)"""
        ) if not connection.execute(
            "SELECT 1 FROM exchange_rates WHERE currency_code='NPR'"
        ).fetchone() else None

        for code, unit, rate in DEFAULT_RATES:
            exists = connection.execute(
                "SELECT id FROM exchange_rates WHERE currency_code=? AND effective_from=?",
                (code, "2000-01-01"),
            ).fetchone()
            if exists is None:
                connection.execute(
                    """INSERT INTO exchange_rates
                       (currency_code,unit,rate_to_npr,effective_from,effective_to,updated_by,active)
                       VALUES (?,?,?,?,?,?,1)""",
                    (code, unit, rate, "2000-01-01", None, "SYSTEM"),
                )

        # No starter crew groups: a duty group only exists once real crew have
        # picked their names on the iPad for a real sector.

        # SMTP settings, Communication Manager and stock alerts.
        from comms_schema import initialize_comms
        initialize_comms(connection, _add_column)

        connection.commit()
    finally:
        connection.close()
