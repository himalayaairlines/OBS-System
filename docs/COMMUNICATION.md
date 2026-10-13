# Stock alerts, SMTP and the Communication Manager

This is the operations and reference guide for the stock-alert upgrade (API 7.3). For the code layout, start with [`backend/alert_engine.py`](../backend/alert_engine.py).

## What it does

- Authorised users create **alert rules**. Each rule names the products it watches (specific products, categories, or every product, with exclusions), a condition and threshold, its recipients, channels, frequency and an optional escalation policy.
- When warehouse stock changes, matching rules are evaluated. A breach opens an **incident**, which queues **in-app notifications** and **emails** for that rule's recipients only.
- **A product with no active rule never produces email or in-app alerts.** The warehouse dashboard still flags it as low stock.

The only stock figure in this system is **warehouse on-hand** (`main_inventory.quantity`). Flight trolley stock is never evaluated. There is a single warehouse, so warehouse and department scopes from the original brief do not apply yet (see [Not yet implemented](#not-yet-implemented)).

## How it works

```text
stock change (move_stock) ──same transaction──▶ stock_change_outbox
                                                     │
worker (embedded thread or python worker.py) ◀───────┘
  1. evaluate each changed product against active rules
  2. leased scheduler: full re-check every N minutes, reminders, escalations, digests
  3. send due email jobs over SMTP
        │
        ├─▶ alert_incidents + alert_events  (business state: OPEN, ACKNOWLEDGED, ESCALATED, SUPPRESSED, RESOLVED)
        ├─▶ user_notifications               (in-app, per user)
        └─▶ notification_jobs                (delivery state: PENDING, PROCESSING, ACCEPTED, TEMP_FAILED, FAILED, CANCELLED)
```

- **No lost events.** `routers/warehouse.move_stock` is the only code that changes warehouse stock. It writes an outbox row in the same transaction, so if the stock change rolls back the event does too. Product create, update and deactivate also write events. The periodic sweep catches anything missed while the worker was down.
- **No duplicates.** A unique partial index allows one live incident per rule and product. Evaluation runs under `BEGIN IMMEDIATE`, and jobs are claimed with a lease. Every job and notification has a unique idempotency key.
- **Deduplication.** While the condition holds, the same incident stays open. Stock going 9, 8, 7, 6 under a "below 10" rule sends one alert. Recovery resolves the incident; a later breach opens a new one.
- **Hysteresis.** With a recovery margin of 5 on a "below 10" rule, the incident resolves only at 15 or more.
- **Severity.** Severity goes LOW, then CRITICAL (below the critical threshold), then OUT (zero). A rise can notify again (`notify_on_critical`) without opening a new incident. Recipients can be limited to LOW-only or CRITICAL-only notifications.
- **Overlap.** Rules that overlap all run independently. An **exclusive** rule takes over a product it names explicitly from rules that reach that product only through a category or "every product".
- **Rule changes apply at once.** Editing, activating or disabling a rule re-evaluates its products straight away. Incidents a rule no longer covers are resolved with a reason.
- **Delivery is at-least-once.** If the process dies after the SMTP server accepts a message but before the job is marked, the message is sent again. A stable `Message-ID` lets duplicates be recognised. **ACCEPTED only means the SMTP server took the message**, not that it reached an inbox.
- **SMTP outages lose nothing.** While SMTP is disabled or misconfigured, jobs stay PENDING. Connection failures retry with exponential backoff (1 minute doubling, capped at 6 hours) up to *max attempts*, then go to FAILED. Administrators can retry FAILED jobs.

### Conditions

| Trigger | Breach when | Threshold |
|---|---|---|
| Below | stock < T | fixed, or each product's reorder level |
| At or below | stock ≤ T | fixed, or reorder level |
| Out of stock | stock ≤ 0 | none |
| Below a percentage of reorder level | stock < reorder × P% | P |
| Below for a duration | stock < T continuously for N minutes | fixed, or reorder level |

### Frequency

| Option | Behaviour |
|---|---|
| Once per new breach | Notifies when the incident opens, plus severity rises and recovery if those are enabled |
| Repeat every N hours | Reminders every N hours while active, up to *max reminders* |
| Once a day | At most one reminder per local calendar day per incident |
| Daily / weekly digest | In-app alerts go out straight away; one summary email per recipient at the digest time |

**Business hours only** holds emails until the next business window. The timezone, hours and days are set in Communication settings (default Asia/Kathmandu, 09:00–18:00, Sunday–Friday).

### Escalation

A policy has numbered steps. Step N fires once the incident has been open and **unacknowledged** for its wait time, counted from when it opened. Acknowledging stops escalation. Suppressing stops all messages until the stock recovers.

## Permissions

| Permission | Roles | Allows |
|---|---|---|
| `comms_view` | inventory, inventory_supervisor, management | Dashboard, rules, alerts, groups, policies (read); act on alerts they are involved in |
| `comms_rules` | inventory, inventory_supervisor, management | Create rules; edit their own |
| `comms_rules_all` | management | Edit, disable or delete anyone's rule; assign an owner |
| `comms_groups` | inventory_supervisor, management | Recipient groups and escalation policies |
| `comms_admin` | management, admin | Delivery log, retry/cancel, communication settings |
| `comms_templates` | management, admin | Email templates |
| `smtp_view` | management, admin | See SMTP settings (password never shown) |
| `smtp_manage` | admin | Change SMTP settings and credentials |
| `smtp_test` | admin | Test connection, send a test email |

Every signed-in user can read and mark their own notifications, and nobody else's.

**Information-sharing policy.** A recipient must be an active user whose role can view warehouse stock (`warehouse` permission). Anyone else is refused when the rule is saved and dropped at send time, and the preview explains why. External email addresses are allowed only at domains an administrator lists in Communication settings; by default there are none. Only the rule owner, a resolved recipient, the assignee or management can acknowledge, comment, assign or suppress an alert.

## Setup

1. Install the two new dependencies:

   ```bash
   cd backend
   pip install -r requirements.txt     # adds cryptography and tzdata
   ```

2. Create the encryption key and the `.env` file:

   ```bash
   copy .env.example .env              # macOS/Linux: cp .env.example .env
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Put the key in `OBS_SECRET_KEY`. Without it, a development key is generated in `backend/.obs_secret.key`, which is git-ignored. Back it up separately from the database.

3. Start the API. The database migrates itself on start: it adds the new tables and `users.email`, and leaves existing rows untouched.

   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8000 --env-file .env
   ```

   The start-up banner shows `v7.3` and whether the worker is embedded.

4. In the app, sign in as **ADMIN001**. Under **Admin → Login accounts**, give each person who should receive email an address.
5. Under **Communication Manager → Settings → SMTP / email**: enter host, port, encryption, username, password and sender, then **Save**. Use **Test connection**, then **Send test email**, and only then switch on **Send email**.
6. Sign in as an inventory user and create a rule under **Communication Manager → Alert rules → New rule**. Use **Simulate** before activating.

### SMTP notes

- STARTTLS on port 587 or implicit TLS on port 465. **Certificates are always verified**; there is no switch to turn that off. For a private CA, set `OBS_SMTP_CA_FILE`.
- Unencrypted SMTP is refused unless `OBS_SMTP_ALLOW_PLAINTEXT=1`, which is meant for an internal relay only.
- **Microsoft 365:** host `smtp.office365.com`, port 587, STARTTLS, with SMTP AUTH enabled for the sending mailbox. Tenants that allow only OAuth cannot be used yet. The transport lives in `mailer.open_smtp`, so an OAuth provider can be added there.
- The stored password is encrypted with `OBS_SECRET_KEY`, never returned by the API and never written to logs or the audit trail. Changing the key makes the stored password unreadable, and the API says to re-enter it.

## Running the worker

| Setup | How |
|---|---|
| Development, or one API process | Nothing to do. The worker runs inside uvicorn (`OBS_EMBEDDED_WORKER=1`). |
| Production | Run `python worker.py` as a service (NSSM or Task Scheduler on Windows, systemd on Linux), and start the API with `OBS_EMBEDDED_WORKER=0`. |
| Diagnostics or cron | `python worker.py --once` runs one cycle and prints a summary. |

Running several API workers, or the API and `worker.py` together, is safe: outbox rows and jobs are claimed under a write lock, and the scheduler is leased in `worker_leases`. Work done inside FastAPI request handlers is never relied on for delivery; jobs live in the database until they are sent.

## Tests

```bash
cd backend
pip install pytest          # optional; unittest works too
python -m unittest discover -s tests -t . -v
```

The tests run the API on a random local port against a throwaway database, with an in-process fake SMTP server. They never send real email. They cover:

- scenarios A–E
- deduplication, boundaries, hysteresis and duration conditions
- low-to-critical routing and recipient deduplication
- overlapping and exclusive rules, disable and edit while an incident is open
- rollback, concurrent workers, reminders, escalation, digests and business hours
- SMTP: secret masking, permissions, authentication failure, bad host, timeout, TLS certificate failure, test email, outage and retry, permanent rejection
- authorisation, private notifications, template injection
- a full end-to-end workflow and regression checks on existing endpoints

The certificate test needs `openssl` on the PATH.

## Backup and rollback

- **Before upgrading,** stop the API and copy `backend/obs.db`, plus `-wal` and `-shm` if present. Keep `OBS_SECRET_KEY`, or `.obs_secret.key`, in a separate secure place.
- **Rolling back the code** to API 7.2 needs no database change. The new tables and `users.email` are simply ignored by the old code. To return to the exact previous state, restore the copied `obs.db`.
- To turn off **outgoing email** without rolling back, switch off *Send email* in SMTP settings. Jobs wait in the queue. To stop **all** alerts, disable the rules.

## API

All routes need a bearer token. Details are at `/docs` on a running server.

- **SMTP:** `GET/PUT /settings/smtp`, `POST /settings/smtp/test-connection`, `POST /settings/smtp/send-test`
- **Rules:** `GET/POST /communication/rules`, `GET/PUT/DELETE /communication/rules/{id}`, `POST …/{id}/activate|disable|duplicate|simulate`, `POST /communication/rules/simulate`
- **Builder data:** `GET /communication/options`, `POST /communication/recipients/preview`, `GET /communication/dashboard`
- **Groups and policies:** `GET/POST /communication/groups`, `PUT/DELETE /communication/groups/{id}`, `GET/POST /communication/escalation-policies`, `PUT/DELETE …/{id}`
- **Alerts:** `GET /communication/alerts`, `GET /communication/alerts/{id}`, `POST …/{id}/acknowledge|comment|assign|suppress`
- **Delivery:** `GET /communication/deliveries`, `POST …/{id}/retry|cancel`
- **Templates:** `GET /communication/templates`, `PUT …/{key}`, `POST …/{key}/preview`, `GET …/{key}/versions`, `POST …/{key}/restore/{version}`
- **Settings:** `GET/PUT /communication/settings`
- **Notifications:** `GET /notifications`, `GET /notifications/unread-count`, `POST /notifications/{id}/read`, `POST /notifications/read-all`
- **Users:** the existing `POST/PUT /users` now accept an optional `email`

All changes to rules, groups, policies, templates, settings, SMTP, deliveries and alert actions are written to `audit_logs`. Secrets are never logged.

## Not yet implemented

These parts of the original brief are deliberately left for later increments:

- **Authentication hardening:** Argon2id, password-reset email, sessions stored in the database with revocation, MFA, a granular role editor, a security audit screen. Sessions still live in memory and passwords use PBKDF2.
- **Multi-warehouse, department and location scopes.** The data model has one warehouse, so these would need that structure first.
- **Microsoft Teams, SMS and push channels.** In-app and email are the only channels.
- **OAuth email delivery** for Microsoft 365 tenants that block SMTP AUTH.
- **Live updates.** The bell polls every 30 seconds; there is no WebSocket.

## Stock Alert Settings (API 7.4)

Communication Manager → **Stock alert settings** shows every item with the alert values that actually apply to it, and lets authorised users change them per item. Nothing is hardcoded: every threshold, recipient, interval and escalation step comes from the database and takes effect at once (the change is re-evaluated immediately and also queued through the outbox).

### What decides an item's alerts (precedence)

1. **Master switch** (Settings → *Stock alerts enabled*). Off: no new incidents, reminders, escalations or digests. Existing incidents are left as they are.
2. **Item setting – Disabled**: no stock alerts for that item from any rule. Open incidents for it are resolved.
3. **Item setting – Override**: each value set on the item (alert threshold, critical threshold, recovery level) replaces that value in every rule that reaches the item. Empty boxes keep the rule's value. Recipients, channels, frequency and escalation always come from the rule.
4. **Rules**: an *exclusive* rule naming the item explicitly takes over from rules reaching it only by category or "every product"; otherwise each matching rule evaluates on its own.

An item no rule reaches never alerts, even with an override (shown as *Needs attention*). There is one stock location (the main warehouse), so there is no warehouse or department level. Stock is counted in whole units, so thresholds are whole numbers; **0 is a real threshold and is never the same as "not set"**.

### Recovery

An incident resolves when stock reaches the item's *recovery level* (stock ≥ value) or, without one, the rule's threshold plus its recovery margin. Between the alert threshold and the recovery level the incident stays open, so stock bouncing around the threshold does not produce repeated alerts. Acknowledging never resolves a shortage.

### Item table, coverage, simulation

* Search, category, status and below-threshold filters; server-side paging. ● marks values set on the item itself.
* **Alert coverage**: configured, inheriting, disabled, missing and invalid counts, so gaps are visible before they cause a missed alert.
* Item page: where each value comes from (rule, item override, reorder level), the rule versions, alert history, and **Test the configuration**: enter a stock figure to see the outcome per rule (would alert, severity rises, no duplicate, stays open, would resolve), the recipients and the email text. A simulation changes nothing and sends nothing.
* **Usage-based suggestion**: average daily usage from trolley loads minus returns (last 90 days; needs at least 3 loads over 14 days) and supplier lead time from received purchase orders. Shown only when the data exists, and only applied if you put it in the form and save.
* **Bulk edit** (select rows): preview before/after for every item, then apply. Contradictory results block the whole change.
* **Export CSV / Import**: columns `product_code, product_name, category, mode, low_threshold, critical_threshold, recovery_threshold, note`. Import is dry-run first; duplicates, unknown codes and invalid values are reported and nothing is applied until the file is clean.

### Snooze, versions, quiet hours

* **Snooze** (alert page, reason required): pauses reminders and escalation for up to *Longest snooze* hours. Critical/out-of-stock alerts can be snoozed only if Settings allow it. A worse stock level always ends a snooze and notifies as usual.
* **Rule versions**: every create/edit/activate/disable stores a snapshot. Each incident records the rule version that opened it (Alert rules → rule → Version history).
* **Critical ignores business hours** (Settings): for business-hours rules, critical and out-of-stock email goes out at once.

### Permissions

| Permission | Who | Allows |
|---|---|---|
| `comms_view` | inventory, inventory supervisor, management, admin | item table, coverage, simulation, suggestions, export |
| `comms_item_settings` | inventory supervisor, management, admin | change item settings, bulk edit, import |

The administrator role now also has `comms_view`, `comms_rules`, `comms_rules_all` and `comms_groups`, so the administrator can configure alerts. A rule's owner must still be someone who can see warehouse stock (for example an inventory supervisor).

### Endpoints (all under `/communication`)

`GET /items`, `GET /items/{id}`, `PUT /items/{id}/alert-settings`, `POST /items/{id}/simulate`, `POST /items/bulk`, `GET /items-export`, `POST /items-import`, `GET /coverage`, `GET /rules/{id}/versions`, `POST /alerts/{id}/snooze`, `POST /alerts/{id}/unsnooze`. Settings gain `alerts_enabled`, `max_snooze_hours`, `allow_snooze_critical`, `critical_ignores_business_hours`.

### Database (added automatically on start, nothing removed)

`stock_alert_item_settings` (one row per item with its own settings; no row = inherit), `comm_rule_versions`, and on `alert_incidents`: `rule_version`, `snoozed_until`, `snooze_reason`, `snoozed_by`.
