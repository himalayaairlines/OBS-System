"""Communication Manager: stock-alert rules, recipients, alerts and deliveries.

Authorisation (all enforced here, not in the app):
  comms_view       read the dashboard, rules, alerts and an alert's deliveries
  comms_rules      create rules and edit the rules they own
  comms_rules_all  edit, disable or delete anyone's rule
  comms_groups     recipient groups and escalation policies
  comms_admin      the delivery log, retry/cancel, communication settings
  comms_templates  email templates

A recipient must be an active user whose role can see warehouse stock, or an
address in an allowed external domain. Anyone else is dropped and reported.
"""

from datetime import date, timedelta
import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

import alert_engine
from comms_core import (
    ACTIVE_STATUSES, EMAIL_RE, allowed_email_domains, get_tz, iso, load_settings,
    render_text, resolve_recipients, unknown_placeholders, user_can_receive, utc_now,
)
from comms_schema import DEFAULT_TEMPLATES, TEMPLATE_PLACEHOLDERS
from database import ROLES, get_connection
from paging import MAX_PAGE_SIZE, PageParam
from security import current_user, log_action, permissions_for, requires

router = APIRouter(prefix="/communication", tags=["communication"])

TRIGGERS = ("BELOW", "AT_OR_BELOW", "OUT_OF_STOCK", "PERCENT_OF_REORDER", "BELOW_FOR_DURATION")
FREQUENCIES = ("ONCE", "REPEAT_HOURS", "DAILY", "DAILY_DIGEST", "WEEKLY_DIGEST")
RECIPIENT_TYPES = ("USER", "ROLE", "GROUP", "OWNER", "EMAIL")


def _page_window(total, page, page_size, limit, offset):
    """Translate page/page_size into limit/offset; legacy limit/offset still work."""
    if not page:
        return None, limit, offset
    size = page_size or limit
    pages = max(1, -(-total // size))
    page = min(page, pages)
    return page, size, (page - 1) * size


def _paged(body, page, size):
    if page:
        pages = max(1, -(-body["total"] // size))
        body.update(page=page, page_size=size, pages=pages, has_prev=page > 1, has_next=page < pages)
    return body


def has(user: dict, permission: str) -> bool:
    return permission in permissions_for(user["role"])


def requires_any(*permissions: str):
    def dependency(user: dict = Depends(current_user)) -> dict:
        if not any(has(user, p) for p in permissions):
            raise HTTPException(status_code=403, detail="You do not have permission to perform this action.")
        return user
    return dependency


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class RecipientIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient_type: str = Field(pattern="^(USER|ROLE|GROUP|OWNER|EMAIL)$")
    recipient_value: str | None = Field(default=None, max_length=254)
    level: str = Field(default="ALL", pattern="^(ALL|LOW|CRITICAL)$")


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    owner_user_id: int | None = None
    priority: int = Field(default=100, ge=1, le=1000)
    effective_from: str | None = None
    effective_to: str | None = None
    scope_all: bool = False
    exclusive: bool = False
    products: list[int] = []
    categories: list[str] = []
    exclude_products: list[int] = []
    exclude_categories: list[str] = []
    trigger_type: str = Field(default="BELOW")
    threshold_source: str = Field(default="FIXED", pattern="^(FIXED|REORDER_LEVEL)$")
    threshold: float | None = Field(default=None, ge=0)
    critical_threshold: float | None = Field(default=None, ge=0)
    percent: float | None = Field(default=None, gt=0, le=500)
    duration_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 30)
    recovery_margin: float = Field(default=0, ge=0)
    channel_in_app: bool = True
    channel_email: bool = True
    frequency: str = Field(default="ONCE")
    repeat_hours: int | None = Field(default=None, ge=1, le=168)
    max_reminders: int = Field(default=10, ge=0, le=100)
    business_hours_only: bool = False
    notify_on_critical: bool = True
    notify_on_recovery: bool = False
    escalation_policy_id: int | None = None
    recipients: list[RecipientIn] = []
    activate: bool = False


class GroupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    active: bool = True
    user_ids: list[int] = []


class StepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_no: int = Field(ge=1, le=10)
    after_minutes: int = Field(ge=1, le=60 * 24 * 14)
    recipient_type: str = Field(pattern="^(USER|ROLE|GROUP|OWNER|EMAIL)$")
    recipient_value: str | None = Field(default=None, max_length=254)


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    active: bool = True
    steps: list[StepIn] = Field(min_length=1)


class CommentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comment: str | None = Field(default=None, max_length=1000)


class RemarksIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    remarks: str = Field(min_length=3, max_length=1000)


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=250)
    body: str = Field(min_length=1, max_length=20000)


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = Field(min_length=1, max_length=64)
    business_start: str = Field(pattern=r"^\d{2}:\d{2}$")
    business_end: str = Field(pattern=r"^\d{2}:\d{2}$")
    business_days: list[int] = Field(min_length=1)
    digest_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    digest_weekday: int = Field(ge=0, le=6)
    external_email_domains: list[str] = []
    sweep_minutes: int = Field(ge=1, le=60)
    # 7.4 (optional so older apps can still save the other settings)
    alerts_enabled: bool | None = None
    max_snooze_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    allow_snooze_critical: bool | None = None
    critical_ignores_business_hours: bool | None = None


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _iso_date(value, label):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label} must be YYYY-MM-DD.")


def _validate_recipient_entry(connection, item, settings):
    kind, value = item.recipient_type, (item.recipient_value or "").strip()
    if kind == "OWNER":
        return None
    if not value:
        raise HTTPException(status_code=400, detail=f"A {kind.lower()} recipient needs a value.")
    if kind == "USER":
        row = connection.execute("SELECT role, active, name FROM users WHERE id=?",
                                 (int(value) if value.isdigit() else -1,)).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail=f"User {value} does not exist.")
        if not user_can_receive(row["role"]):
            raise HTTPException(status_code=400,
                                detail=f"{row['name']} cannot view warehouse stock, so cannot receive stock alerts.")
    elif kind == "ROLE":
        if value not in ROLES:
            raise HTTPException(status_code=400, detail=f"Unknown role {value}.")
        if not user_can_receive(value):
            raise HTTPException(status_code=400, detail=f"The {value} role cannot view warehouse stock.")
    elif kind == "GROUP":
        if not value.isdigit() or not connection.execute(
                "SELECT 1 FROM comm_groups WHERE id=?", (int(value),)).fetchone():
            raise HTTPException(status_code=400, detail=f"Recipient group {value} does not exist.")
    elif kind == "EMAIL":
        address = value.lower()
        if not EMAIL_RE.match(address):
            raise HTTPException(status_code=400, detail=f"{value} is not a valid email address.")
        if address.rsplit("@", 1)[-1] not in allowed_email_domains(settings):
            raise HTTPException(
                status_code=400,
                detail=f"{value} is outside the allowed external domains. "
                       "Add the domain in Communication settings, or pick a registered user.")
        return address
    return value


