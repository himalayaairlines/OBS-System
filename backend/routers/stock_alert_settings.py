"""Stock Alert Settings: per-item thresholds, effective configuration, coverage.

This sits on top of the Communication Manager rules (routers/communication.py).
Rules decide *who* is told, *how* and *how often*; the settings here let an
administrator see, for every item, what actually applies to it, and override
individual threshold values or switch alerts off for one item. Nothing here
sends mail: changes are written, an outbox event is queued in the same
transaction, and the normal engine/worker does the rest.

Permissions
  comms_view           read the item table, effective configuration, coverage,
                       suggestions; run a simulation
  comms_item_settings  change item settings (single, bulk, import)
"""

import csv
import io
import math
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

import alert_engine
from comms_core import ACTIVE_STATUSES, SEVERITY_RANK, iso, load_settings, parse_iso, utc_now
from database import get_connection
from paging import MAX_PAGE_SIZE
from security import log_action, requires

router = APIRouter(prefix="/communication", tags=["stock-alert-settings"])

MODES = ("INHERIT", "OVERRIDE", "DISABLED")
STATUSES = ("CONFIGURED", "INHERITED", "DISABLED", "NOT_CONFIGURED", "INVALID", "GLOBAL_OFF", "INACTIVE")
CSV_COLUMNS = ["product_code", "product_name", "category", "mode", "low_threshold",
               "critical_threshold", "recovery_threshold", "note"]
# Movements that are consumption (trolley loads out, returns back in).
CONSUMPTION_REASONS = ("OBS_LOAD", "OBS_RETURN")
MIN_HISTORY_DAYS = 14
MIN_LOADS = 3


class ItemSettingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(pattern="^(INHERIT|OVERRIDE|DISABLED)$")
    low_threshold: float | None = Field(default=None, ge=0)
    critical_threshold: float | None = Field(default=None, ge=0)
    recovery_threshold: float | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)
    # Optimistic locking: the version the editor loaded (None = no row yet).
    expected_version: int | None = None


class BulkIn(ItemSettingIn):
    product_ids: list[int] = Field(min_length=1, max_length=1000)
    dry_run: bool = True


class SimulateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quantity: float = Field(ge=0, le=10_000_000)


class ItemAlertIn(BaseModel):
    """One-screen alert set-up for a single item (creates or updates its own rule)."""
    model_config = ConfigDict(extra="forbid")
    low_threshold: float = Field(ge=0)
    critical_threshold: float | None = Field(default=None, ge=0)
    recovery_threshold: float | None = Field(default=None, ge=0)
    comparison: str = Field(default="BELOW", pattern="^(BELOW|AT_OR_BELOW)$")
    low_recipient_user_ids: list[int] = []
    critical_recipient_user_ids: list[int] = []
    channel_email: bool = True
    channel_in_app: bool = True
    frequency: str = Field(default="ONCE", pattern="^(ONCE|REPEAT_HOURS|DAILY|DAILY_DIGEST|WEEKLY_DIGEST)$")
    repeat_hours: int | None = Field(default=None, ge=1, le=168)
    notify_on_recovery: bool = False
    escalation_policy_id: int | None = None
    owner_user_id: int | None = None
    active: bool = True


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv: str = Field(min_length=1, max_length=2_000_000)
    dry_run: bool = True


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _whole(value, label):
    """Warehouse stock is counted in whole units, so thresholds are too."""
    if value is None:
        return None
    if not float(value).is_integer():
        raise HTTPException(status_code=400, detail=f"{label} must be a whole number (stock is counted in whole units).")
    return float(value)


