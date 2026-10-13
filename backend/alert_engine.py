"""Stock-alert evaluation: rules -> incidents -> queued notifications.

How it fits together
--------------------
* Every warehouse movement goes through routers/warehouse.move_stock, which
  writes a row to stock_change_outbox inside the same transaction. If the
  movement rolls back, so does the event; if it commits, the event is there.
* The worker (comms_worker.py) drains the outbox and calls evaluate_product().
  A periodic sweep re-evaluates everything in scope, which catches anything
  missed while the worker was down and drives duration conditions, reminders,
  escalation and digests.
* Evaluation never sends email. It writes incidents, events, in-app
  notifications and notification_jobs rows; the mailer delivers those later.
* The only stock figure is warehouse on-hand (main_inventory.quantity). The
  per-flight trolley stock is a different thing and is never evaluated here.

Products with no matching ACTIVE rule never produce outbound communication,
whatever the warehouse dashboard shows as low stock.

Precedence (Stock Alert Settings)
---------------------------------
1. The master switch (app_settings.alerts_enabled). Off: nothing is evaluated.
2. Item setting DISABLED: no stock alerts for that item from any rule.
3. Item setting OVERRIDE: each value it sets (alert / critical / recovery
   threshold) replaces that value in every rule reaching the item. Values it
   leaves empty come from the rule. Recipients, channels, frequency and
   escalation always come from the rule.
4. Rules: an `exclusive` rule naming the item explicitly takes over from rules
   that reach it only through a category or "every product". Otherwise every
   matching rule evaluates on its own (each with its own incident).
There is one stock location (the main warehouse), so there is no warehouse or
department level.
"""
import json

from contextlib import contextmanager
from datetime import timedelta
import os
import sqlite3

from comms_core import (
    ACTIVE_STATUSES, SEVERITY_RANK, format_qty, iso, load_settings, local_date,
    local_display, next_business_start, is_business_time, parse_iso,
    render_email, resolve_recipients, utc_now,
)

WAREHOUSE_NAME = "Main warehouse"
DIGEST_FREQUENCIES = ("DAILY_DIGEST", "WEEKLY_DIGEST")
SEVERITY_TEMPLATE = {"LOW": "low_stock", "CRITICAL": "critical_stock", "OUT": "out_of_stock"}
KIND_TEMPLATE = {"REMINDER": "reminder", "ESCALATION": "escalation", "RECOVERY": "recovery"}


@contextmanager
def immediate(connection):
    """Run a block under BEGIN IMMEDIATE so concurrent workers serialise."""
    previous = connection.isolation_level
    connection.isolation_level = None
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.isolation_level = previous


def enqueue_stock_event(connection, product_id: int, source: str, reference: str | None = None):
    """Record that a product's stock or alert inputs changed. Call inside the
    same transaction as the change itself."""
    connection.execute(
        "INSERT INTO stock_change_outbox(product_id,source,reference,created_at) VALUES (?,?,?,?)",
        (product_id, source, reference, iso(utc_now())),
    )


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
def load_rule(connection, rule_id: int) -> dict | None:
    row = connection.execute("SELECT * FROM comm_rules WHERE id=?", (rule_id,)).fetchone()
    if not row:
        return None
    return _hydrate(connection, dict(row))


def _hydrate(connection, rule: dict) -> dict:
    rule["scopes"] = [dict(r) for r in connection.execute(
        "SELECT scope_type, scope_value, include FROM comm_rule_scopes WHERE rule_id=? ORDER BY id",
        (rule["id"],)).fetchall()]
    rule["recipients"] = [dict(r) for r in connection.execute(
        "SELECT recipient_type, recipient_value, level FROM comm_rule_recipients WHERE rule_id=? ORDER BY id",
        (rule["id"],)).fetchall()]
    return rule


def active_rules(connection, now, settings) -> list[dict]:
    today = local_date(now, settings)
    rows = connection.execute(
        """SELECT * FROM comm_rules WHERE status='ACTIVE'
             AND (effective_from IS NULL OR effective_from <= ?)
             AND (effective_to IS NULL OR effective_to >= ?)
           ORDER BY priority, id""", (today, today)).fetchall()
    return [_hydrate(connection, dict(r)) for r in rows]


def match_kind(rule: dict, product: dict) -> str | None:
    """How a rule reaches a product: PRODUCT, CATEGORY, ALL, or None."""
    pid, category = str(product["id"]), product["category"]
    scopes = rule.get("scopes") or []
    for s in scopes:
        if not s["include"] and (
            (s["scope_type"] == "PRODUCT" and s["scope_value"] == pid)
            or (s["scope_type"] == "CATEGORY" and s["scope_value"] == category)
        ):
            return None
    for s in scopes:
        if s["include"] and s["scope_type"] == "PRODUCT" and s["scope_value"] == pid:
            return "PRODUCT"
    for s in scopes:
        if s["include"] and s["scope_type"] == "CATEGORY" and s["scope_value"] == category:
            return "CATEGORY"
    if rule.get("scope_all"):
        return "ALL"
    return None


