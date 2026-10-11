"""Shared helpers for the Communication Manager.

Covers UTC time, the organisation's timezone and business hours, settings,
safe template rendering and recipient resolution. Nothing here sends anything.
"""

from datetime import datetime, time, timedelta, timezone
import html
import re

from database import ROLE_PERMISSIONS
from comms_schema import DEFAULT_SETTINGS, TEMPLATE_PLACEHOLDERS

SEVERITY_RANK = {"LOW": 1, "CRITICAL": 2, "OUT": 3}
ACTIVE_STATUSES = ("OPEN", "ACKNOWLEDGED", "ESCALATED", "SUPPRESSED")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# A recipient must be allowed to see warehouse stock, or the message itself
# would leak data the person cannot see in the app.
RECIPIENT_PERMISSION = "warehouse"


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #
def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_tz(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - Windows without tzdata, or a bad name
        if name == "Asia/Kathmandu":
            return timezone(timedelta(hours=5, minutes=45), "Asia/Kathmandu")
        return timezone.utc


def _hhmm(value: str, fallback: str) -> time:
    try:
        hours, minutes = (value or fallback).split(":")
        return time(int(hours), int(minutes))
    except (ValueError, AttributeError):
        hours, minutes = fallback.split(":")
        return time(int(hours), int(minutes))


def business_days(settings: dict) -> set[int]:
    try:
        return {int(d) for d in str(settings.get("business_days", "")).split(",") if d.strip() != ""}
    except ValueError:
        return {6, 0, 1, 2, 3, 4}


def is_business_time(moment: datetime, settings: dict) -> bool:
    tz = get_tz(settings.get("timezone") or DEFAULT_SETTINGS["timezone"])
    local = moment.astimezone(tz)
    start = _hhmm(settings.get("business_start"), "09:00")
    end = _hhmm(settings.get("business_end"), "18:00")
    return local.weekday() in business_days(settings) and start <= local.time() < end


def next_business_start(moment: datetime, settings: dict) -> datetime:
    """The next moment inside business hours (or `moment` itself if already inside)."""
    if is_business_time(moment, settings):
        return moment
    tz = get_tz(settings.get("timezone") or DEFAULT_SETTINGS["timezone"])
    start = _hhmm(settings.get("business_start"), "09:00")
    days = business_days(settings) or {0, 1, 2, 3, 4, 5, 6}
    local = moment.astimezone(tz)
    for offset in range(0, 8):
        day = (local + timedelta(days=offset)).date()
        candidate = datetime.combine(day, start, tzinfo=tz)
        if day.weekday() in days and candidate > local:
            return candidate.astimezone(timezone.utc)
    return moment


def local_date(moment: datetime, settings: dict) -> str:
    tz = get_tz(settings.get("timezone") or DEFAULT_SETTINGS["timezone"])
    return moment.astimezone(tz).date().isoformat()


def local_display(value: str | datetime | None, settings: dict) -> str:
    moment = parse_iso(value) if isinstance(value, str) else value
    if not moment:
        return ""
    name = settings.get("timezone") or DEFAULT_SETTINGS["timezone"]
    return moment.astimezone(get_tz(name)).strftime("%Y-%m-%d %H:%M ") + name


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def load_settings(connection) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    for row in connection.execute("SELECT key, value FROM app_settings").fetchall():
        settings[row["key"]] = row["value"]
    return settings


def allowed_email_domains(settings: dict) -> set[str]:
    return {
        d.strip().lower().lstrip("@")
        for d in str(settings.get("external_email_domains") or "").split(",")
        if d.strip()
    }


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
def unknown_placeholders(*texts: str) -> set[str]:
    found = set()
    for text in texts:
        found.update(PLACEHOLDER_RE.findall(text or ""))
    return found - TEMPLATE_PLACEHOLDERS


def render_text(template: str, context: dict) -> str:
    """Plain substitution of whitelisted names. No expressions, no code."""
    def replace(match):
        name = match.group(1)
        if name not in TEMPLATE_PLACEHOLDERS:
            return match.group(0)
        value = context.get(name)
        return "" if value is None else str(value)
    return PLACEHOLDER_RE.sub(replace, template or "")


def render_email(connection, key: str, context: dict) -> tuple[str, str, str]:
    """Return (subject, text, html) for a stored template."""
    row = connection.execute(
        "SELECT subject, body FROM email_templates WHERE key=?", (key,)
    ).fetchone()
    if not row:
        raise KeyError(f"Missing email template {key}")
    subject = " ".join(render_text(row["subject"], context).split())[:250]
    text = render_text(row["body"], context)
    body_html = (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;'
        'white-space:pre-wrap;color:#16202B">' + html.escape(text) + "</div>"
    )
    return subject, text, body_html


def format_qty(value) -> str:
    if value is None:
        return ""
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


# --------------------------------------------------------------------------- #
# Recipients
# --------------------------------------------------------------------------- #
def user_can_receive(role: str) -> bool:
    return RECIPIENT_PERMISSION in ROLE_PERMISSIONS.get(role, set())


def _level_matches(level: str, severity: str | None) -> bool:
    if severity is None or level == "ALL":
        return True
    if level == "LOW":
        return severity == "LOW"
    return severity in ("CRITICAL", "OUT")


def resolve_recipients(connection, entries, owner_user_id, severity=None, settings=None):
    """Expand recipient entries into deduplicated people.

    Returns (recipients, excluded). Each recipient carries the user id (None for
    an explicit address), name, email and the entries that selected them, so
    the preview can explain why someone is on the list.
    """
    settings = settings or load_settings(connection)
    domains = allowed_email_domains(settings)
    recipients: dict[str, dict] = {}
    excluded: list[dict] = []

    def consider_user(row, via):
        if not row:
            return
        label = f"{row['name']} ({row['employee_id']})"
        if not row["active"]:
            excluded.append({"who": label, "reason": "Account is inactive.", "via": via})
            return
        if not user_can_receive(row["role"]):
            excluded.append({
                "who": label,
                "reason": "Their role cannot view warehouse stock, so they may not receive stock alerts.",
                "via": via,
            })
            return
        key = f"user:{row['id']}"
        entry = recipients.setdefault(key, {
            "key": key, "user_id": row["id"], "name": row["name"],
            "employee_id": row["employee_id"], "role": row["role"],
            "email": (row["email"] or "").strip() or None, "via": [],
        })
        if via not in entry["via"]:
            entry["via"].append(via)

    user_columns = "id, employee_id, name, role, active, email"
    for item in entries:
        kind = item["recipient_type"]
        value = item.get("recipient_value")
        level = item.get("level") or "ALL"
        if not _level_matches(level, severity):
            continue
        via = kind if kind == "OWNER" else f"{kind}:{value}"
        if kind == "OWNER":
            consider_user(connection.execute(
                f"SELECT {user_columns} FROM users WHERE id=?", (owner_user_id,)).fetchone(), via)
        elif kind == "USER":
            consider_user(connection.execute(
                f"SELECT {user_columns} FROM users WHERE id=?", (int(value),)).fetchone(), via)
        elif kind == "ROLE":
            for row in connection.execute(
                    f"SELECT {user_columns} FROM users WHERE role=?", (value,)).fetchall():
                consider_user(row, via)
        elif kind == "GROUP":
            group = connection.execute(
                "SELECT id, name, active FROM comm_groups WHERE id=?", (int(value),)).fetchone()
            if not group or not group["active"]:
                excluded.append({"who": f"Group {value}", "reason": "Group is disabled or deleted.", "via": via})
                continue
            via = f"GROUP:{group['name']}"
            for row in connection.execute(
                f"""SELECT u.id, u.employee_id, u.name, u.role, u.active, u.email
                    FROM comm_group_members m JOIN users u ON u.id = m.user_id
                    WHERE m.group_id=?""", (group["id"],)).fetchall():
                consider_user(row, via)
        elif kind == "EMAIL":
            address = (value or "").strip().lower()
            domain = address.rsplit("@", 1)[-1] if "@" in address else ""
            if not EMAIL_RE.match(address) or domain not in domains:
                excluded.append({
                    "who": address,
                    "reason": "External address not permitted by the allowed-domain policy.",
                    "via": via,
                })
                continue
            key = f"email:{address}"
            recipients.setdefault(key, {
                "key": key, "user_id": None, "name": address, "employee_id": None,
                "role": None, "email": address, "via": [via],
            })

    # An explicit address that belongs to a registered user collapses into that user.
    by_email = {r["email"].lower(): k for k, r in recipients.items() if r["user_id"] and r["email"]}
    for key in [k for k, r in recipients.items() if r["user_id"] is None]:
        owner_key = by_email.get(recipients[key]["email"])
        if owner_key:
            recipients[owner_key]["via"].extend(recipients[key]["via"])
            del recipients[key]

    return list(recipients.values()), excluded