def _clean_setting(data: ItemSettingIn) -> dict:
    """Validate one item setting on its own (rule-dependent checks come later)."""
    low = _whole(data.low_threshold, "Alert threshold")
    critical = _whole(data.critical_threshold, "Critical threshold")
    recovery = _whole(data.recovery_threshold, "Recovery threshold")
    if data.mode != "OVERRIDE":
        if any(v is not None for v in (low, critical, recovery)):
            raise HTTPException(status_code=400, detail="Thresholds can only be set when the item overrides its rules.")
    else:
        if all(v is None for v in (low, critical, recovery)):
            raise HTTPException(status_code=400,
                                detail="Set at least one threshold to override, or choose 'Inherit'.")
        if low is not None and critical is not None and critical >= low:
            raise HTTPException(status_code=400, detail="The critical threshold must be lower than the alert threshold.")
        if low is not None and recovery is not None and recovery <= low:
            raise HTTPException(status_code=400,
                                detail="The recovery level must be above the alert threshold, or stock would "
                                       "count as recovered while still low.")
    return {"mode": data.mode, "low_threshold": low, "critical_threshold": critical,
            "recovery_threshold": recovery, "note": (data.note or "").strip() or None}


def _check_against_rules(connection, product: dict, clean: dict, settings, rules) -> list[str]:
    """Problems this setting would cause with the rules that reach the item."""
    if clean["mode"] != "OVERRIDE":
        return []
    trial = dict(product)
    trial["alert_override"] = clean
    trial["alert_setting"] = {**clean, "product_id": product["id"]}
    problems = []
    for rule, _how, suppressed in alert_engine.effective_rules(rules, trial):
        if suppressed or rule["trigger_type"] == "OUT_OF_STOCK":
            continue
        detail = alert_engine.threshold_details(rule, trial)
        if detail is None:
            continue
        for warning in detail["warnings"]:
            problems.append(f"Rule '{rule['name']}': {warning}")
        if clean["recovery_threshold"] is not None and clean["recovery_threshold"] <= detail["low"]:
            problems.append(f"Rule '{rule['name']}': recovery level {clean['recovery_threshold']:g} is not "
                            f"above the alert threshold {detail['low']:g}.")
    return problems


def _products(connection, ids=None) -> list[dict]:
    query = """SELECT p.id, p.product_code, p.product_name, p.category, p.unit, p.reorder_level,
                      p.active, COALESCE(m.quantity, 0) AS quantity
               FROM products p LEFT JOIN main_inventory m ON m.product_id = p.id"""
    params: tuple = ()
    if ids is not None:
        if not ids:
            return []
        query += f" WHERE p.id IN ({','.join('?' * len(ids))})"
        params = tuple(ids)
    rows = [dict(r) for r in connection.execute(query + " ORDER BY p.category, p.product_name", params)]
    settings = {r["product_id"]: dict(r) for r in connection.execute("SELECT * FROM stock_alert_item_settings")}
    for product in rows:
        setting = settings.get(product["id"])
        product["alert_setting"] = setting
        product["alert_override"] = setting if setting and setting["mode"] == "OVERRIDE" else None
    return rows


def _product_or_404(connection, product_id):
    rows = _products(connection, [product_id])
    if not rows:
        raise HTTPException(status_code=404, detail="Item not found.")
    return rows[0]


def _incident_summary(connection) -> tuple[dict, dict]:
    last = {r["product_id"]: r["last"] for r in connection.execute(
        "SELECT product_id, MAX(opened_at) AS last FROM alert_incidents GROUP BY product_id")}
    worst: dict[int, str] = {}
    marks = ",".join("?" * len(ACTIVE_STATUSES))
    for r in connection.execute(
            f"SELECT product_id, severity FROM alert_incidents WHERE status IN ({marks})", ACTIVE_STATUSES):
        if SEVERITY_RANK[r["severity"]] > SEVERITY_RANK.get(worst.get(r["product_id"]), 0):
            worst[r["product_id"]] = r["severity"]
    return last, worst