def effective_rules(rules: list[dict], product: dict) -> list[tuple[dict, str, int | None]]:
    """Rules that evaluate this product, as (rule, how, suppressed_by_rule_id).

    Overlapping rules all evaluate independently, except that an `exclusive`
    rule naming the product explicitly takes over from rules that only reach it
    through a category or "every product". Two explicit rules both run.
    """
    matched = [(r, match_kind(r, product)) for r in rules]
    matched = [(r, how) for r, how in matched if how]
    exclusive = [r for r, how in matched if how == "PRODUCT" and r.get("exclusive")]
    owner = exclusive[0]["id"] if exclusive else None
    return [(r, how, owner if (owner and how != "PRODUCT") else None) for r, how in matched]


def threshold_details(rule: dict, product: dict) -> dict | None:
    """Effective thresholds for one rule and item, with where each value came from.

    Returns {"low", "critical", "op", "recovery", "sources": {...}, "warnings": [...]}
    or None when the rule cannot be applied to this item (no threshold).
    """
    trigger = rule["trigger_type"]
    reorder = product.get("reorder_level")
    override = product.get("alert_override") or {}
    sources, warnings = {}, []
    if trigger == "OUT_OF_STOCK":
        return {"low": 0.0, "critical": None, "op": "<=", "recovery": None,
                "sources": {"low": "RULE_OUT_OF_STOCK"}, "warnings": warnings}
    if trigger == "PERCENT_OF_REORDER":
        if reorder is None or rule.get("percent") is None:
            low = None
        else:
            low = float(reorder) * float(rule["percent"]) / 100.0
            sources["low"] = "RULE_PERCENT_OF_REORDER"
        op = "<"
    else:
        if rule.get("threshold_source") == "REORDER_LEVEL":
            low = reorder
            sources["low"] = "ITEM_REORDER_LEVEL"
        else:
            low = rule.get("threshold")
            sources["low"] = "RULE"
        op = "<=" if trigger == "AT_OR_BELOW" else "<"
    if override.get("low_threshold") is not None:
        low, sources["low"] = override["low_threshold"], "ITEM_OVERRIDE"
    if low is None:
        return None
    critical = _crit(rule)
    if critical is not None:
        sources["critical"] = "RULE"
    if override.get("critical_threshold") is not None:
        critical, sources["critical"] = float(override["critical_threshold"]), "ITEM_OVERRIDE"
    if critical is not None and critical >= float(low):
        warnings.append(f"Critical threshold {format_qty(critical)} is not below the alert threshold "
                        f"{format_qty(low)}, so it is ignored.")
        critical = None
        sources.pop("critical", None)
    recovery = None
    if override.get("recovery_threshold") is not None:
        recovery, sources["recovery"] = float(override["recovery_threshold"]), "ITEM_OVERRIDE"
    else:
        margin = float(rule.get("recovery_margin") or 0)
        # Shown as "recovers when stock reaches": with "at or below" stock must
        # rise past low + margin, i.e. reach it plus one whole unit.
        recovery = float(low) + margin + (1 if op == "<=" else 0)
        sources["recovery"] = "RULE_MARGIN"
    return {"low": float(low), "critical": critical, "op": op, "recovery": recovery,
            "sources": sources, "warnings": warnings}


def thresholds(rule: dict, product: dict):
    """Return (low, critical, comparator) or None when the rule cannot be applied."""
    detail = threshold_details(rule, product)
    if detail is None:
        return None
    return detail["low"], detail["critical"], detail["op"]


def _crit(rule):
    value = rule.get("critical_threshold")
    return None if value is None else float(value)


def _cmp(qty, limit, op):
    return qty <= limit if op == "<=" else qty < limit


def classify(qty: float, rule: dict, product: dict):
    """Return (breached, severity, low, critical, op)."""
    limits = thresholds(rule, product)
    if limits is None:
        return False, None, None, None, None
    low, critical, op = limits
    if not _cmp(qty, low, op):
        return False, None, low, critical, op
    if qty <= 0:
        severity = "OUT"
    elif critical is not None and _cmp(qty, critical, op):
        severity = "CRITICAL"
    else:
        severity = "LOW"
    return True, severity, low, critical, op


def recovered(qty: float, low: float, op: str, margin: float, recovery_threshold=None) -> bool:
    """Hysteresis: stock must clear the threshold by `margin` to resolve, or
    reach the item's own recovery threshold when one is set."""
    if recovery_threshold is not None:
        # Recovery can never happen while the alert condition itself still holds.
        return qty >= float(recovery_threshold) and not _cmp(qty, low, op)
    margin = float(margin or 0)
    return qty > low + margin if op == "<=" else qty >= low + margin


# --------------------------------------------------------------------------- #
# Item alert settings
# --------------------------------------------------------------------------- #
def load_item_setting(connection, product_id: int) -> dict | None:
    row = connection.execute("SELECT * FROM stock_alert_item_settings WHERE product_id=?",
                             (product_id,)).fetchone()
    return dict(row) if row else None


