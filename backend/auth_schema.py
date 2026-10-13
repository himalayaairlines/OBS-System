"""Accounts and sign-in security tables (API 7.5).

Adds database-backed sessions, single-use e-mail tokens (password reset,
invitation, sign-in verification code), per-user two-factor and lock state,
and the client address on audit records. Everything is additive: existing
users, passwords and audit rows are kept.
"""

AUTH_SCHEMA = """
-- One row per signed-in device. The token itself is never stored, only its
-- SHA-256, so a copy of the database cannot be used to sign in.
CREATE TABLE IF NOT EXISTS user_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    idle_minutes INTEGER,
    ip_address TEXT,
    user_agent TEXT,
    device TEXT,
    two_factor INTEGER NOT NULL DEFAULT 0,
    revoked_at TEXT,
    revoked_reason TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id, revoked_at);

-- Single-use secrets sent by e-mail. Only hashes are stored.
--   RESET   password reset link (30 minutes)
--   INVITE  invitation to set the first password (72 hours)
--   OTP     6-digit sign-in code (5 minutes); `challenge` is what the app holds
CREATE TABLE IF NOT EXISTS auth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose TEXT NOT NULL CHECK (purpose IN ('RESET','INVITE','OTP')),
    token_hash TEXT NOT NULL,
    challenge TEXT UNIQUE,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    sends INTEGER NOT NULL DEFAULT 1,
    last_sent_at TEXT,
    ip_address TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash ON auth_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id, purpose);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(timestamp);
"""

USER_COLUMNS = (
    ("email_verified", "INTEGER NOT NULL DEFAULT 0"),
    ("two_factor", "INTEGER NOT NULL DEFAULT 0"),
    ("password_changed_at", "TEXT"),
    ("must_change_password", "INTEGER NOT NULL DEFAULT 0"),
    ("last_login_at", "TEXT"),
    ("last_login_ip", "TEXT"),
    # AUTO = too many wrong passwords (expires), ADMIN = locked by an administrator.
    ("lock_reason", "TEXT"),
    ("invited_at", "TEXT"),
    ("invited_by", "TEXT"),
    ("is_demo", "INTEGER NOT NULL DEFAULT 0"),
)

AUTH_SETTINGS = {
    # Ground staff: signed out after this long without activity, and after
    # the absolute limit whatever happens.
    "session_idle_minutes": "60",
    "session_hours": "12",
    # OBS iPad accounts work offline in the air for hours, so they get a longer
    # absolute limit and no idle timeout.
    "device_session_hours": "24",
    # Weak or default passwords must be changed at the next sign-in.
    "require_strong_passwords": "1",
    # Wrong passwords before a temporary lock, and how long it lasts.
    "lockout_attempts": "5",
    "lockout_minutes": "15",
    # Address of the web app, used in links inside e-mails. Never taken from
    # the request, so a forged Host header cannot redirect a reset link.
    "app_url": "",
}

# A value that can never match any password: invited accounts have no password yet.
UNUSABLE_PASSWORD = "pbkdf2$1$00$unusable"


def _rebuild_notification_jobs(connection):
    """Allow the SECURITY job kind (sign-in codes, reset links, notices)."""
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='notification_jobs'").fetchone()
    if not row or "'SECURITY'" in (row[0] or ""):
        return
    new_sql = row[0].replace("'DIGEST','TEST')", "'DIGEST','TEST','SECURITY')")
    if new_sql == row[0]:
        return
    connection.execute("ALTER TABLE notification_jobs RENAME TO notification_jobs_old")
    connection.execute(new_sql)
    connection.execute("INSERT INTO notification_jobs SELECT * FROM notification_jobs_old")
    connection.execute("DROP TABLE notification_jobs_old")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_due ON notification_jobs(status, next_attempt_at)")


def initialize_auth(connection, add_column, demo_ids=()):
    for column, kind in USER_COLUMNS:
        add_column(connection, "users", column, kind)
    add_column(connection, "audit_logs", "ip_address", "TEXT")
    connection.executescript(AUTH_SCHEMA)
    for key, value in AUTH_SETTINGS.items():
        connection.execute(
            "INSERT OR IGNORE INTO app_settings(key,value,updated_by) VALUES (?,?,'SYSTEM')", (key, value))
    if demo_ids:
        marks = ",".join("?" * len(demo_ids))
        connection.execute(f"UPDATE users SET is_demo=1 WHERE employee_id IN ({marks})", tuple(demo_ids))
    # Existing accounts with an address keep working; their address counts as
    # verified only once they prove it (reset link, invitation or sign-in code).
    connection.execute(
        "UPDATE users SET password_changed_at=COALESCE(password_changed_at, created_at) "
        "WHERE password_changed_at IS NULL")
    # E-mail must identify one account, because it can be used to sign in.
    try:
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email ON users(lower(email)) WHERE email IS NOT NULL")
    except Exception:  # noqa: BLE001 - duplicates in old data: sign-in by e-mail refuses ambiguity instead
        pass
    _rebuild_notification_jobs(connection)