def _row(product, config, last, worst) -> dict:
    qty = float(product["quantity"])
    low = config["low"]
    below = config["status"] in ("CONFIGURED", "INHERITED") and any(
        e["applies"] and e["low"] is not None and (qty <= e["low"] if e["comparison"] == "<=" else qty < e["low"])
        for e in config["rules"])
    silent = config["status"] in ("DISABLED", "INACTIVE")
    if silent:  # nothing applies, so show no thresholds rather than the rules' unused ones
        low = None
        config = {**config, "critical": None, "recovery": None,
                  "source": "Item disabled" if config["status"] == "DISABLED" else "Item inactive"}
    return {
        "product_id": product["id"], "product_code": product["product_code"],
        "product_name": product["product_name"], "category": product["category"],
        "unit": product["unit"], "active": bool(product["active"]), "quantity": product["quantity"],
        "reorder_level": product["reorder_level"],
        "status": config["status"], "mode": config["mode"], "source": config["source"],
        "low": low, "critical": config["critical"], "recovery": config["recovery"],
        "low_is_override": bool(product["alert_override"] and product["alert_override"]["low_threshold"] is not None),
        "critical_is_override": bool(product["alert_override"]
                                     and product["alert_override"]["critical_threshold"] is not None),
        "recovery_is_override": bool(product["alert_override"]
                                     and product["alert_override"]["recovery_threshold"] is not None),
        "rule_count": sum(1 for e in config["rules"] if e["applies"]),
        "below_threshold": bool(below),
        "open_severity": worst.get(product["id"]),
        "last_alert_at": last.get(product["id"]),
        "setting_version": product["alert_setting"]["version"] if product["alert_setting"] else None,
    }


def _all_rows(connection):
    settings = load_settings(connection)
    now = utc_now()
    rules = alert_engine.active_rules(connection, now, settings)
    last, worst = _incident_summary(connection)
    rows = []
    for product in _products(connection):
        config = alert_engine.effective_configuration(connection, product, settings, rules, now)
        rows.append(_row(product, config, last, worst))
    return rows