def attach_item_setting(connection, product: dict) -> dict:
    """Put the item's OVERRIDE values on the product dict the engine passes around."""
    setting = load_item_setting(connection, product["id"])
    product["alert_setting"] = setting
    product["alert_override"] = setting if setting and setting["mode"] == "OVERRIDE" else None
    return product


def alerts_enabled(settings: dict) -> bool:
    return str(settings.get("alerts_enabled", "1")) != "0"


def is_snoozed(incident: dict, now) -> bool:
    until = parse_iso(incident.get("snoozed_until"))
    return bool(until and until > now)


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #
def load_product(connection, product_id: int, with_setting: bool = False) -> dict | None:
    row = connection.execute(
        """SELECT p.id, p.product_code, p.product_name, p.category, p.unit,
                  p.reorder_level, p.active, COALESCE(m.quantity, 0) AS quantity
           FROM products p LEFT JOIN main_inventory m ON m.product_id = p.id
           WHERE p.id=?""", (product_id,)).fetchone()
    if not row:
        return None
    product = dict(row)
    return attach_item_setting(connection, product) if with_setting else product


def products_in_scope(connection, rules: list[dict]) -> list[int]:
    """Products any rule could match, plus products holding a live incident."""
    rows = connection.execute("SELECT id, category FROM products WHERE active=1").fetchall()
    ids = {r["id"] for r in rows if any(match_kind(rule, dict(r)) for rule in rules)}
    placeholders = ",".join("?" * len(ACTIVE_STATUSES))
    for row in connection.execute(
            f"SELECT DISTINCT product_id FROM alert_incidents WHERE status IN ({placeholders})",
            ACTIVE_STATUSES).fetchall():
        ids.add(row["product_id"])
    for row in connection.execute("SELECT DISTINCT product_id FROM alert_breach_watch").fetchall():
        ids.add(row["product_id"])
    return sorted(ids)


# --------------------------------------------------------------------------- #
# Incidents
# --------------------------------------------------------------------------- #
def _active_incident(connection, rule_id, product_id):
    placeholders = ",".join("?" * len(ACTIVE_STATUSES))
    row = connection.execute(
        f"SELECT * FROM alert_incidents WHERE rule_id=? AND product_id=? AND status IN ({placeholders})",
        (rule_id, product_id, *ACTIVE_STATUSES)).fetchone()
    return dict(row) if row else None