def _validate_rule(connection, data: RuleIn, user: dict, owner_id: int) -> dict:
    settings = load_settings(connection)
    if data.trigger_type not in TRIGGERS:
        raise HTTPException(status_code=400, detail="Unknown trigger type.")
    if data.frequency not in FREQUENCIES:
        raise HTTPException(status_code=400, detail="Unknown frequency.")
    if not (data.scope_all or data.products or data.categories):
        raise HTTPException(status_code=400,
                            detail="Choose at least one product or category, or deliberately select every product.")
    for pid in set(data.products) | set(data.exclude_products):
        row = connection.execute("SELECT active FROM products WHERE id=?", (pid,)).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail=f"Product {pid} does not exist.")
    known = {r["category"] for r in connection.execute("SELECT DISTINCT category FROM products").fetchall()}
    for category in set(data.categories) | set(data.exclude_categories):
        if category not in known:
            raise HTTPException(status_code=400, detail=f"Category {category} does not exist.")
    if set(data.products) & set(data.exclude_products):
        raise HTTPException(status_code=400, detail="A product cannot be both included and excluded.")
    if set(data.categories) & set(data.exclude_categories):
        raise HTTPException(status_code=400, detail="A category cannot be both included and excluded.")

    trigger = data.trigger_type
    if trigger == "PERCENT_OF_REORDER" and data.percent is None:
        raise HTTPException(status_code=400, detail="Enter the percentage of the reorder level.")
    if trigger == "BELOW_FOR_DURATION" and not data.duration_minutes:
        raise HTTPException(status_code=400, detail="Enter how long stock must stay low before alerting.")
    if trigger in ("BELOW", "AT_OR_BELOW", "BELOW_FOR_DURATION") and data.threshold_source == "FIXED" \
            and data.threshold is None:
        raise HTTPException(status_code=400, detail="Enter the alert threshold.")
    if data.critical_threshold is not None and trigger in ("BELOW", "AT_OR_BELOW", "BELOW_FOR_DURATION") \
            and data.threshold_source == "FIXED" and data.critical_threshold >= data.threshold:
        raise HTTPException(status_code=400, detail="The critical threshold must be lower than the alert threshold.")
    if data.frequency == "REPEAT_HOURS" and not data.repeat_hours:
        raise HTTPException(status_code=400, detail="Enter how many hours between reminders.")
    if not (data.channel_in_app or data.channel_email):
        raise HTTPException(status_code=400, detail="Choose at least one channel.")
    start, end = _iso_date(data.effective_from, "Start date"), _iso_date(data.effective_to, "End date")
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="The end date is before the start date.")
    if data.escalation_policy_id is not None and not connection.execute(
            "SELECT 1 FROM comm_escalation_policies WHERE id=?", (data.escalation_policy_id,)).fetchone():
        raise HTTPException(status_code=400, detail="Escalation policy not found.")

    recipients = []
    for item in data.recipients:
        value = _validate_recipient_entry(connection, item, settings)
        recipients.append({"recipient_type": item.recipient_type, "recipient_value": value,
                           "level": item.level})
    if data.activate:
        people, _ = resolve_recipients(connection, recipients, owner_id, None, settings)
        if not people:
            raise HTTPException(status_code=400,
                                detail="This rule has no valid recipients, so it cannot be activated.")
        if data.channel_email and not data.channel_in_app and not any(p["email"] for p in people):
            raise HTTPException(status_code=400,
                                detail="Email only, but none of the recipients has an email address.")
    return {"recipients": recipients, "effective_from": start, "effective_to": end}


def _resolve_owner(connection, data: RuleIn, user: dict, current_owner: int | None = None) -> int:
    owner_id = data.owner_user_id or current_owner or user["id"]
    if owner_id != user["id"] and owner_id != current_owner and not has(user, "comms_rules_all"):
        raise HTTPException(status_code=403, detail="Only management can assign a rule to someone else.")
    row = connection.execute("SELECT role, active FROM users WHERE id=?", (owner_id,)).fetchone()
    if not row or not row["active"]:
        raise HTTPException(status_code=400, detail="The rule owner must be an active user.")
    if not user_can_receive(row["role"]):
        raise HTTPException(status_code=400, detail="The rule owner must be able to view warehouse stock.")
    return owner_id


def _write_rule(connection, rule_id, data: RuleIn, clean: dict, owner_id: int, user: dict, status: str):
    fields = {
        "name": data.name.strip(), "description": (data.description or "").strip() or None,
        "owner_user_id": owner_id, "status": status, "priority": data.priority,
        "effective_from": clean["effective_from"], "effective_to": clean["effective_to"],
        "scope_all": int(data.scope_all), "exclusive": int(data.exclusive),
        "trigger_type": data.trigger_type, "threshold_source": data.threshold_source,
        "threshold": data.threshold, "critical_threshold": data.critical_threshold,
        "percent": data.percent, "duration_minutes": data.duration_minutes,
        "recovery_margin": data.recovery_margin, "channel_in_app": int(data.channel_in_app),
        "channel_email": int(data.channel_email), "frequency": data.frequency,
        "repeat_hours": data.repeat_hours, "max_reminders": data.max_reminders,
        "business_hours_only": int(data.business_hours_only),
        "notify_on_critical": int(data.notify_on_critical),
        "notify_on_recovery": int(data.notify_on_recovery),
        "escalation_policy_id": data.escalation_policy_id,
    }
    now = iso(utc_now())
    if rule_id is None:
        fields.update(created_by=user["employee_id"], created_at=now, updated_by=user["employee_id"], updated_at=now)
        cols = ",".join(fields)
        rule_id = connection.execute(
            f"INSERT INTO comm_rules({cols}) VALUES ({','.join('?' * len(fields))})",
            tuple(fields.values())).lastrowid
    else:
        fields.update(updated_by=user["employee_id"], updated_at=now)
        sets = ",".join(f"{k}=?" for k in fields)
        connection.execute(f"UPDATE comm_rules SET {sets}, version=version+1 WHERE id=?",
                           (*fields.values(), rule_id))
        connection.execute("DELETE FROM comm_rule_scopes WHERE rule_id=?", (rule_id,))
        connection.execute("DELETE FROM comm_rule_recipients WHERE rule_id=?", (rule_id,))
    scopes = ([("PRODUCT", str(p), 1) for p in dict.fromkeys(data.products)]
              + [("CATEGORY", c, 1) for c in dict.fromkeys(data.categories)]
              + [("PRODUCT", str(p), 0) for p in dict.fromkeys(data.exclude_products)]
              + [("CATEGORY", c, 0) for c in dict.fromkeys(data.exclude_categories)])
    connection.executemany(
        "INSERT INTO comm_rule_scopes(rule_id,scope_type,scope_value,include) VALUES (?,?,?,?)",
        [(rule_id, *s) for s in scopes])
    connection.executemany(
        "INSERT OR IGNORE INTO comm_rule_recipients(rule_id,recipient_type,recipient_value,level) VALUES (?,?,?,?)",
        [(rule_id, r["recipient_type"], r["recipient_value"], r["level"]) for r in clean["recipients"]])
    return rule_id