def _write_setting(connection, product_id, clean, user, now):
    """Insert/update/delete the item row and queue a re-evaluation (same transaction)."""
    if clean["mode"] == "INHERIT":
        connection.execute("DELETE FROM stock_alert_item_settings WHERE product_id=?", (product_id,))
    else:
        connection.execute(
            """INSERT INTO stock_alert_item_settings
               (product_id,mode,low_threshold,critical_threshold,recovery_threshold,note,version,updated_by,updated_at)
               VALUES (?,?,?,?,?,?,1,?,?)
               ON CONFLICT(product_id) DO UPDATE SET mode=excluded.mode, low_threshold=excluded.low_threshold,
                 critical_threshold=excluded.critical_threshold, recovery_threshold=excluded.recovery_threshold,
                 note=excluded.note, version=stock_alert_item_settings.version+1,
                 updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
            (product_id, clean["mode"], clean["low_threshold"], clean["critical_threshold"],
             clean["recovery_threshold"], clean["note"], user["employee_id"], now))
    alert_engine.enqueue_stock_event(connection, product_id, "ALERT_SETTINGS", f"by {user['employee_id']}")


def _evaluate_now(product_ids):
    """Apply a settings change at once instead of waiting for the worker."""
    connection = get_connection()
    try:
        settings = load_settings(connection)
        now = utc_now()
        rules = alert_engine.active_rules(connection, now, settings)
        for product_id in product_ids:
            with alert_engine.immediate(connection):
                alert_engine.evaluate_product(connection, product_id, now, settings, rules)
    finally:
        connection.close()


def _before(setting):
    if not setting:
        return {"mode": "INHERIT"}
    return {k: setting.get(k) for k in ("mode", "low_threshold", "critical_threshold", "recovery_threshold", "note")}


# --------------------------------------------------------------------------- #
# Item table and coverage
# --------------------------------------------------------------------------- #
@router.get("/items")
def list_items(q: str | None = Query(None, max_length=100), category: str | None = None,
               status: str | None = Query(None, pattern="^(" + "|".join(STATUSES) + ")$"),
               mode: str | None = Query(None, pattern="^(INHERIT|OVERRIDE|DISABLED)$"),
               below: bool = False, include_inactive: bool = False,
               page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=MAX_PAGE_SIZE),
               user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        rows = _all_rows(connection)
    finally:
        connection.close()
    needle = (q or "").strip().lower()
    rows = [r for r in rows
            if (include_inactive or r["active"])
            and (not needle or needle in r["product_name"].lower() or needle in r["product_code"].lower())
            and (not category or r["category"] == category)
            and (not status or r["status"] == status)
            and (not mode or r["mode"] == mode)
            and (not below or r["below_threshold"])]
    total = len(rows)
    pages = max(1, -(-total // page_size))
    page = min(page, pages)
    start = (page - 1) * page_size
    return {"items": rows[start:start + page_size], "total": total, "page": page, "page_size": page_size,
            "pages": pages, "has_prev": page > 1, "has_next": page < pages}


@router.get("/coverage")
def coverage(user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        rows = [r for r in _all_rows(connection) if r["active"]]
    finally:
        connection.close()
    counts = {s: 0 for s in STATUSES}
    for r in rows:
        counts[r["status"]] += 1
    gaps = [{k: r[k] for k in ("product_id", "product_code", "product_name", "category", "status", "quantity")}
            for r in rows if r["status"] in ("NOT_CONFIGURED", "INVALID")]
    return {
        "total_items": len(rows),
        "explicitly_configured": counts["CONFIGURED"],
        "inheriting": counts["INHERITED"],
        "excluded": counts["DISABLED"],
        "missing": counts["NOT_CONFIGURED"],
        "invalid": counts["INVALID"],
        "global_off": counts["GLOBAL_OFF"] > 0,
        "below_threshold": sum(1 for r in rows if r["below_threshold"]),
        "gaps": gaps[:200],
    }


# --------------------------------------------------------------------------- #
# One item
# --------------------------------------------------------------------------- #
def _suggestion(connection, product, safety_days: float) -> dict:
    """Usage-based threshold suggestion. Never applied automatically."""
    now = utc_now()
    since = iso(now - timedelta(days=90))
    rows = connection.execute(
        f"""SELECT change_qty, created_at FROM main_inventory_txn
            WHERE product_id=? AND reason IN ({','.join('?' * len(CONSUMPTION_REASONS))}) AND created_at >= ?
            ORDER BY created_at""", (product["id"], *CONSUMPTION_REASONS, since)).fetchall()
    loads = [r for r in rows if r["change_qty"] < 0]
    result = {"available": False, "safety_days": safety_days, "basis": "Trolley loads minus returns, last 90 days."}
    if len(loads) < MIN_LOADS:
        result["reason"] = f"Not enough usage history ({len(loads)} trolley loads; at least {MIN_LOADS} needed)."
        return result
    first = parse_iso(rows[0]["created_at"])
    days = max((now - first).total_seconds() / 86400, 1.0)
    if days < MIN_HISTORY_DAYS:
        result["reason"] = f"Usage history covers {days:.0f} days; at least {MIN_HISTORY_DAYS} needed."
        return result
    used = -sum(r["change_qty"] for r in rows)
    if used <= 0:
        result["reason"] = "No net consumption in the period."
        return result
    daily = used / days
    qty = float(product["quantity"])
    result.update(available=True, average_daily_usage=round(daily, 2), history_days=round(days),
                  days_of_cover=round(qty / daily, 1))
    leads = []
    for r in connection.execute(
            """SELECT o.order_placement_date AS placed, COALESCE(o.delivery_date, o.received_date) AS arrived
               FROM purchase_orders o JOIN purchase_order_items i ON i.order_id = o.id
               WHERE i.product_id=? AND o.order_placement_date IS NOT NULL
                 AND COALESCE(o.delivery_date, o.received_date) IS NOT NULL""", (product["id"],)):
        try:
            placed, arrived = parse_iso(r["placed"][:10] + "T00:00:00Z"), parse_iso(r["arrived"][:10] + "T00:00:00Z")
        except (TypeError, ValueError):
            continue
        if arrived >= placed:
            leads.append((arrived - placed).days)
    if not leads:
        result["lead_time_days"] = None
        result["note"] = ("No received purchase orders with dates for this item, so supplier lead time is "
                          "unknown and no reorder point is suggested. Days of cover is still shown.")
        return result
    lead = sum(leads) / len(leads)
    lead_demand = daily * lead
    result.update(lead_time_days=round(lead, 1), purchase_orders=len(leads),
                  suggested_low=math.ceil(lead_demand + daily * safety_days),
                  suggested_critical=(math.ceil(lead_demand) if safety_days > 0 else None),
                  formula="Alert threshold = average daily usage x (lead time + safety days). "
                          "Critical = usage during the lead time alone.")
    return result


@router.get("/items/{product_id}")
def get_item(product_id: int, safety_days: float = Query(7, ge=0, le=180),
             user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        product = _product_or_404(connection, product_id)
        config = alert_engine.effective_configuration(connection, product)
        last, worst = _incident_summary(connection)
        incidents = [dict(r) for r in connection.execute(
            """SELECT i.id, i.status, i.severity, i.opened_at, i.resolved_at, i.last_quantity,
                      i.threshold_at_open, i.rule_version, r.name AS rule_name
               FROM alert_incidents i JOIN comm_rules r ON r.id = i.rule_id
               WHERE i.product_id=? ORDER BY i.id DESC LIMIT 20""", (product_id,))]
        return {"item": _row(product, config, last, worst), "configuration": config,
                "item_rule": _item_rule_view(_item_rule(connection, product_id)),
                "can_edit_rule": "comms_rules" in _perms(user),
                "suggestion": _suggestion(connection, product, safety_days), "incidents": incidents,
                "can_edit": "comms_item_settings" in _perms(user)}
    finally:
        connection.close()


def _perms(user):
    from security import permissions_for
    return permissions_for(user["role"])


@router.put("/items/{product_id}/alert-settings")
def put_item_setting(product_id: int, data: ItemSettingIn, user: dict = Depends(requires("comms_item_settings"))):
    clean = _clean_setting(data)
    connection = get_connection()
    try:
        product = _product_or_404(connection, product_id)
        settings = load_settings(connection)
        rules = alert_engine.active_rules(connection, utc_now(), settings)
        problems = _check_against_rules(connection, product, clean, settings, rules)
        if problems:
            raise HTTPException(status_code=400, detail=" ".join(problems))
        current = product["alert_setting"]
        current_version = current["version"] if current else None
        if data.expected_version != current_version:
            raise HTTPException(status_code=409,
                                detail="Someone else changed this item's alert settings. Reload and try again.")
        with alert_engine.immediate(connection):
            _write_setting(connection, product_id, clean, user, iso(utc_now()))
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "ITEM_ALERT_SETTINGS",
               f"Stock alert settings for {product['product_code']}: {clean['mode'].lower()}.",
               "product", product_id, metadata={"before": _before(current), "after": clean})
    _evaluate_now([product_id])
    return get_item(product_id, 7, user)


# --------------------------------------------------------------------------- #
# The item's own rule (quick set-up from the item page)
# --------------------------------------------------------------------------- #
def _item_rule(connection, product_id):
    """A rule whose only scope is this one item: the item's own alert."""
    for row in connection.execute(
            """SELECT r.id FROM comm_rules r WHERE r.scope_all=0 AND r.status IN ('ACTIVE','DRAFT','DISABLED')
                 AND EXISTS (SELECT 1 FROM comm_rule_scopes s WHERE s.rule_id=r.id AND s.scope_type='PRODUCT'
                             AND s.scope_value=? AND s.include=1)
                 AND (SELECT COUNT(*) FROM comm_rule_scopes s WHERE s.rule_id=r.id) = 1
               ORDER BY r.status='ACTIVE' DESC, r.id DESC""", (str(product_id),)):
        return alert_engine.load_rule(connection, row["id"])
    return None


def _item_rule_view(rule):
    if not rule:
        return None
    low = rule.get("threshold")
    users = lambda level: [int(r["recipient_value"]) for r in rule["recipients"]  # noqa: E731
                           if r["recipient_type"] == "USER" and r["level"] in (level, "ALL")]
    return {
        "rule_id": rule["id"], "name": rule["name"], "status": rule["status"], "version": rule["version"],
        "low_threshold": low, "critical_threshold": rule.get("critical_threshold"),
        "recovery_threshold": (low + float(rule.get("recovery_margin") or 0)
                               + (1 if rule["trigger_type"] == "AT_OR_BELOW" else 0)) if low is not None else None,
        "comparison": rule["trigger_type"] if rule["trigger_type"] in ("BELOW", "AT_OR_BELOW") else "BELOW",
        "low_recipient_user_ids": users("LOW"), "critical_recipient_user_ids": users("CRITICAL"),
        "other_recipients": [r for r in rule["recipients"] if r["recipient_type"] != "USER"],
        "channel_email": bool(rule["channel_email"]), "channel_in_app": bool(rule["channel_in_app"]),
        "frequency": rule["frequency"], "repeat_hours": rule.get("repeat_hours"),
        "notify_on_recovery": bool(rule["notify_on_recovery"]),
        "escalation_policy_id": rule.get("escalation_policy_id"), "owner_user_id": rule["owner_user_id"],
        "editable_here": rule["trigger_type"] in ("BELOW", "AT_OR_BELOW") and rule["threshold_source"] == "FIXED",
    }


@router.put("/items/{product_id}/alert-rule")
def put_item_rule(product_id: int, data: ItemAlertIn, user: dict = Depends(requires("comms_rules"))):
    """Create or update the item's own alert rule from the item page.

    Low-stock recipients get low alerts, critical recipients get critical and
    out-of-stock alerts; someone in both lists gets everything. Everything else
    (validation, ownership, versions, re-evaluation) is the normal rule path.
    """
    from comms_core import user_can_receive
    from routers.communication import RecipientIn, RuleIn, create_rule, update_rule

    low = _whole(data.low_threshold, "Alert threshold")
    critical = _whole(data.critical_threshold, "Critical threshold")
    recovery = _whole(data.recovery_threshold, "Recovery level")
    if critical is not None and critical >= low:
        raise HTTPException(status_code=400, detail="The critical threshold must be lower than the alert threshold.")
    if recovery is not None and recovery <= low:
        raise HTTPException(status_code=400, detail="The recovery level must be above the alert threshold.")
    low_ids, crit_ids = list(dict.fromkeys(data.low_recipient_user_ids)), list(dict.fromkeys(data.critical_recipient_user_ids))
    if not low_ids and not crit_ids:
        raise HTTPException(status_code=400, detail="Choose at least one person to notify.")
    if crit_ids and critical is None:
        raise HTTPException(status_code=400, detail="Set a critical threshold, or leave the critical recipients empty.")
    if data.frequency == "REPEAT_HOURS" and not data.repeat_hours:
        raise HTTPException(status_code=400, detail="Enter how many hours between reminders.")
    connection = get_connection()
    try:
        product = _product_or_404(connection, product_id)
        existing = _item_rule(connection, product_id)
        for uid in dict.fromkeys(low_ids + crit_ids):
            row = connection.execute("SELECT name, role, active FROM users WHERE id=?", (uid,)).fetchone()
            if not row or not row["active"]:
                raise HTTPException(status_code=400, detail=f"User {uid} is not an active user.")
            if not user_can_receive(row["role"]):
                raise HTTPException(status_code=400,
                                    detail=f"{row['name']} cannot view warehouse stock, so cannot receive stock alerts.")
        owner = data.owner_user_id or (existing["owner_user_id"] if existing else None)
        if owner is None:
            if user_can_receive(user["role"]):
                owner = user["id"]
            else:  # e.g. an administrator, who cannot see stock: the first recipient owns it
                owner = (low_ids or crit_ids)[0]
    finally:
        connection.close()
    recipients = ([RecipientIn(recipient_type="USER", recipient_value=str(u),
                               level="ALL" if u in crit_ids else "LOW") for u in low_ids]
                  + [RecipientIn(recipient_type="USER", recipient_value=str(u), level="CRITICAL")
                     for u in crit_ids if u not in low_ids])
    if existing:  # keep any role/group/email recipients added on the Alert rules screen
        recipients += [RecipientIn(**{k: r[k] for k in ("recipient_type", "recipient_value", "level")})
                       for r in existing["recipients"] if r["recipient_type"] != "USER"]
    body = RuleIn(
        name=(existing["name"] if existing else f"Stock alert: {product['product_name']} ({product['product_code']})")[:120],
        description=(existing.get("description") if existing else "Set up from Stock alert settings."),
        owner_user_id=owner, products=[product_id], exclusive=True, trigger_type=data.comparison,
        threshold=low, critical_threshold=critical,
        # "Resolved when stock reaches X" is stock >= X for either comparison.
        recovery_margin=(recovery - low - (1 if data.comparison == "AT_OR_BELOW" else 0)) if recovery is not None else 0,
        channel_email=data.channel_email, channel_in_app=data.channel_in_app, frequency=data.frequency,
        repeat_hours=data.repeat_hours if data.frequency == "REPEAT_HOURS" else None,
        notify_on_critical=True, notify_on_recovery=data.notify_on_recovery,
        escalation_policy_id=data.escalation_policy_id, recipients=recipients, activate=data.active,
    )
    if existing:
        if not data.active and existing["status"] == "ACTIVE":
            from routers.communication import disable_rule
            update_rule(existing["id"], body.model_copy(update={"activate": False}), user)
            disable_rule(existing["id"], user)
        else:
            update_rule(existing["id"], body, user)
    else:
        create_rule(body, user)
    return get_item(product_id, 7, user)


@router.post("/items/{product_id}/simulate")
def simulate_item(product_id: int, data: SimulateIn, user: dict = Depends(requires("comms_view"))):
    _whole(data.quantity, "Quantity")
    connection = get_connection()
    try:
        _product_or_404(connection, product_id)
        return alert_engine.simulate_item(connection, product_id, data.quantity)
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# Bulk, export, import
# --------------------------------------------------------------------------- #
@router.post("/items/bulk")
def bulk_update(data: BulkIn, user: dict = Depends(requires("comms_item_settings"))):
    clean = _clean_setting(data)
    ids = list(dict.fromkeys(data.product_ids))
    connection = get_connection()
    try:
        products = {p["id"]: p for p in _products(connection, ids)}
        missing = [i for i in ids if i not in products]
        if missing:
            raise HTTPException(status_code=400, detail=f"Unknown item id(s): {', '.join(map(str, missing))}.")
        settings = load_settings(connection)
        now = utc_now()
        rules = alert_engine.active_rules(connection, now, settings)
        preview, errors = [], []
        for pid in ids:
            product = products[pid]
            before = alert_engine.effective_configuration(connection, dict(product), settings, rules, now)
            trial = dict(product)
            trial["alert_setting"] = None if clean["mode"] == "INHERIT" else {**clean, "product_id": pid,
                                                                             "version": 0}
            trial["alert_override"] = trial["alert_setting"] if clean["mode"] == "OVERRIDE" else None
            after = alert_engine.effective_configuration(connection, trial, settings, rules, now)
            problems = _check_against_rules(connection, product, clean, settings, rules)
            if problems:
                errors.append({"product_code": product["product_code"], "problems": problems})
            preview.append({"product_id": pid, "product_code": product["product_code"],
                            "product_name": product["product_name"], "quantity": product["quantity"],
                            "before": {k: before[k] for k in ("status", "low", "critical", "recovery", "mode")},
                            "after": {k: after[k] for k in ("status", "low", "critical", "recovery", "mode")}})
        result = {"dry_run": data.dry_run, "count": len(ids), "items": preview, "errors": errors}
        if data.dry_run or errors:
            result["applied"] = False
            return result
        stamp = iso(now)
        with alert_engine.immediate(connection):
            for pid in ids:
                _write_setting(connection, pid, clean, user, stamp)
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "BULK_ITEM_ALERT_SETTINGS",
               f"Bulk stock alert settings ({clean['mode'].lower()}) for {len(ids)} items.",
               "product", None, metadata={"product_ids": ids, "setting": clean})
    _evaluate_now(ids)
    result["applied"] = True
    return result