def add_event(connection, incident_id, event_type, now, severity=None, quantity=None,
              actor="SYSTEM", detail=None) -> int:
    return connection.execute(
        """INSERT INTO alert_events(incident_id,event_type,severity,quantity,actor,detail,created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (incident_id, event_type, severity, quantity, actor, detail, iso(now))).lastrowid


def _resolve(connection, incident, now, resolution, qty, actor="SYSTEM"):
    connection.execute(
        "UPDATE alert_incidents SET status='RESOLVED', resolved_at=?, resolved_by=?, resolution=?, "
        "last_quantity=?, last_evaluated_at=? WHERE id=?",
        (iso(now), actor, resolution, qty, iso(now), incident["id"]))
    return add_event(connection, incident["id"], "RESOLVED", now, incident["severity"], qty, actor, resolution)


def evaluate_product(connection, product_id: int, now=None, settings=None, rules=None) -> list[dict]:
    """Bring every incident for one product up to date. Caller owns the transaction."""
    now = now or utc_now()
    settings = settings or load_settings(connection)
    rules = rules if rules is not None else active_rules(connection, now, settings)
    actions: list[dict] = []
    if not alerts_enabled(settings):
        return actions
    product = load_product(connection, product_id)
    if not product:
        return actions
    attach_item_setting(connection, product)
    qty = float(product["quantity"])

    placeholders = ",".join("?" * len(ACTIVE_STATUSES))
    live = [dict(r) for r in connection.execute(
        f"SELECT * FROM alert_incidents WHERE product_id=? AND status IN ({placeholders})",
        (product_id, *ACTIVE_STATUSES)).fetchall()]

    item_disabled = bool(product["alert_setting"] and product["alert_setting"]["mode"] == "DISABLED")
    if not product["active"] or item_disabled:
        reason = "Product deactivated." if not product["active"] else "Stock alerts disabled for this item."
        for incident in live:
            _resolve(connection, incident, now, reason, qty)
            actions.append({"incident_id": incident["id"], "action": "RESOLVED"})
        connection.execute("DELETE FROM alert_breach_watch WHERE product_id=?", (product_id,))
        return actions

    applicable = [(r, how) for r, how, suppressed in effective_rules(rules, product) if not suppressed]
    applicable_ids = {r["id"] for r, _ in applicable}

    # Incidents whose rule was disabled, edited out of scope or overridden.
    for incident in live:
        if incident["rule_id"] not in applicable_ids:
            _resolve(connection, incident, now, "Rule no longer applies to this product.", qty)
            actions.append({"incident_id": incident["id"], "action": "RESOLVED"})
    if applicable_ids:
        connection.execute(
            f"DELETE FROM alert_breach_watch WHERE product_id=? AND rule_id NOT IN "
            f"({','.join('?' * len(applicable_ids))})",
            (product_id, *applicable_ids))
    else:
        connection.execute("DELETE FROM alert_breach_watch WHERE product_id=?", (product_id,))

    for rule, _how in applicable:
        breached, severity, low, critical, op = classify(qty, rule, product)
        incident = _active_incident(connection, rule["id"], product_id)

        if breached:
            breach_started = now
            if rule["trigger_type"] == "BELOW_FOR_DURATION":
                connection.execute(
                    "INSERT OR IGNORE INTO alert_breach_watch(rule_id,product_id,since) VALUES (?,?,?)",
                    (rule["id"], product_id, iso(now)))
                since = parse_iso(connection.execute(
                    "SELECT since FROM alert_breach_watch WHERE rule_id=? AND product_id=?",
                    (rule["id"], product_id)).fetchone()["since"])
                breach_started = since
                if not incident and now - since < timedelta(minutes=int(rule.get("duration_minutes") or 0)):
                    continue

            if not incident:
                try:
                    incident_id = connection.execute(
                        """INSERT INTO alert_incidents
                           (rule_id,product_id,status,severity,opened_at,breach_started_at,
                            last_evaluated_at,opening_quantity,last_quantity,threshold_at_open,
                            rule_version)
                           VALUES (?,?,'OPEN',?,?,?,?,?,?,?,?)""",
                        (rule["id"], product_id, severity, iso(now), iso(breach_started),
                         iso(now), qty, qty, low, rule.get("version"))).lastrowid
                except sqlite3.IntegrityError:
                    continue  # another worker opened it first
                event_id = add_event(connection, incident_id, "OPENED", now, severity, qty,
                                     detail=f"Stock {format_qty(qty)} vs threshold {format_qty(low)}.")
                incident = _active_incident(connection, rule["id"], product_id)
                notify(connection, rule, incident, product, "ALERT", severity, event_id, now, settings)
                actions.append({"incident_id": incident_id, "action": "OPENED", "severity": severity})
                continue

            connection.execute(
                "UPDATE alert_incidents SET last_quantity=?, last_evaluated_at=? WHERE id=?",
                (qty, iso(now), incident["id"]))
            incident["last_quantity"] = qty
            old_rank, new_rank = SEVERITY_RANK[incident["severity"]], SEVERITY_RANK[severity]
            if new_rank > old_rank:
                # A worse condition always cancels a snooze.
                connection.execute("UPDATE alert_incidents SET severity=?, snoozed_until=NULL WHERE id=?",
                                   (severity, incident["id"]))
                event_id = add_event(connection, incident["id"], "SEVERITY_UP", now, severity, qty,
                                     detail=f"{incident['severity']} -> {severity}")
                incident["severity"] = severity
                if rule["notify_on_critical"] and incident["status"] != "SUPPRESSED":
                    notify(connection, rule, incident, product, "ALERT", severity, event_id, now, settings)
                actions.append({"incident_id": incident["id"], "action": "SEVERITY_UP", "severity": severity})
            elif new_rank < old_rank:
                connection.execute("UPDATE alert_incidents SET severity=? WHERE id=?",
                                   (severity, incident["id"]))
                add_event(connection, incident["id"], "SEVERITY_DOWN", now, severity, qty,
                          detail=f"{incident['severity']} -> {severity}")
                actions.append({"incident_id": incident["id"], "action": "SEVERITY_DOWN"})
            continue

        connection.execute("DELETE FROM alert_breach_watch WHERE rule_id=? AND product_id=?",
                           (rule["id"], product_id))
        if not incident:
            continue
        override = product.get("alert_override") or {}
        if low is not None and recovered(qty, low, op, rule["recovery_margin"],
                                         override.get("recovery_threshold")):
            event_id = _resolve(connection, incident, now, "Stock recovered above threshold.", qty)
            if rule["notify_on_recovery"] and incident["status"] != "SUPPRESSED":
                incident["last_quantity"] = qty
                notify(connection, rule, incident, product, "RECOVERY", incident["severity"],
                       event_id, now, settings)
            actions.append({"incident_id": incident["id"], "action": "RESOLVED"})
        else:
            connection.execute(
                "UPDATE alert_incidents SET last_quantity=?, last_evaluated_at=? WHERE id=?",
                (qty, iso(now), incident["id"]))
    return actions


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
def _context(rule, incident, product, severity, recipient, settings, step=None):
    low, critical, _ = thresholds(rule, product) or (None, None, None)
    qty = incident.get("last_quantity") if incident else product["quantity"]
    return {
        "item_name": product["product_name"],
        "item_code": product["product_code"],
        "category_name": product["category"],
        "warehouse_name": WAREHOUSE_NAME,
        "current_stock": format_qty(qty),
        "available_stock": format_qty(qty),
        "reorder_level": format_qty(product.get("reorder_level")),
        "configured_threshold": format_qty(low),
        "critical_threshold": format_qty(critical) or "not set",
        "unit": product.get("unit") or "pcs",
        "recipient_name": recipient.get("name") or "",
        "alert_severity": {"LOW": "low", "CRITICAL": "critical", "OUT": "out of stock"}.get(severity, ""),
        "detected_at": local_display(incident.get("opened_at"), settings) if incident else "",
        "rule_name": rule["name"],
        "incident_id": incident["id"] if incident else "",
        "inventory_link": os.getenv("OBS_APP_URL", ""),
        "escalation_step": step or "",
        "sent_at": local_display(utc_now(), settings),
    }


def _title(kind, severity, product, qty, step=None):
    label = {"LOW": "Low stock", "CRITICAL": "Critical stock", "OUT": "Out of stock"}.get(severity, "Stock")
    if kind == "RECOVERY":
        return f"Recovered: {product['product_name']}"
    if kind == "REMINDER":
        return f"Reminder · {label}: {product['product_name']}"
    if kind == "ESCALATION":
        return f"Escalation {step} · {label}: {product['product_name']}"
    return f"{label}: {product['product_name']} ({format_qty(qty)} {product.get('unit') or 'pcs'})"


def notify(connection, rule, incident, product, kind, severity, event_id, now, settings,
           entries=None, step=None) -> dict:
    """Queue in-app and email notifications for one incident event."""
    entries = entries if entries is not None else rule["recipients"]
    recipients, _excluded = resolve_recipients(
        connection, entries, rule["owner_user_id"], severity, settings)
    qty = incident.get("last_quantity")
    in_app = email = 0
    title = _title(kind, severity, product, qty, step)
    body = (f"{product['product_name']} ({product['product_code']}) is at {format_qty(qty)} "
            f"{product.get('unit') or 'pcs'} in the {WAREHOUSE_NAME.lower()}. Rule: {rule['name']}.")
    if kind == "RECOVERY":
        body = f"Stock recovered to {format_qty(qty)}. Incident #{incident['id']} is resolved."

    if rule["channel_in_app"]:
        for r in recipients:
            if r["user_id"] is None:
                continue
            in_app += connection.execute(
                """INSERT OR IGNORE INTO user_notifications
                   (user_id,idempotency_key,incident_id,product_id,kind,severity,title,body,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (r["user_id"], f"{kind}:{incident['id']}:{event_id}:{r['key']}", incident["id"],
                 product["id"], kind, severity, title, body, iso(now))).rowcount

    digest_only = rule["frequency"] in DIGEST_FREQUENCIES and kind in ("ALERT", "REMINDER")
    if rule["channel_email"] and not digest_only:
        template = SEVERITY_TEMPLATE[severity] if kind == "ALERT" else KIND_TEMPLATE[kind]
        bypass = (str(settings.get("critical_ignores_business_hours", "0")) == "1"
                  and severity in ("CRITICAL", "OUT"))
        due = next_business_start(now, settings) if rule["business_hours_only"] and not bypass else now
        max_attempts = _smtp_max_attempts(connection)
        for r in recipients:
            if not r["email"]:
                continue
            subject, text, html_body = render_email(
                connection, template, _context(rule, incident, product, severity, r, settings, step))
            email += connection.execute(
                """INSERT OR IGNORE INTO notification_jobs
                   (idempotency_key,kind,incident_id,rule_id,user_id,recipient_email,recipient_name,
                    subject,body_text,body_html,status,max_attempts,next_attempt_at,created_by,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'PENDING',?,?,'SYSTEM',?)""",
                (f"{kind}:{incident['id']}:{event_id}:{r['key']}", kind, incident["id"], rule["id"],
                 r["user_id"], r["email"], r["name"], subject, text, html_body, max_attempts,
                 iso(due), iso(now))).rowcount

    if kind in ("ALERT", "REMINDER"):
        connection.execute("UPDATE alert_incidents SET last_notified_at=? WHERE id=?",
                           (iso(now), incident["id"]))
    return {"in_app": in_app, "email": email, "recipients": len(recipients)}


def _smtp_max_attempts(connection) -> int:
    row = connection.execute("SELECT max_attempts FROM smtp_settings WHERE id=1").fetchone()
    return int(row["max_attempts"]) if row else 6


# --------------------------------------------------------------------------- #
# Periodic work: reminders, escalation, digests
# --------------------------------------------------------------------------- #
def run_reminders(connection, now, settings) -> int:
    sent = 0
    rows = connection.execute(
        """SELECT i.* FROM alert_incidents i JOIN comm_rules r ON r.id = i.rule_id
           WHERE i.status IN ('OPEN','ACKNOWLEDGED','ESCALATED') AND r.status='ACTIVE'
             AND r.frequency IN ('REPEAT_HOURS','DAILY')""").fetchall()
    for row in rows:
        incident = dict(row)
        if is_snoozed(incident, now):
            continue
        rule = load_rule(connection, incident["rule_id"])
        if incident["reminders_sent"] >= rule["max_reminders"]:
            continue
        if rule["business_hours_only"] and not is_business_time(now, settings):
            continue
        last = parse_iso(incident["last_notified_at"]) or parse_iso(incident["opened_at"])
        if rule["frequency"] == "REPEAT_HOURS":
            due = now - last >= timedelta(hours=max(1, int(rule["repeat_hours"] or 24)))
        else:
            due = local_date(last, settings) != local_date(now, settings)
        if not due:
            continue
        product = load_product(connection, incident["product_id"], with_setting=True)
        event_id = add_event(connection, incident["id"], "REMINDER", now, incident["severity"],
                             incident["last_quantity"])
        connection.execute("UPDATE alert_incidents SET reminders_sent=reminders_sent+1 WHERE id=?",
                           (incident["id"],))
        notify(connection, rule, incident, product, "REMINDER", incident["severity"], event_id, now, settings)
        sent += 1
    return sent


def run_escalations(connection, now, settings) -> int:
    fired = 0
    rows = connection.execute(
        """SELECT i.* FROM alert_incidents i
           JOIN comm_rules r ON r.id = i.rule_id
           JOIN comm_escalation_policies p ON p.id = r.escalation_policy_id AND p.active=1
           WHERE i.status IN ('OPEN','ESCALATED') AND r.status='ACTIVE'""").fetchall()
    for row in rows:
        incident = dict(row)
        if is_snoozed(incident, now):
            continue
        rule = load_rule(connection, incident["rule_id"])
        step_no = incident["escalation_step"] + 1
        steps = [dict(s) for s in connection.execute(
            "SELECT * FROM comm_escalation_steps WHERE policy_id=? AND step_no=?",
            (rule["escalation_policy_id"], step_no)).fetchall()]
        if not steps:
            continue
        wait = min(s["after_minutes"] for s in steps)
        if now - parse_iso(incident["opened_at"]) < timedelta(minutes=wait):
            continue
        product = load_product(connection, incident["product_id"], with_setting=True)
        event_id = add_event(connection, incident["id"], "ESCALATED", now, incident["severity"],
                             incident["last_quantity"], detail=f"Escalation step {step_no}")
        connection.execute("UPDATE alert_incidents SET escalation_step=?, status='ESCALATED' WHERE id=?",
                           (step_no, incident["id"]))
        entries = [{"recipient_type": s["recipient_type"], "recipient_value": s["recipient_value"],
                    "level": "ALL"} for s in steps]
        notify(connection, rule, incident, product, "ESCALATION", incident["severity"], event_id,
               now, settings, entries=entries, step=step_no)
        fired += 1
    return fired


def run_digests(connection, now, settings) -> int:
    queued = 0
    tz_today = local_date(now, settings)
    from comms_core import get_tz, _hhmm  # local import keeps the public surface small
    local_now = now.astimezone(get_tz(settings.get("timezone") or "Asia/Kathmandu"))
    if local_now.time() < _hhmm(settings.get("digest_time"), "09:00"):
        return 0
    for rule in active_rules(connection, now, settings):
        if rule["frequency"] not in DIGEST_FREQUENCIES or not rule["channel_email"]:
            continue
        if rule["frequency"] == "WEEKLY_DIGEST" and str(local_now.weekday()) != str(settings.get("digest_weekday", "6")):
            continue
        incidents = connection.execute(
            """SELECT i.*, p.product_name, p.product_code, p.unit FROM alert_incidents i
               JOIN products p ON p.id = i.product_id
               WHERE i.rule_id=? AND i.status IN ('OPEN','ACKNOWLEDGED','ESCALATED')
               ORDER BY p.product_name""", (rule["id"],)).fetchall()
        if not incidents:
            continue
        lines = "\n".join(
            f"- {i['product_name']} ({i['product_code']}): {format_qty(i['last_quantity'])} "
            f"{i['unit'] or 'pcs'} [{i['severity'].lower()}] incident #{i['id']}" for i in incidents)
        recipients, _ = resolve_recipients(connection, rule["recipients"], rule["owner_user_id"],
                                           None, settings)
        for r in recipients:
            if not r["email"]:
                continue
            context = {"recipient_name": r["name"], "rule_name": rule["name"], "digest_lines": lines,
                       "sent_at": local_display(now, settings)}
            subject, text, html_body = render_email(connection, "digest", context)
            queued += connection.execute(
                """INSERT OR IGNORE INTO notification_jobs
                   (idempotency_key,kind,rule_id,user_id,recipient_email,recipient_name,subject,
                    body_text,body_html,status,max_attempts,next_attempt_at,created_by,created_at)
                   VALUES (?,'DIGEST',?,?,?,?,?,?,?,'PENDING',?,?,'SYSTEM',?)""",
                (f"DIGEST:{rule['id']}:{tz_today}:{r['key']}", rule["id"], r["user_id"], r["email"],
                 r["name"], subject, text, html_body, _smtp_max_attempts(connection), iso(now),
                 iso(now))).rowcount
    return queued


# --------------------------------------------------------------------------- #
# Simulation (no writes)
# --------------------------------------------------------------------------- #
def simulate(connection, rule: dict, settings=None) -> dict:
    """Show what a rule would do right now, without writing or sending anything."""
    settings = settings or load_settings(connection)
    now = utc_now()
    others = [r for r in active_rules(connection, now, settings) if r["id"] != rule.get("id")]
    products = [dict(r) for r in connection.execute(
        """SELECT p.id, p.product_code, p.product_name, p.category, p.unit, p.reorder_level,
                  p.active, COALESCE(m.quantity,0) AS quantity
           FROM products p LEFT JOIN main_inventory m ON m.product_id = p.id
           WHERE p.active=1 ORDER BY p.category, p.product_name""").fetchall()]
    items = []
    for product in products:
        how = match_kind(rule, product)
        if not how:
            continue
        attach_item_setting(connection, product)
        item_disabled = bool(product["alert_setting"] and product["alert_setting"]["mode"] == "DISABLED")
        overriding = None
        if how != "PRODUCT":
            for other in others:
                if other.get("exclusive") and match_kind(other, product) == "PRODUCT":
                    overriding = other["name"]
                    break
        breached, severity, low, critical, _op = classify(float(product["quantity"]), rule, product)
        existing = _active_incident(connection, rule["id"], product["id"]) if rule.get("id") else None
        items.append({
            "product_id": product["id"], "product_code": product["product_code"],
            "product_name": product["product_name"], "category": product["category"],
            "quantity": product["quantity"], "unit": product["unit"],
            "threshold": low, "critical_threshold": critical, "matched_by": how,
            "breached": breached, "severity": severity,
            "overridden_by": overriding,
            "item_disabled": item_disabled,
            "item_override": bool(product["alert_override"]),
            "existing_incident_id": existing["id"] if existing else None,
            "would_notify": bool(breached and not overriding and not existing and not item_disabled),
        })
    preview = {}
    for level in ("LOW", "CRITICAL"):
        people, excluded = resolve_recipients(connection, rule.get("recipients") or [],
                                              rule.get("owner_user_id"), level, settings)
        preview[level] = {"recipients": people, "excluded": excluded}
    return {
        "items": items,
        "matched": len(items),
        "breached": sum(1 for i in items if i["breached"]),
        "would_notify": sum(1 for i in items if i["would_notify"]),
        "recipients": preview,
        "note": "Simulation only. Nothing was saved and no message was sent.",
    }


# --------------------------------------------------------------------------- #
# Effective configuration and per-item simulation (no writes)
# --------------------------------------------------------------------------- #
SOURCE_LABELS = {
    "RULE": "Rule threshold",
    "RULE_OUT_OF_STOCK": "Rule (out of stock)",
    "RULE_PERCENT_OF_REORDER": "Rule (% of reorder level)",
    "ITEM_REORDER_LEVEL": "Item reorder level",
    "ITEM_OVERRIDE": "Item override",
    "RULE_MARGIN": "Rule recovery margin",
}
MATCH_LABELS = {"PRODUCT": "Item named in rule", "CATEGORY": "Category", "ALL": "Every product"}


def effective_configuration(connection, product: dict, settings=None, rules=None, now=None) -> dict:
    """Everything that decides alerts for one item, and where each value comes from."""
    now = now or utc_now()
    settings = settings or load_settings(connection)
    rules = rules if rules is not None else active_rules(connection, now, settings)
    if "alert_setting" not in product:
        attach_item_setting(connection, product)
    setting = product.get("alert_setting")
    entries = []
    for rule, how, suppressed_by in effective_rules(rules, product):
        detail = threshold_details(rule, product)
        entries.append({
            "rule_id": rule["id"], "rule_name": rule["name"], "rule_version": rule.get("version"),
            "priority": rule["priority"], "matched_by": how, "matched_label": MATCH_LABELS.get(how, how),
            "applies": suppressed_by is None and detail is not None,
            "overridden_by_rule_id": suppressed_by,
            "trigger_type": rule["trigger_type"],
            "low": detail["low"] if detail else None,
            "critical": detail["critical"] if detail else None,
            "recovery": detail["recovery"] if detail else None,
            "comparison": detail["op"] if detail else None,
            "sources": {k: {"code": v, "label": SOURCE_LABELS.get(v, v)}
                        for k, v in (detail["sources"] if detail else {}).items()},
            "warnings": (detail["warnings"] if detail else
                         ["This rule has no threshold for this item (no reorder level or percentage)."]),
            "frequency": rule["frequency"], "repeat_hours": rule.get("repeat_hours"),
            "channel_email": bool(rule["channel_email"]), "channel_in_app": bool(rule["channel_in_app"]),
            "business_hours_only": bool(rule["business_hours_only"]),
            "notify_on_critical": bool(rule["notify_on_critical"]),
            "notify_on_recovery": bool(rule["notify_on_recovery"]),
            "escalation_policy_id": rule.get("escalation_policy_id"),
        })
    applying = [e for e in entries if e["applies"]]
    mode = setting["mode"] if setting else "INHERIT"
    if not alerts_enabled(settings):
        status, explanation = "GLOBAL_OFF", "Stock alerts are switched off for the whole system."
    elif not product.get("active", 1):
        status, explanation = "INACTIVE", "The item is inactive."
    elif mode == "DISABLED":
        status, explanation = "DISABLED", "Alerts are disabled for this item."
    elif not entries:
        status = "INVALID" if mode == "OVERRIDE" else "NOT_CONFIGURED"
        explanation = ("The item has its own thresholds but no active rule reaches it, so nobody would be told."
                       if mode == "OVERRIDE" else "No active rule reaches this item. It never sends alerts.")
    elif not applying:
        status, explanation = "INVALID", "Rules reach this item but none can be applied to it."
    elif mode == "OVERRIDE" or any(e["matched_by"] == "PRODUCT" for e in applying):
        status, explanation = "CONFIGURED", "Explicitly configured for this item."
    else:
        status, explanation = "INHERITED", "Inherits from a category or every-product rule."
    if status in ("CONFIGURED", "INHERITED") and any(e["warnings"] for e in applying):
        explanation += " Some values are ignored; see the warnings."
    # The headline figures: the most sensitive applying rule (highest alert threshold).
    head = max(applying, key=lambda e: e["low"]) if applying else None
    return {
        "product_id": product["id"], "status": status, "explanation": explanation,
        "item_setting": setting, "mode": mode,
        "low": head["low"] if head else None, "critical": head["critical"] if head else None,
        "recovery": head["recovery"] if head else None,
        "source": ("Item override" if mode == "OVERRIDE" and head else
                   (f"{head['matched_label']}: {head['rule_name']}" if head else None)),
        "rules": entries,
    }


def simulate_item(connection, product_id: int, quantity: float, settings=None) -> dict:
    """What would happen if this item's stock were `quantity` now. Writes nothing."""
    settings = settings or load_settings(connection)
    now = utc_now()
    product = load_product(connection, product_id, with_setting=True)
    if not product:
        return {}
    product["quantity"] = quantity
    rules = active_rules(connection, now, settings)
    config = effective_configuration(connection, product, settings, rules, now)
    results = []
    blocked = config["status"] in ("GLOBAL_OFF", "DISABLED", "INACTIVE")
    for entry in config["rules"]:
        rule = next(r for r in rules if r["id"] == entry["rule_id"])
        breached, severity, low, critical, op = classify(float(quantity), rule, product)
        incident = _active_incident(connection, rule["id"], product_id)
        outcome, reason = "NONE", "Stock is above the alert threshold."
        if not entry["applies"]:
            outcome, reason = "SKIPPED", ("An exclusive rule for this item takes over."
                                          if entry["overridden_by_rule_id"] else "Rule cannot be applied.")
        elif blocked:
            outcome, reason = "BLOCKED", config["explanation"]
        elif breached and rule["trigger_type"] == "BELOW_FOR_DURATION" and not incident:
            outcome = "WATCH"
            reason = f"Opens only after stock stays low for {rule.get('duration_minutes')} minutes."
        elif breached and not incident:
            outcome, reason = "OPEN", "A new incident would open and notify recipients."
        elif breached and incident:
            if SEVERITY_RANK[severity] > SEVERITY_RANK[incident["severity"]]:
                outcome = "SEVERITY_UP"
                reason = ("Severity rises; recipients are notified." if rule["notify_on_critical"]
                          else "Severity rises; this rule does not notify on severity changes.")
            else:
                outcome = "SUPPRESSED"
                reason = (f"Incident #{incident['id']} is already open at {incident['severity'].lower()}; "
                          f"frequency '{rule['frequency']}' prevents a duplicate message.")
        elif incident:
            override = product.get("alert_override") or {}
            if recovered(float(quantity), low, op, rule["recovery_margin"], override.get("recovery_threshold")):
                outcome = "RESOLVE"
                reason = ("Incident would resolve and a recovery message would be sent."
                          if rule["notify_on_recovery"] else "Incident would resolve (no recovery message).")
            else:
                outcome = "HOLD"
                reason = "Above the alert threshold but not yet at the recovery level, so it stays open."
        level = severity if severity in ("CRITICAL", "OUT") else "LOW"
        people, excluded = resolve_recipients(connection, rule["recipients"], rule["owner_user_id"],
                                              level, settings)
        preview = None
        if outcome in ("OPEN", "SEVERITY_UP", "RESOLVE") and rule["channel_email"]:
            fake = {"id": incident["id"] if incident else "(new)", "last_quantity": quantity,
                    "opened_at": iso(now)}
            template = "recovery" if outcome == "RESOLVE" else SEVERITY_TEMPLATE[severity]
            first = people[0] if people else {"name": "Recipient"}
            subject, text, _html = render_email(
                connection, template, _context(rule, fake, product, severity or "LOW", first, settings))
            preview = {"subject": subject, "body": text}
        results.append({**entry, "breached": breached, "severity": severity, "outcome": outcome,
                        "reason": reason, "existing_incident_id": incident["id"] if incident else None,
                        "recipients": [{"name": p["name"], "email": bool(p["email"]),
                                        "in_app": p["user_id"] is not None} for p in people],
                        "excluded": excluded, "email_preview": preview})
    return {"product_id": product_id, "quantity": quantity, "status": config["status"],
            "explanation": config["explanation"], "results": results,
            "note": "Simulation only. Stock, incidents and delivery history are unchanged; nothing was sent."}