def _rule_or_404(connection, rule_id):
    rule = alert_engine.load_rule(connection, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found.")
    return rule


def _can_edit(user, rule) -> bool:
    return has(user, "comms_rules_all") or (has(user, "comms_rules") and rule["owner_user_id"] == user["id"])


def _require_edit(user, rule):
    if not _can_edit(user, rule):
        raise HTTPException(status_code=403, detail="You can only change rules you own.")


def _snapshot(connection, rule_id: int, action: str, user: dict):
    """Keep the saved state of a rule. Call inside the transaction that changed it."""
    rule = alert_engine.load_rule(connection, rule_id)
    connection.execute(
        "INSERT OR REPLACE INTO comm_rule_versions(rule_id,version,action,snapshot,changed_by,changed_at) "
        "VALUES (?,?,?,?,?,?)",
        (rule_id, rule["version"], action, json.dumps(rule, default=str), user["employee_id"], iso(utc_now())))


def reevaluate_rule_products(rule_id: int):
    """Apply a rule change at once instead of waiting for the next sweep."""
    connection = get_connection()
    try:
        settings = load_settings(connection)
        now = utc_now()
        rule = alert_engine.load_rule(connection, rule_id)
        ids = set(alert_engine.products_in_scope(connection, [rule])) if rule else set()
        ids |= {r["product_id"] for r in connection.execute(
            "SELECT DISTINCT product_id FROM alert_incidents WHERE rule_id=?", (rule_id,)).fetchall()}
        rules = alert_engine.active_rules(connection, now, settings)
        for product_id in sorted(ids):
            with alert_engine.immediate(connection):
                alert_engine.evaluate_product(connection, product_id, now, settings, rules)
    finally:
        connection.close()


def _rule_from_input(data: RuleIn, owner_id: int, rule_id=None) -> dict:
    """An unsaved rule in the engine's shape, for simulation."""
    rule = data.model_dump(exclude={"products", "categories", "exclude_products",
                                    "exclude_categories", "recipients", "activate"})
    rule.update(id=rule_id, owner_user_id=owner_id, status="DRAFT")
    rule["scopes"] = ([{"scope_type": "PRODUCT", "scope_value": str(p), "include": 1} for p in data.products]
                      + [{"scope_type": "CATEGORY", "scope_value": c, "include": 1} for c in data.categories]
                      + [{"scope_type": "PRODUCT", "scope_value": str(p), "include": 0} for p in data.exclude_products]
                      + [{"scope_type": "CATEGORY", "scope_value": c, "include": 0} for c in data.exclude_categories])
    rule["recipients"] = [r.model_dump() for r in data.recipients]
    return rule


def _describe_scope(connection, rule) -> str:
    names = {str(r["id"]): r["product_name"] for r in connection.execute(
        "SELECT id, product_name FROM products").fetchall()}
    inc = [names.get(s["scope_value"], f"#{s['scope_value']}") if s["scope_type"] == "PRODUCT"
           else f"category {s['scope_value']}" for s in rule["scopes"] if s["include"]]
    exc = [names.get(s["scope_value"], f"#{s['scope_value']}") if s["scope_type"] == "PRODUCT"
           else f"category {s['scope_value']}" for s in rule["scopes"] if not s["include"]]
    text = "Every product" if rule["scope_all"] and not inc else ", ".join(inc)
    if rule["scope_all"] and inc:
        text = "Every product"
    if exc:
        text += f" (except {', '.join(exc)})"
    return text


def _describe_condition(rule) -> str:
    t = rule["trigger_type"]
    src = "reorder level" if rule["threshold_source"] == "REORDER_LEVEL" else _n(rule["threshold"])
    if t == "OUT_OF_STOCK":
        base = "Out of stock"
    elif t == "PERCENT_OF_REORDER":
        base = f"Below {_n(rule['percent'])}% of reorder level"
    elif t == "AT_OR_BELOW":
        base = f"At or below {src}"
    elif t == "BELOW_FOR_DURATION":
        base = f"Below {src} for {rule['duration_minutes']} min"
    else:
        base = f"Below {src}"
    if rule.get("critical_threshold") is not None and t != "OUT_OF_STOCK":
        base += f"; critical below {_n(rule['critical_threshold'])}"
    return base


def _n(value):
    if value is None:
        return "—"
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


# --------------------------------------------------------------------------- #
# Options for the rule builder
# --------------------------------------------------------------------------- #
@router.get("/options")
def options(user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        products = [dict(r) for r in connection.execute(
            """SELECT p.id, p.product_code, p.product_name, p.category, p.unit, p.reorder_level,
                      COALESCE(m.quantity,0) AS quantity
               FROM products p LEFT JOIN main_inventory m ON m.product_id = p.id
               WHERE p.active=1 ORDER BY p.category, p.product_name""").fetchall()]
        users = [{**dict(r), "has_email": bool(r["email"]), "can_receive": user_can_receive(r["role"]),
                  "email": None} for r in connection.execute(
            "SELECT id, employee_id, name, role, email FROM users WHERE active=1 ORDER BY name").fetchall()]
        groups = [dict(r) for r in connection.execute(
            """SELECT g.id, g.name, g.active, COUNT(m.user_id) AS members FROM comm_groups g
               LEFT JOIN comm_group_members m ON m.group_id = g.id GROUP BY g.id ORDER BY g.name""").fetchall()]
        policies = [dict(r) for r in connection.execute(
            "SELECT id, name, active FROM comm_escalation_policies ORDER BY name").fetchall()]
        settings = load_settings(connection)
        return {
            "products": products,
            "categories": sorted({p["category"] for p in products}),
            "users": users,
            "roles": [{"value": r, "can_receive": user_can_receive(r)} for r in ROLES],
            "groups": groups,
            "policies": policies,
            "timezone": settings.get("timezone"),
            "external_email_domains": sorted(allowed_email_domains(settings)),
            "me": {"id": user["id"], "can_create": has(user, "comms_rules"),
                   "can_edit_all": has(user, "comms_rules_all"), "can_manage_groups": has(user, "comms_groups"),
                   "is_admin": has(user, "comms_admin")},
        }
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@router.get("/dashboard")
def dashboard(user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        rules = {r["status"]: r["c"] for r in connection.execute(
            "SELECT status, COUNT(*) c FROM comm_rules GROUP BY status").fetchall()}
        marks = ",".join("?" * len(ACTIVE_STATUSES))
        active = connection.execute(
            f"""SELECT i.status, i.severity, p.category FROM alert_incidents i
                JOIN products p ON p.id = i.product_id WHERE i.status IN ({marks})""",
            ACTIVE_STATUSES).fetchall()
        since = iso(utc_now() - timedelta(days=30))
        resolved = connection.execute(
            "SELECT COUNT(*) c FROM alert_incidents WHERE status='RESOLVED' AND resolved_at >= ?",
            (since,)).fetchone()["c"]
        jobs = {r["status"]: r["c"] for r in connection.execute(
            "SELECT status, COUNT(*) c FROM notification_jobs GROUP BY status").fetchall()}
        by_category: dict[str, int] = {}
        for row in active:
            by_category[row["category"]] = by_category.get(row["category"], 0) + 1
        days = 14
        start = (utc_now() - timedelta(days=days - 1)).date().isoformat()
        opened = {r["d"]: r["c"] for r in connection.execute(
            "SELECT substr(opened_at,1,10) d, COUNT(*) c FROM alert_incidents "
            "WHERE substr(opened_at,1,10) >= ? GROUP BY d", (start,)).fetchall()}
        trend = []
        for offset in range(days):
            day = (date.fromisoformat(start) + timedelta(days=offset)).isoformat()
            trend.append({"date": day, "opened": opened.get(day, 0)})
        settings = load_settings(connection)
        smtp = connection.execute("SELECT enabled FROM smtp_settings WHERE id=1").fetchone()
        awaiting = connection.execute(
            f"""SELECT COUNT(*) c FROM alert_incidents i JOIN comm_rules r ON r.id = i.rule_id
                WHERE i.status IN ('OPEN','ESCALATED') AND r.escalation_policy_id IS NOT NULL""").fetchone()["c"]
        return {
            "rules": {"active": rules.get("ACTIVE", 0), "disabled": rules.get("DISABLED", 0),
                      "draft": rules.get("DRAFT", 0)},
            "alerts": {
                "low": sum(1 for r in active if r["severity"] == "LOW"),
                "critical": sum(1 for r in active if r["severity"] == "CRITICAL"),
                "out": sum(1 for r in active if r["severity"] == "OUT"),
                "unacknowledged": sum(1 for r in active if r["status"] in ("OPEN", "ESCALATED")),
                "escalated": sum(1 for r in active if r["status"] == "ESCALATED"),
                "suppressed": sum(1 for r in active if r["status"] == "SUPPRESSED"),
                "snoozed": connection.execute(
                    f"SELECT COUNT(*) c FROM alert_incidents WHERE status IN ({marks}) AND snoozed_until > ?",
                    (*ACTIVE_STATUSES, iso(utc_now()))).fetchone()["c"],
                "resolved_30d": resolved,
                "awaiting_escalation": awaiting,
            },
            "by_category": [{"category": k, "count": v} for k, v in sorted(by_category.items(), key=lambda x: -x[1])],
            "trend": trend,
            "deliveries": {
                "pending": jobs.get("PENDING", 0) + jobs.get("PROCESSING", 0),
                "accepted": jobs.get("ACCEPTED", 0),
                "retrying": jobs.get("TEMP_FAILED", 0),
                "failed": jobs.get("FAILED", 0),
                "cancelled": jobs.get("CANCELLED", 0),
            },
            "smtp_enabled": bool(smtp and smtp["enabled"]),
            "alerts_enabled": alert_engine.alerts_enabled(settings),
            "item_overrides": connection.execute(
                "SELECT COUNT(*) c FROM stock_alert_item_settings WHERE mode='OVERRIDE'").fetchone()["c"],
            "items_disabled": connection.execute(
                "SELECT COUNT(*) c FROM stock_alert_item_settings WHERE mode='DISABLED'").fetchone()["c"],
            "last_sweep_at": settings.get("_last_sweep_at"),
            "timezone": settings.get("timezone"),
        }
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
@router.get("/rules")
def list_rules(status: str | None = None, user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        query = ("SELECT r.*, u.name AS owner_name FROM comm_rules r "
                 "LEFT JOIN users u ON u.id = r.owner_user_id")
        params = ()
        if status:
            query += " WHERE r.status=?"
            params = (status.upper(),)
        rows = connection.execute(query + " ORDER BY r.status='ACTIVE' DESC, r.priority, r.name", params).fetchall()
        result = []
        marks = ",".join("?" * len(ACTIVE_STATUSES))
        for row in rows:
            rule = alert_engine._hydrate(connection, dict(row))
            rule["scope_text"] = _describe_scope(connection, rule)
            rule["condition_text"] = _describe_condition(rule)
            rule["active_incidents"] = connection.execute(
                f"SELECT COUNT(*) c FROM alert_incidents WHERE rule_id=? AND status IN ({marks})",
                (rule["id"], *ACTIVE_STATUSES)).fetchone()["c"]
            rule["can_edit"] = _can_edit(user, rule)
            result.append(rule)
        return result
    finally:
        connection.close()


@router.get("/rules/{rule_id}")
def get_rule(rule_id: int, user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        rule = _rule_or_404(connection, rule_id)
        owner = connection.execute("SELECT name FROM users WHERE id=?", (rule["owner_user_id"],)).fetchone()
        rule["owner_name"] = owner["name"] if owner else None
        rule["scope_text"] = _describe_scope(connection, rule)
        rule["condition_text"] = _describe_condition(rule)
        rule["can_edit"] = _can_edit(user, rule)
        rule["incidents"] = [dict(r) for r in connection.execute(
            """SELECT i.id, i.status, i.severity, i.opened_at, i.resolved_at, i.last_quantity,
                      p.product_name, p.product_code
               FROM alert_incidents i JOIN products p ON p.id = i.product_id
               WHERE i.rule_id=? ORDER BY i.id DESC LIMIT 50""", (rule_id,)).fetchall()]
        return rule
    finally:
        connection.close()


@router.post("/rules")
def create_rule(data: RuleIn, user: dict = Depends(requires("comms_rules"))):
    connection = get_connection()
    try:
        owner_id = _resolve_owner(connection, data, user)
        clean = _validate_rule(connection, data, user, owner_id)
        status = "ACTIVE" if data.activate else "DRAFT"
        with connection:
            rule_id = _write_rule(connection, None, data, clean, owner_id, user, status)
            _snapshot(connection, rule_id, "CREATE", user)
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "CREATE_RULE", f"Created alert rule '{data.name}' ({status}).",
               "comm_rule", rule_id, metadata={"rule": data.model_dump()})
    if status == "ACTIVE":
        reevaluate_rule_products(rule_id)
    return get_rule(rule_id, user)


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, data: RuleIn, user: dict = Depends(requires("comms_rules"))):
    connection = get_connection()
    try:
        existing = _rule_or_404(connection, rule_id)
        _require_edit(user, existing)
        owner_id = _resolve_owner(connection, data, user, existing["owner_user_id"])
        status = "ACTIVE" if data.activate else (
            existing["status"] if existing["status"] != "ACTIVE" else "ACTIVE")
        if status == "ACTIVE":
            data.activate = True
        clean = _validate_rule(connection, data, user, owner_id)
        with connection:
            _write_rule(connection, rule_id, data, clean, owner_id, user, status)
            _snapshot(connection, rule_id, "UPDATE", user)
    finally:
        connection.close()
    before = {k: existing.get(k) for k in ("name", "status", "trigger_type", "threshold",
                                           "critical_threshold", "frequency")}
    log_action(user, "COMMUNICATION", "UPDATE_RULE", f"Updated alert rule '{data.name}'.",
               "comm_rule", rule_id, metadata={"before": before, "after": data.model_dump()})
    reevaluate_rule_products(rule_id)
    return get_rule(rule_id, user)


def _set_status(rule_id, user, status, action):
    connection = get_connection()
    try:
        rule = _rule_or_404(connection, rule_id)
        _require_edit(user, rule)
        if status == "ACTIVE":
            people, _ = resolve_recipients(connection, rule["recipients"], rule["owner_user_id"])
            if not people:
                raise HTTPException(status_code=400, detail="This rule has no valid recipients.")
        with connection:
            connection.execute(
                "UPDATE comm_rules SET status=?, updated_by=?, updated_at=?, version=version+1 WHERE id=?",
                (status, user["employee_id"], iso(utc_now()), rule_id))
            _snapshot(connection, rule_id, action, user)
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", action, f"{action.title().replace('_', ' ')}: '{rule['name']}'.",
               "comm_rule", rule_id)
    reevaluate_rule_products(rule_id)
    return get_rule(rule_id, user)


@router.post("/rules/{rule_id}/activate")
def activate_rule(rule_id: int, user: dict = Depends(requires("comms_rules"))):
    return _set_status(rule_id, user, "ACTIVE", "ACTIVATE_RULE")


@router.post("/rules/{rule_id}/disable")
def disable_rule(rule_id: int, user: dict = Depends(requires("comms_rules"))):
    return _set_status(rule_id, user, "DISABLED", "DISABLE_RULE")


@router.post("/rules/{rule_id}/duplicate")
def duplicate_rule(rule_id: int, user: dict = Depends(requires("comms_rules"))):
    connection = get_connection()
    try:
        rule = _rule_or_404(connection, rule_id)
        fields = {k: rule[k] for k in (
            "description", "priority", "effective_from", "effective_to", "scope_all", "exclusive",
            "metric", "trigger_type", "threshold_source", "threshold", "critical_threshold", "percent",
            "duration_minutes", "recovery_margin", "channel_in_app", "channel_email", "frequency",
            "repeat_hours", "max_reminders", "business_hours_only", "notify_on_critical",
            "notify_on_recovery", "escalation_policy_id")}
        now = iso(utc_now())
        fields.update(name=f"{rule['name']} (copy)"[:120], owner_user_id=user["id"], status="DRAFT",
                      created_by=user["employee_id"], created_at=now,
                      updated_by=user["employee_id"], updated_at=now)
        with connection:
            new_id = connection.execute(
                f"INSERT INTO comm_rules({','.join(fields)}) VALUES ({','.join('?' * len(fields))})",
                tuple(fields.values())).lastrowid
            connection.executemany(
                "INSERT INTO comm_rule_scopes(rule_id,scope_type,scope_value,include) VALUES (?,?,?,?)",
                [(new_id, s["scope_type"], s["scope_value"], s["include"]) for s in rule["scopes"]])
            connection.executemany(
                "INSERT INTO comm_rule_recipients(rule_id,recipient_type,recipient_value,level) VALUES (?,?,?,?)",
                [(new_id, r["recipient_type"], r["recipient_value"], r["level"]) for r in rule["recipients"]])
            _snapshot(connection, new_id, "DUPLICATE", user)
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "DUPLICATE_RULE", f"Duplicated rule '{rule['name']}' as a draft.",
               "comm_rule", new_id, metadata={"source_rule_id": rule_id})
    return get_rule(new_id, user)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, user: dict = Depends(requires("comms_rules"))):
    connection = get_connection()
    try:
        rule = _rule_or_404(connection, rule_id)
        _require_edit(user, rule)
        if connection.execute("SELECT 1 FROM alert_incidents WHERE rule_id=? LIMIT 1", (rule_id,)).fetchone():
            raise HTTPException(
                status_code=409,
                detail="This rule has alert history. Disable it instead, so the history is kept.")
        with connection:
            connection.execute("DELETE FROM comm_rules WHERE id=?", (rule_id,))
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "DELETE_RULE", f"Deleted rule '{rule['name']}'.", "comm_rule", rule_id)
    return {"status": "success"}


