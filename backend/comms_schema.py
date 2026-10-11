"""Schema, defaults and migrations for SMTP settings and the Communication Manager.

Kept apart from database.py so the stock-alert feature can be read, reviewed
and migrated on its own. Every statement is idempotent: running it against an
existing obs.db adds what is missing and never touches existing rows.

All timestamps in these tables are UTC, ISO-8601 with a trailing "Z".
"""

COMMS_SCHEMA = """
-- Generic key/value settings (timezone, business hours, digest time, ...).
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_by TEXT,
    updated_at TEXT
);

-- Single-row SMTP configuration. The password is encrypted at rest and is
-- never returned by the API.
CREATE TABLE IF NOT EXISTS smtp_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    host TEXT,
    port INTEGER NOT NULL DEFAULT 587,
    security TEXT NOT NULL DEFAULT 'starttls'
        CHECK (security IN ('starttls','ssl','none')),
    auth_enabled INTEGER NOT NULL DEFAULT 1,
    username TEXT,
    password_enc TEXT,
    sender_email TEXT,
    sender_name TEXT DEFAULT 'Himalaya Airlines OBS',
    reply_to TEXT,
    timeout_seconds INTEGER NOT NULL DEFAULT 20,
    max_attempts INTEGER NOT NULL DEFAULT 6,
    rate_limit_per_minute INTEGER NOT NULL DEFAULT 30,
    updated_by TEXT,
    updated_at TEXT
);

-- Editable, versioned email templates. Only whitelisted {{placeholders}}.
CREATE TABLE IF NOT EXISTS email_templates (
    key TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS email_template_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    updated_by TEXT,
    updated_at TEXT,
    UNIQUE (template_key, version),
    FOREIGN KEY (template_key) REFERENCES email_templates(key) ON DELETE CASCADE
);

-- Recipient groups are independent of login roles.
CREATE TABLE IF NOT EXISTS comm_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS comm_group_members (
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    added_at TEXT,
    PRIMARY KEY (group_id, user_id),
    FOREIGN KEY (group_id) REFERENCES comm_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS comm_escalation_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at TEXT,
    updated_at TEXT
);

-- Step N fires when an incident has been open and unacknowledged for
-- after_minutes. Recipient columns mirror comm_rule_recipients.
CREATE TABLE IF NOT EXISTS comm_escalation_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id INTEGER NOT NULL,
    step_no INTEGER NOT NULL,
    after_minutes INTEGER NOT NULL CHECK (after_minutes > 0),
    recipient_type TEXT NOT NULL CHECK (recipient_type IN ('USER','ROLE','GROUP','OWNER','EMAIL')),
    recipient_value TEXT,
    UNIQUE (policy_id, step_no, recipient_type, recipient_value),
    FOREIGN KEY (policy_id) REFERENCES comm_escalation_policies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS comm_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    owner_user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','ACTIVE','DISABLED')),
    priority INTEGER NOT NULL DEFAULT 100,
    effective_from TEXT,
    effective_to TEXT,
    -- Only true when the creator deliberately chose "every product".
    scope_all INTEGER NOT NULL DEFAULT 0,
    -- When set, this rule's explicitly listed products are not evaluated by
    -- other rules that reach them only through a category or "every product".
    exclusive INTEGER NOT NULL DEFAULT 0,
    -- The only authoritative stock figure in this system is warehouse on-hand.
    metric TEXT NOT NULL DEFAULT 'WAREHOUSE_ON_HAND' CHECK (metric IN ('WAREHOUSE_ON_HAND')),
    trigger_type TEXT NOT NULL CHECK (trigger_type IN
        ('BELOW','AT_OR_BELOW','OUT_OF_STOCK','PERCENT_OF_REORDER','BELOW_FOR_DURATION')),
    threshold_source TEXT NOT NULL DEFAULT 'FIXED' CHECK (threshold_source IN ('FIXED','REORDER_LEVEL')),
    threshold REAL,
    critical_threshold REAL,
    percent REAL,
    duration_minutes INTEGER,
    recovery_margin REAL NOT NULL DEFAULT 0 CHECK (recovery_margin >= 0),
    channel_in_app INTEGER NOT NULL DEFAULT 1,
    channel_email INTEGER NOT NULL DEFAULT 1,
    frequency TEXT NOT NULL DEFAULT 'ONCE' CHECK (frequency IN
        ('ONCE','REPEAT_HOURS','DAILY','DAILY_DIGEST','WEEKLY_DIGEST')),
    repeat_hours INTEGER,
    max_reminders INTEGER NOT NULL DEFAULT 10 CHECK (max_reminders BETWEEN 0 AND 100),
    business_hours_only INTEGER NOT NULL DEFAULT 0,
    notify_on_critical INTEGER NOT NULL DEFAULT 1,
    notify_on_recovery INTEGER NOT NULL DEFAULT 0,
    escalation_policy_id INTEGER,
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at TEXT,
    updated_by TEXT,
    updated_at TEXT,
    FOREIGN KEY (owner_user_id) REFERENCES users(id),
    FOREIGN KEY (escalation_policy_id) REFERENCES comm_escalation_policies(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS comm_rule_scopes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('PRODUCT','CATEGORY')),
    scope_value TEXT NOT NULL,
    include INTEGER NOT NULL DEFAULT 1,
    UNIQUE (rule_id, scope_type, scope_value),
    FOREIGN KEY (rule_id) REFERENCES comm_rules(id) ON DELETE CASCADE
);

-- level: ALL = every notification, LOW = low-severity only,
-- CRITICAL = critical and out-of-stock only.
CREATE TABLE IF NOT EXISTS comm_rule_recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL,
    recipient_type TEXT NOT NULL CHECK (recipient_type IN ('USER','ROLE','GROUP','OWNER','EMAIL')),
    recipient_value TEXT,
    level TEXT NOT NULL DEFAULT 'ALL' CHECK (level IN ('ALL','LOW','CRITICAL')),
    UNIQUE (rule_id, recipient_type, recipient_value, level),
    FOREIGN KEY (rule_id) REFERENCES comm_rules(id) ON DELETE CASCADE
);

-- Transactional outbox: written in the same transaction as the stock change
-- (see routers/warehouse.move_stock), so a committed movement is never missed.
CREATE TABLE IF NOT EXISTS stock_change_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    reference TEXT,
    created_at TEXT NOT NULL,
    processed_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS alert_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('OPEN','ACKNOWLEDGED','ESCALATED','SUPPRESSED','RESOLVED')),
    severity TEXT NOT NULL CHECK (severity IN ('LOW','CRITICAL','OUT')),
    opened_at TEXT NOT NULL,
    breach_started_at TEXT,
    last_evaluated_at TEXT,
    opening_quantity REAL,
    last_quantity REAL,
    threshold_at_open REAL,
    last_notified_at TEXT,
    reminders_sent INTEGER NOT NULL DEFAULT 0,
    escalation_step INTEGER NOT NULL DEFAULT 0,
    acknowledged_by TEXT,
    acknowledged_at TEXT,
    assigned_to_user_id INTEGER,
    suppressed_by TEXT,
    suppressed_at TEXT,
    resolved_at TEXT,
    resolved_by TEXT,
    resolution TEXT,
    FOREIGN KEY (rule_id) REFERENCES comm_rules(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    FOREIGN KEY (assigned_to_user_id) REFERENCES users(id) ON DELETE SET NULL
);

-- At most one live incident per rule and product, whichever worker gets there first.
CREATE UNIQUE INDEX IF NOT EXISTS ux_incident_active
    ON alert_incidents(rule_id, product_id)
    WHERE status IN ('OPEN','ACKNOWLEDGED','ESCALATED','SUPPRESSED');
CREATE INDEX IF NOT EXISTS idx_incident_status ON alert_incidents(status);
CREATE INDEX IF NOT EXISTS idx_incident_product ON alert_incidents(product_id);

-- Breaches that must persist for a while before they count (BELOW_FOR_DURATION).
CREATE TABLE IF NOT EXISTS alert_breach_watch (
    rule_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    since TEXT NOT NULL,
    PRIMARY KEY (rule_id, product_id),
    FOREIGN KEY (rule_id) REFERENCES comm_rules(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT,
    quantity REAL,
    actor TEXT,
    detail TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES alert_incidents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_alert_events_incident ON alert_events(incident_id);

-- Durable delivery queue. Delivery state is separate from incident state.
-- ACCEPTED means the SMTP server took the message, not that it reached an inbox.
CREATE TABLE IF NOT EXISTS notification_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('ALERT','REMINDER','ESCALATION','RECOVERY','DIGEST','TEST')),
    channel TEXT NOT NULL DEFAULT 'EMAIL' CHECK (channel IN ('EMAIL')),
    incident_id INTEGER,
    rule_id INTEGER,
    user_id INTEGER,
    recipient_email TEXT NOT NULL,
    recipient_name TEXT,
    subject TEXT NOT NULL,
    body_text TEXT NOT NULL,
    body_html TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN
        ('PENDING','PROCESSING','ACCEPTED','TEMP_FAILED','FAILED','CANCELLED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 6,
    next_attempt_at TEXT NOT NULL,
    locked_until TEXT,
    lock_owner TEXT,
    last_error TEXT,
    smtp_response TEXT,
    message_id TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    accepted_at TEXT,
    FOREIGN KEY (incident_id) REFERENCES alert_incidents(id) ON DELETE SET NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON notification_jobs(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_jobs_incident ON notification_jobs(incident_id);

CREATE TABLE IF NOT EXISTS user_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    incident_id INTEGER,
    product_id INTEGER,
    kind TEXT NOT NULL,
    severity TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    read_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (incident_id) REFERENCES alert_incidents(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_user_notifications ON user_notifications(user_id, read_at);

-- Cross-process lease so only one scheduler runs the periodic sweep at a time.
CREATE TABLE IF NOT EXISTS worker_leases (
    name TEXT PRIMARY KEY,
    owner TEXT,
    expires_at TEXT
);
"""