@router.get("/items-export")
def export_items(user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        products = _products(connection)
    finally:
        connection.close()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for p in products:
        s = p["alert_setting"] or {}
        writer.writerow([p["product_code"], p["product_name"], p["category"], s.get("mode") or "INHERIT",
                         _fmt(s.get("low_threshold")), _fmt(s.get("critical_threshold")),
                         _fmt(s.get("recovery_threshold")), s.get("note") or ""])
    log_action(user, "COMMUNICATION", "EXPORT_ITEM_ALERT_SETTINGS", "Exported stock alert item settings.",
               "product", None)
    return Response(content=buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="stock-alert-item-settings.csv"'})


def _fmt(value):
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _num(text, label, line):
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"line {line}: {label} '{text}' is not a number")
    if value < 0 or not value.is_integer():
        raise ValueError(f"line {line}: {label} must be a whole number of 0 or more")
    return value


@router.post("/items-import")
def import_items(data: ImportIn, user: dict = Depends(requires("comms_item_settings"))):
    """CSV with the export's columns. Dry run first; applying is all-or-nothing."""
    reader = csv.DictReader(io.StringIO(data.csv.lstrip("﻿")))
    if not reader.fieldnames or "product_code" not in [f.strip() for f in reader.fieldnames] \
            or "mode" not in [f.strip() for f in reader.fieldnames]:
        raise HTTPException(status_code=400, detail="The file needs at least the columns product_code and mode. "
                                                    "Download the export to get the template.")
    connection = get_connection()
    try:
        by_code = {p["product_code"].upper(): p for p in _products(connection)}
        settings = load_settings(connection)
        now = utc_now()
        rules = alert_engine.active_rules(connection, now, settings)
        errors, changes, seen = [], [], {}
        for index, raw in enumerate(reader, start=2):
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            code = row.get("product_code", "").upper()
            if not code:
                continue
            if code in seen:
                errors.append(f"line {index}: {code} already appears on line {seen[code]}")
                continue
            seen[code] = index
            product = by_code.get(code)
            if not product:
                errors.append(f"line {index}: unknown product code {code}")
                continue
            mode = (row.get("mode") or "INHERIT").upper()
            try:
                if mode not in MODES:
                    raise ValueError(f"line {index}: mode must be INHERIT, OVERRIDE or DISABLED")
                item = ItemSettingIn(mode=mode, low_threshold=_num(row.get("low_threshold"), "low_threshold", index),
                                     critical_threshold=_num(row.get("critical_threshold"), "critical_threshold", index),
                                     recovery_threshold=_num(row.get("recovery_threshold"), "recovery_threshold", index),
                                     note=row.get("note") or None)
                clean = _clean_setting(item)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            except HTTPException as exc:
                errors.append(f"line {index}: {exc.detail}")
                continue
            problems = _check_against_rules(connection, product, clean, settings, rules)
            if problems:
                errors.append(f"line {index}: {' '.join(problems)}")
                continue
            if _before(product["alert_setting"]) == ({"mode": "INHERIT"} if mode == "INHERIT" else
                                                     {k: clean[k] for k in ("mode", "low_threshold",
                                                                           "critical_threshold",
                                                                           "recovery_threshold", "note")}):
                continue  # unchanged
            changes.append({"product_id": product["id"], "product_code": product["product_code"],
                            "product_name": product["product_name"], "before": _before(product["alert_setting"]),
                            "after": clean})
        result = {"dry_run": data.dry_run, "changes": changes, "errors": errors, "applied": False}
        if data.dry_run or errors or not changes:
            return result
        stamp = iso(now)
        with alert_engine.immediate(connection):
            for change in changes:
                _write_setting(connection, change["product_id"], change["after"], user, stamp)
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "IMPORT_ITEM_ALERT_SETTINGS",
               f"Imported stock alert settings for {len(changes)} items.", "product", None,
               metadata={"changes": changes})
    _evaluate_now([c["product_id"] for c in changes])
    result["applied"] = True
    return result