@router.get("/rules/{rule_id}/versions")
def rule_versions(rule_id: int, user: dict = Depends(requires("comms_view"))):
    """Saved states of a rule, newest first, each with a summary of what it did."""
    connection = get_connection()
    try:
        _rule_or_404(connection, rule_id)
        rows = connection.execute(
            "SELECT version, action, snapshot, changed_by, changed_at FROM comm_rule_versions "
            "WHERE rule_id=? ORDER BY version DESC", (rule_id,)).fetchall()
        result = []
        for row in rows:
            snap = json.loads(row["snapshot"])
            snap.setdefault("scopes", [])
            snap.setdefault("recipients", [])
            result.append({
                "version": row["version"], "action": row["action"], "changed_by": row["changed_by"],
                "changed_at": row["changed_at"], "status": snap.get("status"),
                "condition_text": _describe_condition(snap), "scope_text": _describe_scope(connection, snap),
                "threshold": snap.get("threshold"), "critical_threshold": snap.get("critical_threshold"),
                "recovery_margin": snap.get("recovery_margin"), "frequency": snap.get("frequency"),
                "recipients": snap.get("recipients"), "escalation_policy_id": snap.get("escalation_policy_id"),
            })
        return result
    finally:
        connection.close()


@router.post("/rules/simulate")
def simulate_unsaved(data: RuleIn, user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        owner_id = data.owner_user_id or user["id"]
        return alert_engine.simulate(connection, _rule_from_input(data, owner_id))
    finally:
        connection.close()


@router.post("/rules/{rule_id}/simulate")
def simulate_saved(rule_id: int, user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        return alert_engine.simulate(connection, _rule_or_404(connection, rule_id))
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# Recipient groups
# --------------------------------------------------------------------------- #
def _group(connection, group_id):
    row = connection.execute("SELECT * FROM comm_groups WHERE id=?", (group_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Group not found.")
    group = dict(row)
    group["members"] = [dict(r) | {"has_email": bool(r["email"]), "email": None} for r in connection.execute(
        """SELECT u.id, u.employee_id, u.name, u.role, u.active, u.email FROM comm_group_members m
           JOIN users u ON u.id = m.user_id WHERE m.group_id=? ORDER BY u.name""", (group_id,)).fetchall()]
    group["used_by_rules"] = connection.execute(
        "SELECT COUNT(DISTINCT rule_id) c FROM comm_rule_recipients WHERE recipient_type='GROUP' AND recipient_value=?",
        (str(group_id),)).fetchone()["c"]
    return group


@router.get("/groups")
def list_groups(user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        return [_group(connection, r["id"]) for r in connection.execute(
            "SELECT id FROM comm_groups ORDER BY name").fetchall()]
    finally:
        connection.close()


def _save_group(connection, group_id, data: GroupIn, user):
    for uid in data.user_ids:
        row = connection.execute("SELECT name, role FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail=f"User {uid} does not exist.")
        if not user_can_receive(row["role"]):
            raise HTTPException(status_code=400,
                                detail=f"{row['name']} cannot view warehouse stock, so cannot be in an alert group.")
    now = iso(utc_now())
    try:
        if group_id is None:
            group_id = connection.execute(
                "INSERT INTO comm_groups(name,description,active,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (data.name.strip(), data.description, int(data.active), user["employee_id"], now, now)).lastrowid
        else:
            connection.execute("UPDATE comm_groups SET name=?, description=?, active=?, updated_at=? WHERE id=?",
                               (data.name.strip(), data.description, int(data.active), now, group_id))
            connection.execute("DELETE FROM comm_group_members WHERE group_id=?", (group_id,))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="A group with that name already exists.")
    connection.executemany("INSERT OR IGNORE INTO comm_group_members(group_id,user_id,added_at) VALUES (?,?,?)",
                           [(group_id, uid, now) for uid in data.user_ids])
    return group_id


@router.post("/groups")
def create_group(data: GroupIn, user: dict = Depends(requires("comms_groups"))):
    connection = get_connection()
    try:
        with connection:
            group_id = _save_group(connection, None, data, user)
        result = _group(connection, group_id)
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "CREATE_GROUP", f"Created recipient group '{data.name}'.",
               "comm_group", group_id, metadata={"members": data.user_ids})
    return result


@router.put("/groups/{group_id}")
def update_group(group_id: int, data: GroupIn, user: dict = Depends(requires("comms_groups"))):
    connection = get_connection()
    try:
        before = _group(connection, group_id)
        with connection:
            _save_group(connection, group_id, data, user)
        result = _group(connection, group_id)
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "UPDATE_GROUP", f"Updated recipient group '{data.name}'.",
               "comm_group", group_id,
               metadata={"members_before": [m["id"] for m in before["members"]], "members_after": data.user_ids,
                         "active": data.active})
    return result


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, user: dict = Depends(requires("comms_groups"))):
    connection = get_connection()
    try:
        group = _group(connection, group_id)
        if group["used_by_rules"]:
            raise HTTPException(status_code=409,
                                detail="Rules still send to this group. Disable it instead, or remove it from those rules.")
        with connection:
            connection.execute("DELETE FROM comm_groups WHERE id=?", (group_id,))
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "DELETE_GROUP", f"Deleted recipient group '{group['name']}'.",
               "comm_group", group_id)
    return {"status": "success"}


@router.post("/recipients/preview")
def preview_recipients(data: RuleIn, user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        entries = [r.model_dump() for r in data.recipients]
        result = {}
        for level in ("LOW", "CRITICAL"):
            people, excluded = resolve_recipients(connection, entries, data.owner_user_id or user["id"], level)
            result[level] = {"recipients": people, "excluded": excluded}
        return result
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# Escalation policies
# --------------------------------------------------------------------------- #
def _policy(connection, policy_id):
    row = connection.execute("SELECT * FROM comm_escalation_policies WHERE id=?", (policy_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Escalation policy not found.")
    policy = dict(row)
    policy["steps"] = [dict(r) for r in connection.execute(
        "SELECT step_no, after_minutes, recipient_type, recipient_value FROM comm_escalation_steps "
        "WHERE policy_id=? ORDER BY step_no, id", (policy_id,)).fetchall()]
    policy["used_by_rules"] = connection.execute(
        "SELECT COUNT(*) c FROM comm_rules WHERE escalation_policy_id=?", (policy_id,)).fetchone()["c"]
    return policy


@router.get("/escalation-policies")
def list_policies(user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        return [_policy(connection, r["id"]) for r in connection.execute(
            "SELECT id FROM comm_escalation_policies ORDER BY name").fetchall()]
    finally:
        connection.close()


def _save_policy(connection, policy_id, data: PolicyIn, user):
    settings = load_settings(connection)
    waits = {}
    for step in data.steps:
        if step.step_no in waits and waits[step.step_no] != step.after_minutes:
            raise HTTPException(status_code=400, detail=f"All recipients of step {step.step_no} need the same wait.")
        waits[step.step_no] = step.after_minutes
        _validate_recipient_entry(connection, step, settings)
    ordered = sorted(waits.items())
    if [n for n, _ in ordered] != list(range(1, len(ordered) + 1)):
        raise HTTPException(status_code=400, detail="Steps must be numbered 1, 2, 3 … without gaps.")
    if any(b[1] <= a[1] for a, b in zip(ordered, ordered[1:])):
        raise HTTPException(status_code=400, detail="Each step must wait longer than the one before it.")
    now = iso(utc_now())
    try:
        if policy_id is None:
            policy_id = connection.execute(
                "INSERT INTO comm_escalation_policies(name,description,active,created_by,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (data.name.strip(), data.description, int(data.active), user["employee_id"], now, now)).lastrowid
        else:
            connection.execute(
                "UPDATE comm_escalation_policies SET name=?, description=?, active=?, updated_at=? WHERE id=?",
                (data.name.strip(), data.description, int(data.active), now, policy_id))
            connection.execute("DELETE FROM comm_escalation_steps WHERE policy_id=?", (policy_id,))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="A policy with that name already exists.")
    connection.executemany(
        "INSERT OR IGNORE INTO comm_escalation_steps(policy_id,step_no,after_minutes,recipient_type,recipient_value) "
        "VALUES (?,?,?,?,?)",
        [(policy_id, s.step_no, s.after_minutes, s.recipient_type,
          (s.recipient_value or "").strip().lower() if s.recipient_type == "EMAIL" else s.recipient_value)
         for s in data.steps])
    return policy_id


@router.post("/escalation-policies")
def create_policy(data: PolicyIn, user: dict = Depends(requires("comms_groups"))):
    connection = get_connection()
    try:
        with connection:
            policy_id = _save_policy(connection, None, data, user)
        result = _policy(connection, policy_id)
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "CREATE_ESCALATION_POLICY", f"Created escalation policy '{data.name}'.",
               "comm_escalation_policy", policy_id, metadata=data.model_dump())
    return result


@router.put("/escalation-policies/{policy_id}")
def update_policy(policy_id: int, data: PolicyIn, user: dict = Depends(requires("comms_groups"))):
    connection = get_connection()
    try:
        _policy(connection, policy_id)
        with connection:
            _save_policy(connection, policy_id, data, user)
        result = _policy(connection, policy_id)
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "UPDATE_ESCALATION_POLICY", f"Updated escalation policy '{data.name}'.",
               "comm_escalation_policy", policy_id, metadata=data.model_dump())
    return result


@router.delete("/escalation-policies/{policy_id}")
def delete_policy(policy_id: int, user: dict = Depends(requires("comms_groups"))):
    connection = get_connection()
    try:
        policy = _policy(connection, policy_id)
        if policy["used_by_rules"]:
            raise HTTPException(status_code=409, detail="Rules still use this policy. Disable it instead.")
        with connection:
            connection.execute("DELETE FROM comm_escalation_policies WHERE id=?", (policy_id,))
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "DELETE_ESCALATION_POLICY", f"Deleted escalation policy '{policy['name']}'.",
               "comm_escalation_policy", policy_id)
    return {"status": "success"}


# --------------------------------------------------------------------------- #
# Alerts (incidents)
# --------------------------------------------------------------------------- #
@router.get("/alerts")
def list_alerts(status: str = Query("active", pattern="^(active|resolved|all)$"),
                severity: str | None = Query(None, pattern="^(LOW|CRITICAL|OUT)$"),
                rule_id: int | None = None, product_id: int | None = None,
                category: str | None = None,
                limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                page: int | None = PageParam, page_size: int | None = Query(None, ge=1, le=MAX_PAGE_SIZE),
                user: dict = Depends(requires("comms_view"))):
    conditions, params = [], []
    if status == "active":
        conditions.append(f"i.status IN ({','.join('?' * len(ACTIVE_STATUSES))})")
        params.extend(ACTIVE_STATUSES)
    elif status == "resolved":
        conditions.append("i.status='RESOLVED'")
    for column, value in (("i.severity", severity), ("i.rule_id", rule_id),
                          ("i.product_id", product_id), ("p.category", category)):
        if value is not None:
            conditions.append(f"{column}=?")
            params.append(value)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    connection = get_connection()
    try:
        total = connection.execute(
            f"SELECT COUNT(*) c FROM alert_incidents i JOIN products p ON p.id = i.product_id {where}",
            params).fetchone()["c"]
        page, limit, offset = _page_window(total, page, page_size, limit, offset)
        rows = connection.execute(
            f"""SELECT i.*, p.product_name, p.product_code, p.category, p.unit,
                       COALESCE(m.quantity,0) AS current_quantity, r.name AS rule_name,
                       a.name AS assigned_to_name
                FROM alert_incidents i
                JOIN products p ON p.id = i.product_id
                JOIN comm_rules r ON r.id = i.rule_id
                LEFT JOIN main_inventory m ON m.product_id = i.product_id
                LEFT JOIN users a ON a.id = i.assigned_to_user_id
                {where}
                ORDER BY CASE i.severity WHEN 'OUT' THEN 0 WHEN 'CRITICAL' THEN 1 ELSE 2 END,
                         i.opened_at DESC LIMIT ? OFFSET ?""", (*params, limit, offset)).fetchall()
        return _paged({"total": total, "items": [dict(r) for r in rows]}, page, limit)
    finally:
        connection.close()


def _incident(connection, incident_id):
    row = connection.execute(
        """SELECT i.*, p.product_name, p.product_code, p.category, p.unit, p.reorder_level,
                  COALESCE(m.quantity,0) AS current_quantity, r.name AS rule_name, r.owner_user_id,
                  a.name AS assigned_to_name
           FROM alert_incidents i JOIN products p ON p.id = i.product_id
           JOIN comm_rules r ON r.id = i.rule_id
           LEFT JOIN main_inventory m ON m.product_id = i.product_id
           LEFT JOIN users a ON a.id = i.assigned_to_user_id
           WHERE i.id=?""", (incident_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found.")
    return dict(row)


def _may_act(connection, user, incident) -> bool:
    """Owner, assignee, a resolved recipient, or management may act on an alert."""
    if has(user, "comms_rules_all") or incident["owner_user_id"] == user["id"] \
            or incident.get("assigned_to_user_id") == user["id"]:
        return True
    rule = alert_engine.load_rule(connection, incident["rule_id"])
    people, _ = resolve_recipients(connection, rule["recipients"], rule["owner_user_id"])
    return any(p["user_id"] == user["id"] for p in people)


@router.get("/alerts/{incident_id}")
def get_alert(incident_id: int, user: dict = Depends(requires("comms_view"))):
    connection = get_connection()
    try:
        incident = _incident(connection, incident_id)
        incident["events"] = [dict(r) for r in connection.execute(
            "SELECT * FROM alert_events WHERE incident_id=? ORDER BY id", (incident_id,)).fetchall()]
        incident["deliveries"] = [dict(r) for r in connection.execute(
            """SELECT id, kind, recipient_name, recipient_email, status, attempts, last_error,
                      created_at, accepted_at, next_attempt_at
               FROM notification_jobs WHERE incident_id=? ORDER BY id""", (incident_id,)).fetchall()]
        incident["in_app_notifications"] = connection.execute(
            "SELECT COUNT(*) c FROM user_notifications WHERE incident_id=?", (incident_id,)).fetchone()["c"]
        incident["movements"] = [dict(r) for r in connection.execute(
            """SELECT change_qty, balance_after, reason, reference, created_by, created_at
               FROM main_inventory_txn WHERE product_id=? ORDER BY id DESC LIMIT 15""",
            (incident["product_id"],)).fetchall()]
        incident["can_act"] = _may_act(connection, user, incident)
        return incident
    finally:
        connection.close()


def _act(incident_id, user, fn):
    connection = get_connection()
    try:
        incident = _incident(connection, incident_id)
        if not _may_act(connection, user, incident):
            raise HTTPException(status_code=403, detail="Only the rule owner, its recipients or the assignee can act on this alert.")
        with alert_engine.immediate(connection):
            detail = fn(connection, incident)
    finally:
        connection.close()
    return detail


@router.post("/alerts/{incident_id}/acknowledge")
def acknowledge(incident_id: int, data: CommentIn,
                user: dict = Depends(requires_any("comms_view", "alerts_handle"))):
    def run(connection, incident):
        if incident["status"] not in ("OPEN", "ESCALATED"):
            raise HTTPException(status_code=400, detail=f"This alert is {incident['status'].lower()}.")
        now = utc_now()
        connection.execute(
            "UPDATE alert_incidents SET status='ACKNOWLEDGED', acknowledged_by=?, acknowledged_at=? WHERE id=?",
            (user["employee_id"], iso(now), incident_id))
        alert_engine.add_event(connection, incident_id, "ACKNOWLEDGED", now, incident["severity"],
                               incident["last_quantity"], user["employee_id"], data.comment)
    _act(incident_id, user, run)
    log_action(user, "COMMUNICATION", "ACKNOWLEDGE_ALERT", f"Acknowledged alert #{incident_id}.",
               "alert_incident", incident_id)
    return get_alert(incident_id, user)


@router.post("/alerts/{incident_id}/comment")
def comment(incident_id: int, data: CommentIn, user: dict = Depends(requires("comms_view"))):
    if not (data.comment or "").strip():
        raise HTTPException(status_code=400, detail="Write a comment.")

    def run(connection, incident):
        alert_engine.add_event(connection, incident_id, "COMMENT", utc_now(), None, None,
                               user["employee_id"], data.comment.strip())
    _act(incident_id, user, run)
    return get_alert(incident_id, user)


@router.post("/alerts/{incident_id}/assign")
def assign(incident_id: int, data: AssignIn, user: dict = Depends(requires("comms_view"))):
    def run(connection, incident):
        target = connection.execute("SELECT name, role, active FROM users WHERE id=?", (data.user_id,)).fetchone()
        if not target or not target["active"] or not user_can_receive(target["role"]):
            raise HTTPException(status_code=400, detail="Assign to an active user who can view warehouse stock.")
        connection.execute("UPDATE alert_incidents SET assigned_to_user_id=? WHERE id=?", (data.user_id, incident_id))
        alert_engine.add_event(connection, incident_id, "ASSIGNED", utc_now(), None, None,
                               user["employee_id"], f"Assigned to {target['name']}")
    _act(incident_id, user, run)
    log_action(user, "COMMUNICATION", "ASSIGN_ALERT", f"Assigned alert #{incident_id} to user {data.user_id}.",
               "alert_incident", incident_id)
    return get_alert(incident_id, user)


@router.post("/alerts/{incident_id}/suppress")
def suppress(incident_id: int, data: RemarksIn, user: dict = Depends(requires("comms_view"))):
    """Stop notifications for this incident. It still resolves on recovery."""
    def run(connection, incident):
        if incident["status"] in ("RESOLVED", "SUPPRESSED"):
            raise HTTPException(status_code=400, detail=f"This alert is already {incident['status'].lower()}.")
        now = utc_now()
        connection.execute("UPDATE alert_incidents SET status='SUPPRESSED', suppressed_by=?, suppressed_at=? WHERE id=?",
                           (user["employee_id"], iso(now), incident_id))
        connection.execute(
            "UPDATE notification_jobs SET status='CANCELLED', last_error='Incident suppressed', updated_at=? "
            "WHERE incident_id=? AND status IN ('PENDING','TEMP_FAILED')", (iso(now), incident_id))
        alert_engine.add_event(connection, incident_id, "SUPPRESSED", now, incident["severity"],
                               incident["last_quantity"], user["employee_id"], data.remarks)
    _act(incident_id, user, run)
    log_action(user, "COMMUNICATION", "SUPPRESS_ALERT", f"Suppressed alert #{incident_id}: {data.remarks}",
               "alert_incident", incident_id)
    return get_alert(incident_id, user)


class SnoozeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: float = Field(gt=0, le=24 * 30)
    reason: str = Field(min_length=3, max_length=500)


@router.post("/alerts/{incident_id}/snooze")
def snooze(incident_id: int, data: SnoozeIn, user: dict = Depends(requires("comms_view"))):
    """Pause reminders and escalation for a while. The incident stays open, and a
    worse stock level always ends the snooze and notifies as usual."""
    def run(connection, incident):
        settings = load_settings(connection)
        if incident["status"] not in ("OPEN", "ACKNOWLEDGED", "ESCALATED"):
            raise HTTPException(status_code=400, detail=f"This alert is {incident['status'].lower()}.")
        limit = float(settings.get("max_snooze_hours") or 72)
        if data.hours > limit:
            raise HTTPException(status_code=400, detail=f"Alerts can be snoozed for at most {limit:g} hours.")
        if incident["severity"] in ("CRITICAL", "OUT") and str(settings.get("allow_snooze_critical", "0")) != "1":
            raise HTTPException(status_code=400,
                                detail="Critical and out-of-stock alerts cannot be snoozed (Communication settings).")
        now = utc_now()
        until = now + timedelta(hours=data.hours)
        connection.execute(
            "UPDATE alert_incidents SET snoozed_until=?, snooze_reason=?, snoozed_by=? WHERE id=?",
            (iso(until), data.reason.strip(), user["employee_id"], incident_id))
        alert_engine.add_event(connection, incident_id, "SNOOZED", now, incident["severity"],
                               incident["last_quantity"], user["employee_id"],
                               f"Until {iso(until)}: {data.reason.strip()}")
    _act(incident_id, user, run)
    log_action(user, "COMMUNICATION", "SNOOZE_ALERT",
               f"Snoozed alert #{incident_id} for {data.hours:g} h: {data.reason}", "alert_incident", incident_id)
    return get_alert(incident_id, user)


@router.post("/alerts/{incident_id}/unsnooze")
def unsnooze(incident_id: int, user: dict = Depends(requires("comms_view"))):
    def run(connection, incident):
        if not incident.get("snoozed_until"):
            raise HTTPException(status_code=400, detail="This alert is not snoozed.")
        connection.execute("UPDATE alert_incidents SET snoozed_until=NULL WHERE id=?", (incident_id,))
        alert_engine.add_event(connection, incident_id, "UNSNOOZED", utc_now(), incident["severity"],
                               incident["last_quantity"], user["employee_id"], None)
    _act(incident_id, user, run)
    log_action(user, "COMMUNICATION", "UNSNOOZE_ALERT", f"Ended snooze on alert #{incident_id}.",
               "alert_incident", incident_id)
    return get_alert(incident_id, user)


# --------------------------------------------------------------------------- #
# Delivery log
# --------------------------------------------------------------------------- #
@router.get("/deliveries")
def deliveries(status: str | None = Query(None, pattern="^(PENDING|PROCESSING|ACCEPTED|TEMP_FAILED|FAILED|CANCELLED)$"),
               kind: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               page: int | None = PageParam, page_size: int | None = Query(None, ge=1, le=MAX_PAGE_SIZE),
               user: dict = Depends(requires("comms_admin"))):
    conditions, params = [], []
    if status:
        conditions.append("status=?")
        params.append(status)
    if kind:
        conditions.append("kind=?")
        params.append(kind.upper())
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    connection = get_connection()
    try:
        total = connection.execute(f"SELECT COUNT(*) c FROM notification_jobs {where}", params).fetchone()["c"]
        page, limit, offset = _page_window(total, page, page_size, limit, offset)
        rows = connection.execute(
            f"""SELECT id, kind, incident_id, rule_id, recipient_name, recipient_email, subject, status,
                       attempts, max_attempts, last_error, smtp_response, message_id, created_at,
                       accepted_at, next_attempt_at, created_by
                FROM notification_jobs {where} ORDER BY id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset)).fetchall()
        return _paged({"total": total, "items": [dict(r) for r in rows]}, page, limit)
    finally:
        connection.close()


@router.post("/deliveries/{job_id}/retry")
def retry_delivery(job_id: int, user: dict = Depends(requires("comms_admin"))):
    connection = get_connection()
    try:
        job = connection.execute("SELECT status, attempts FROM notification_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Delivery not found.")
        if job["status"] not in ("FAILED", "TEMP_FAILED", "CANCELLED"):
            raise HTTPException(status_code=400, detail=f"A {job['status'].lower()} delivery cannot be retried.")
        smtp = connection.execute("SELECT max_attempts FROM smtp_settings WHERE id=1").fetchone()
        now = iso(utc_now())
        with connection:
            connection.execute(
                "UPDATE notification_jobs SET status='PENDING', next_attempt_at=?, max_attempts=?, "
                "last_error=NULL, updated_at=? WHERE id=?",
                (now, job["attempts"] + int(smtp["max_attempts"] if smtp else 6), now, job_id))
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "RETRY_DELIVERY", f"Re-queued delivery #{job_id}.", "notification_job", job_id)
    return {"status": "queued", "id": job_id}


@router.post("/deliveries/{job_id}/cancel")
def cancel_delivery(job_id: int, user: dict = Depends(requires("comms_admin"))):
    connection = get_connection()
    try:
        with connection:
            changed = connection.execute(
                "UPDATE notification_jobs SET status='CANCELLED', last_error='Cancelled by administrator', "
                "updated_at=? WHERE id=? AND status IN ('PENDING','TEMP_FAILED')",
                (iso(utc_now()), job_id)).rowcount
    finally:
        connection.close()
    if not changed:
        raise HTTPException(status_code=400, detail="Only pending or retrying deliveries can be cancelled.")
    log_action(user, "COMMUNICATION", "CANCEL_DELIVERY", f"Cancelled delivery #{job_id}.", "notification_job", job_id)
    return {"status": "cancelled", "id": job_id}


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
SAMPLE_CONTEXT = {
    "item_name": "Pringles Original 40g", "item_code": "SNK-001", "category_name": "Snacks",
    "warehouse_name": alert_engine.WAREHOUSE_NAME, "current_stock": "4", "available_stock": "4",
    "reorder_level": "20", "configured_threshold": "5", "critical_threshold": "2", "unit": "pcs",
    "recipient_name": "Inventory Officer", "alert_severity": "low", "detected_at": "2026-10-10 09:15 Asia/Kathmandu",
    "rule_name": "Snacks low stock", "incident_id": "123", "inventory_link": "", "escalation_step": "1",
    "digest_lines": "- Pringles Original 40g (SNK-001): 4 pcs [low] incident #123",
    "sent_at": "2026-10-10 09:15 Asia/Kathmandu",
}


@router.get("/templates")
def list_templates(user: dict = Depends(requires("comms_templates"))):
    connection = get_connection()
    try:
        return {
            "templates": [dict(r) for r in connection.execute(
                "SELECT key, subject, body, version, updated_by, updated_at FROM email_templates ORDER BY key").fetchall()],
            "placeholders": sorted(TEMPLATE_PLACEHOLDERS),
        }
    finally:
        connection.close()


@router.put("/templates/{key}")
def update_template(key: str, data: TemplateIn, user: dict = Depends(requires("comms_templates"))):
    if key not in DEFAULT_TEMPLATES:
        raise HTTPException(status_code=404, detail="Template not found.")
    unknown = unknown_placeholders(data.subject, data.body)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown placeholders: {', '.join(sorted(unknown))}.")
    if "\n" in data.subject or "\r" in data.subject:
        raise HTTPException(status_code=400, detail="The subject must be a single line.")
    connection = get_connection()
    try:
        now = iso(utc_now())
        with connection:
            version = connection.execute("SELECT version FROM email_templates WHERE key=?", (key,)).fetchone()["version"] + 1
            connection.execute("UPDATE email_templates SET subject=?, body=?, version=?, updated_by=?, updated_at=? WHERE key=?",
                               (data.subject, data.body, version, user["employee_id"], now, key))
            connection.execute(
                "INSERT INTO email_template_versions(template_key,version,subject,body,updated_by,updated_at) "
                "VALUES (?,?,?,?,?,?)", (key, version, data.subject, data.body, user["employee_id"], now))
    finally:
        connection.close()
    log_action(user, "COMMUNICATION", "UPDATE_TEMPLATE", f"Updated email template {key} to version {version}.",
               "email_template", key)
    return {"key": key, "version": version}


@router.post("/templates/{key}/preview")
def preview_template(key: str, data: TemplateIn, user: dict = Depends(requires("comms_templates"))):
    unknown = unknown_placeholders(data.subject, data.body)
    return {"subject": render_text(data.subject, SAMPLE_CONTEXT), "body": render_text(data.body, SAMPLE_CONTEXT),
            "unknown_placeholders": sorted(unknown)}


@router.get("/templates/{key}/versions")
def template_versions(key: str, user: dict = Depends(requires("comms_templates"))):
    connection = get_connection()
    try:
        return [dict(r) for r in connection.execute(
            "SELECT version, subject, body, updated_by, updated_at FROM email_template_versions "
            "WHERE template_key=? ORDER BY version DESC", (key,)).fetchall()]
    finally:
        connection.close()


@router.post("/templates/{key}/restore/{version}")
def restore_template(key: str, version: int, user: dict = Depends(requires("comms_templates"))):
    connection = get_connection()
    try:
        row = connection.execute("SELECT subject, body FROM email_template_versions WHERE template_key=? AND version=?",
                                 (key, version)).fetchone()
    finally:
        connection.close()
    if not row:
        raise HTTPException(status_code=404, detail="Version not found.")
    return update_template(key, TemplateIn(subject=row["subject"], body=row["body"]), user)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@router.get("/settings")
def get_settings(user: dict = Depends(requires_any("comms_view", "comms_admin"))):
    connection = get_connection()
    try:
        settings = load_settings(connection)
    finally:
        connection.close()
    return {
        "timezone": settings["timezone"], "business_start": settings["business_start"],
        "business_end": settings["business_end"],
        "business_days": sorted(int(d) for d in settings["business_days"].split(",") if d.strip()),
        "digest_time": settings["digest_time"], "digest_weekday": int(settings["digest_weekday"]),
        "external_email_domains": sorted(allowed_email_domains(settings)),
        "sweep_minutes": int(settings["sweep_minutes"]),
        "last_sweep_at": settings.get("_last_sweep_at"),
        "alerts_enabled": alert_engine.alerts_enabled(settings),
        "max_snooze_hours": int(float(settings.get("max_snooze_hours") or 72)),
        "allow_snooze_critical": str(settings.get("allow_snooze_critical", "0")) == "1",
        "critical_ignores_business_hours": str(settings.get("critical_ignores_business_hours", "0")) == "1",
    }


@router.put("/settings")
def update_settings(data: SettingsIn, user: dict = Depends(requires("comms_admin"))):
    tz = get_tz(data.timezone)
    if getattr(tz, "key", None) != data.timezone and str(tz) != data.timezone:
        raise HTTPException(status_code=400, detail=f"Unknown timezone {data.timezone}.")
    for label, value in (("Business start", data.business_start), ("Business end", data.business_end),
                         ("Digest time", data.digest_time)):
        hours, minutes = (int(x) for x in value.split(":"))
        if hours > 23 or minutes > 59:
            raise HTTPException(status_code=400, detail=f"{label} is not a valid time.")
    if data.business_start >= data.business_end:
        raise HTTPException(status_code=400, detail="Business hours must end after they start.")
    if any(d < 0 or d > 6 for d in data.business_days):
        raise HTTPException(status_code=400, detail="Business days are 0 (Monday) to 6 (Sunday).")
    domains = []
    for domain in data.external_email_domains:
        domain = domain.strip().lower().lstrip("@")
        if domain and not EMAIL_RE.match(f"x@{domain}"):
            raise HTTPException(status_code=400, detail=f"{domain} is not a valid domain.")
        if domain:
            domains.append(domain)
    values = {
        "timezone": data.timezone, "business_start": data.business_start, "business_end": data.business_end,
        "business_days": ",".join(str(d) for d in sorted(set(data.business_days))),
        "digest_time": data.digest_time, "digest_weekday": str(data.digest_weekday),
        "external_email_domains": ",".join(sorted(set(domains))), "sweep_minutes": str(data.sweep_minutes),
    }
    for key in ("alerts_enabled", "allow_snooze_critical", "critical_ignores_business_hours"):
        if getattr(data, key) is not None:
            values[key] = "1" if getattr(data, key) else "0"
    if data.max_snooze_hours is not None:
        values["max_snooze_hours"] = str(data.max_snooze_hours)
    connection = get_connection()
    try:
        before = load_settings(connection)
        now = iso(utc_now())
        with connection:
            for key, value in values.items():
                connection.execute(
                    "INSERT INTO app_settings(key,value,updated_by,updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, "
                    "updated_at=excluded.updated_at", (key, value, user["employee_id"], now))
    finally:
        connection.close()
    changed = {k: {"from": before.get(k), "to": v} for k, v in values.items() if before.get(k) != v}
    log_action(user, "COMMUNICATION", "UPDATE_SETTINGS", f"Updated communication settings ({', '.join(changed) or 'no changes'}).",
               "app_settings", None, metadata=changed)
    return get_settings(user)