DEFAULT_SETTINGS = {
    # Nepal has no daylight saving, so this is a fixed +05:45 offset.
    "timezone": "Asia/Kathmandu",
    "business_start": "09:00",
    "business_end": "18:00",
    # Python weekday numbers, Monday = 0. Nepal's working week is Sunday-Friday.
    "business_days": "6,0,1,2,3,4",
    "digest_time": "09:00",
    "digest_weekday": "6",
    # Comma-separated domains that explicit EMAIL recipients may use. Empty
    # means rules can only address registered users.
    "external_email_domains": "",
    "sweep_minutes": "5",
}

TEMPLATE_PLACEHOLDERS = {
    "item_name", "item_code", "category_name", "warehouse_name", "current_stock",
    "available_stock", "reorder_level", "configured_threshold", "critical_threshold",
    "unit", "recipient_name", "alert_severity", "detected_at", "rule_name",
    "incident_id", "inventory_link", "escalation_step", "digest_lines", "sent_at",
}

DEFAULT_TEMPLATES = {
    "low_stock": (
        "[OBS] Low stock: {{item_name}} ({{current_stock}} {{unit}})",
        "Hello {{recipient_name}},\n\n"
        "{{item_name}} ({{item_code}}) in {{warehouse_name}} is low.\n\n"
        "Current stock: {{current_stock}} {{unit}}\n"
        "Alert threshold: {{configured_threshold}}\n"
        "Reorder level: {{reorder_level}}\n"
        "Rule: {{rule_name}}\n"
        "Detected: {{detected_at}}\n\n"
        "Open the OBS app, Comms > Alerts, incident #{{incident_id}} to acknowledge it.\n",
    ),
    "critical_stock": (
        "[OBS] CRITICAL stock: {{item_name}} ({{current_stock}} {{unit}})",
        "Hello {{recipient_name}},\n\n"
        "{{item_name}} ({{item_code}}) in {{warehouse_name}} has reached a critical level.\n\n"
        "Current stock: {{current_stock}} {{unit}}\n"
        "Critical threshold: {{critical_threshold}}\n"
        "Rule: {{rule_name}}\n"
        "Detected: {{detected_at}}\n\n"
        "Incident #{{incident_id}}.\n",
    ),
    "out_of_stock": (
        "[OBS] OUT OF STOCK: {{item_name}}",
        "Hello {{recipient_name}},\n\n"
        "{{item_name}} ({{item_code}}) is out of stock in {{warehouse_name}}.\n\n"
        "Rule: {{rule_name}}\nDetected: {{detected_at}}\nIncident #{{incident_id}}.\n",
    ),
    "reminder": (
        "[OBS] Reminder: {{item_name}} still {{alert_severity}} ({{current_stock}} {{unit}})",
        "Hello {{recipient_name}},\n\n"
        "{{item_name}} ({{item_code}}) is still below its threshold.\n\n"
        "Current stock: {{current_stock}} {{unit}}\nThreshold: {{configured_threshold}}\n"
        "Rule: {{rule_name}}\nIncident #{{incident_id}} has been open since {{detected_at}}.\n",
    ),
    "escalation": (
        "[OBS] ESCALATION {{escalation_step}}: {{item_name}} unacknowledged",
        "Hello {{recipient_name}},\n\n"
        "Incident #{{incident_id}} for {{item_name}} ({{item_code}}) has not been acknowledged.\n\n"
        "Severity: {{alert_severity}}\nCurrent stock: {{current_stock}} {{unit}}\n"
        "Threshold: {{configured_threshold}}\nOpen since: {{detected_at}}\nRule: {{rule_name}}\n",
    ),
    "recovery": (
        "[OBS] Resolved: {{item_name}} back to {{current_stock}} {{unit}}",
        "Hello {{recipient_name}},\n\n"
        "Stock of {{item_name}} ({{item_code}}) has recovered to {{current_stock}} {{unit}}.\n"
        "Incident #{{incident_id}} (rule {{rule_name}}) is resolved.\n",
    ),
    "digest": (
        "[OBS] Stock alert digest: {{rule_name}}",
        "Hello {{recipient_name}},\n\n"
        "These items are below their alert thresholds:\n\n{{digest_lines}}\n\n"
        "Generated {{sent_at}}.\n",
    ),
    "smtp_test": (
        "[OBS] SMTP test message",
        "Hello {{recipient_name}},\n\n"
        "This is a test message from the Himalaya Airlines OBS system, sent {{sent_at}}.\n"
        "If you can read it, outgoing email is configured correctly.\n",
    ),
}


def initialize_comms(connection, add_column):
    """Create the communication tables and seed defaults. Never overwrites edits."""
    add_column(connection, "users", "email", "TEXT")
    connection.executescript(COMMS_SCHEMA)
    connection.execute("INSERT OR IGNORE INTO smtp_settings(id) VALUES (1)")
    for key, value in DEFAULT_SETTINGS.items():
        connection.execute(
            "INSERT OR IGNORE INTO app_settings(key,value,updated_by) VALUES (?,?,'SYSTEM')",
            (key, value),
        )
    for key, (subject, body) in DEFAULT_TEMPLATES.items():
        inserted = connection.execute(
            "INSERT OR IGNORE INTO email_templates(key,subject,body,version,updated_by) "
            "VALUES (?,?,?,1,'SYSTEM')",
            (key, subject, body),
        ).rowcount
        if inserted:
            connection.execute(
                "INSERT OR IGNORE INTO email_template_versions"
                "(template_key,version,subject,body,updated_by) VALUES (?,1,?,?,'SYSTEM')",
                (key, subject, body),
            )
