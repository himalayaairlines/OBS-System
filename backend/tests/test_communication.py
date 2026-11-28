"""Integration tests for SMTP settings, stock-alert rules and delivery.

Run from the backend folder:

    python -m unittest discover -s tests -v        (or: python -m pytest tests)

They start the real API on a random local port against a throwaway database
and talk to an in-process fake SMTP server. No real email is ever sent.
"""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
_TMP = tempfile.mkdtemp(prefix="obs-test-")
os.environ["OBS_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["OBS_EMBEDDED_WORKER"] = "0"
os.environ["OBS_SMTP_ALLOW_PLAINTEXT"] = "1"
from cryptography.fernet import Fernet  # noqa: E402
os.environ["OBS_SECRET_KEY"] = Fernet.generate_key().decode()

import uvicorn  # noqa: E402

import alert_engine  # noqa: E402
import database  # noqa: E402
import comms_worker  # noqa: E402
import mailer  # noqa: E402
import main  # noqa: E402
from comms_core import load_settings, utc_now  # noqa: E402
from database import get_connection  # noqa: E402
from tests.fake_smtp import FakeSMTP, free_port, self_signed_context  # noqa: E402

PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
_server = None
TOKENS = {}
PASSWORDS = {"INV777": "Stock#Count2026"}


# In the shipped matrix only the administrator configures stock alerts (7.5).
# The alert engine still supports rules owned by several people, so these tests
# lend the pre-7.5 Communication Manager rights to the other roles to exercise
# ownership and recipient rules with distinct users. Restored afterwards.
DELEGATED = {
    "inventory": {"comms_view", "comms_rules"},
    "inventory_supervisor": {"comms_view", "comms_rules", "comms_groups", "comms_item_settings"},
    "management": {"comms_view", "comms_rules", "comms_rules_all", "comms_groups", "comms_admin",
                   "comms_item_settings", "comms_templates", "smtp_view"},
}
SHIPPED = {role: set(perms) for role, perms in database.ROLE_PERMISSIONS.items()}


def setUpModule():
    global _server
    # 7.17 folded the inventory supervisor into "inventory"; these tests still
    # need two inventory users with different rights, so ISUP001 is given the
    # legacy role (its permission set is an alias kept for old tokens) and a
    # distinct copy of that set to delegate to.
    database.ROLE_PERMISSIONS["inventory_supervisor"] = set(database.ROLE_PERMISSIONS["inventory"])
    for role, extra in DELEGATED.items():
        database.ROLE_PERMISSIONS[role] |= extra
    connection = database.get_connection()
    connection.execute("UPDATE users SET role='inventory_supervisor', name='Inventory Supervisor' WHERE employee_id='ISUP001'")
    connection.commit()
    connection.close()
    config = uvicorn.Config(main.app, host="127.0.0.1", port=PORT, log_level="warning")
    _server = uvicorn.Server(config)
    threading.Thread(target=_server.run, daemon=True).start()
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{BASE}/health", timeout=1)
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    raise RuntimeError("API did not start")


def tearDownModule():
    for role, perms in SHIPPED.items():
        database.ROLE_PERMISSIONS[role] = perms
    if _server:
        _server.should_exit = True


def call(method, path, who=None, body=None):
    headers = {"Content-Type": "application/json"}
    if who:
        # A raw session token (long) or an Employee ID to sign in as.
        headers["Authorization"] = f"Bearer {who if len(who) > 30 else token(who)}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def token(who):
    if who not in TOKENS:
        status, body = call("POST", "/login", body={"employee_id": who, "password": PASSWORDS.get(who, "1234")})
        assert status == 200, body
        TOKENS[who] = body["token"]
    return TOKENS[who]


def db():
    return get_connection()


def q(sql, params=()):
    connection = db()
    try:
        return [dict(r) for r in connection.execute(sql, params).fetchall()]
    finally:
        connection.close()


def user_id(employee_id):
    return q("SELECT id FROM users WHERE employee_id=?", (employee_id,))[0]["id"]


def set_stock(product_id, target):
    """Move stock through the real API so the transactional outbox is exercised."""
    current = q("SELECT quantity FROM main_inventory WHERE product_id=?", (product_id,))[0]["quantity"]
    if target != current:
        status, body = call("POST", "/main-inventory/adjust", "ISUP001",
                            {"product_id": product_id, "quantity": target - current, "remarks": "test"})
        assert status == 200, body


def cycle():
    comms_worker.process_outbox()


def rule_body(**overrides):
    body = {
        "name": "Test rule", "products": [], "categories": [], "trigger_type": "BELOW",
        "threshold": 10, "recipients": [{"recipient_type": "USER", "recipient_value": str(user_id("INV001"))}],
        "channel_in_app": True, "channel_email": True, "frequency": "ONCE", "activate": True,
    }
    body.update(overrides)
    return body


def make_rule(who="INV001", **overrides):
    status, body = call("POST", "/communication/rules", who, rule_body(**overrides))
    assert status == 200, body
    return body


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        products = q("SELECT id, category FROM products WHERE active=1 ORDER BY id")
        cls.p1, cls.p2, cls.p3 = products[0]["id"], products[1]["id"], products[2]["id"]
        by_cat = {}
        for p in products:
            by_cat.setdefault(p["category"], []).append(p["id"])
        cls.category, members = next((c, ids) for c, ids in by_cat.items() if len(ids) >= 2)
        cls.cat_a, cls.cat_b = members[0], members[1]
        for emp, email in (("INV001", "inv@himalaya-airlines.com"), ("ISUP001", "isup@himalaya-airlines.com"),
                           ("MGT001", "mgt@himalaya-airlines.com"), ("FIN001", "fin@himalaya-airlines.com")):
            connection = db()
            connection.execute("UPDATE users SET email=? WHERE employee_id=?", (email, emp))
            connection.commit()
            connection.close()

    def setUp(self):
        connection = db()
        for table in ("user_notifications", "notification_jobs", "alert_events", "alert_breach_watch",
                      "alert_incidents", "stock_alert_item_settings", "comm_rule_versions",
                      "comm_rule_recipients", "comm_rule_scopes", "comm_rules",
                      "comm_group_members", "comm_groups", "comm_escalation_steps",
                      "comm_escalation_policies", "stock_change_outbox", "worker_leases"):
            connection.execute(f"DELETE FROM {table}")
        connection.execute("UPDATE main_inventory SET quantity=500")
        connection.execute("UPDATE products SET active=1")
        connection.execute("UPDATE smtp_settings SET enabled=0 WHERE id=1")
        connection.execute("UPDATE app_settings SET value='' WHERE key='external_email_domains'")
        # The demo password 1234 would otherwise force a password change first.
        connection.execute("UPDATE app_settings SET value='0' WHERE key='require_strong_passwords'")
        connection.execute("UPDATE users SET must_change_password=0, failed_attempts=0, locked_at=NULL, lock_reason=NULL")
        for key, value in (("alerts_enabled", "1"), ("allow_snooze_critical", "0"),
                           ("critical_ignores_business_hours", "0"), ("max_snooze_hours", "72")):
            connection.execute("UPDATE app_settings SET value=? WHERE key=?", (value, key))
        connection.commit()
        connection.close()

    def incidents(self, **where):
        sql, params = "SELECT * FROM alert_incidents", []
        if where:
            sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
            params = list(where.values())
        return q(sql + " ORDER BY id", params)

    def jobs(self):
        return q("SELECT * FROM notification_jobs ORDER BY id")

    def inbox(self, employee_id):
        return q("SELECT * FROM user_notifications WHERE user_id=? ORDER BY id", (user_id(employee_id),))


# --------------------------------------------------------------------------- #
class RuleEvaluationTests(Base):
    def test_no_matching_rule_sends_nothing(self):
        """Scenario E: low stock with no rule never produces outbound communication."""
        set_stock(self.p1, 1)
        cycle()
        comms_worker.run_scheduled(force_sweep=True, owner="t")
        self.assertEqual(self.incidents(), [])
        self.assertEqual(self.jobs(), [])
        self.assertEqual(q("SELECT * FROM user_notifications"), [])
        status, dash = call("GET", "/warehouse/dashboard", "ISUP001")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(dash["low_stock_count"], 1)  # the dashboard still shows it

    def test_single_item_dedup_recovery_and_reopen(self):
        """Scenario A plus 9 -> 8 -> 7 -> 6 deduplication."""
        make_rule(products=[self.p1], threshold=10)
        for qty in (12, 9, 8, 7, 6):
            set_stock(self.p1, qty)
            cycle()
        self.assertEqual(len(self.incidents()), 1)
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual(len(self.inbox("INV001")), 1)
        self.assertEqual(self.inbox("ISUP001"), [])  # unrelated people hear nothing
        self.assertEqual(self.jobs()[0]["recipient_email"], "inv@himalaya-airlines.com")

        set_stock(self.p1, 15)
        cycle()
        self.assertEqual(self.incidents()[0]["status"], "RESOLVED")
        set_stock(self.p1, 5)
        cycle()
        self.assertEqual(len(self.incidents()), 2)
        self.assertEqual(self.incidents()[1]["status"], "OPEN")
        self.assertEqual(len(self.jobs()), 2)

    def test_threshold_boundaries(self):
        make_rule(products=[self.p1], trigger_type="BELOW", threshold=5)
        make_rule(products=[self.p2], trigger_type="AT_OR_BELOW", threshold=10)
        set_stock(self.p1, 5)
        set_stock(self.p2, 10)
        cycle()
        self.assertEqual(self.incidents(product_id=self.p1), [])
        self.assertEqual(len(self.incidents(product_id=self.p2)), 1)
        set_stock(self.p1, 4)
        cycle()
        self.assertEqual(len(self.incidents(product_id=self.p1)), 1)

    def test_out_of_stock_trigger(self):
        make_rule(products=[self.p1], trigger_type="OUT_OF_STOCK", threshold=None)
        set_stock(self.p1, 1)
        cycle()
        self.assertEqual(self.incidents(), [])
        set_stock(self.p1, 0)
        cycle()
        self.assertEqual(self.incidents()[0]["severity"], "OUT")

    def test_low_to_critical_routes_by_level(self):
        """Scenario C: below 3 = low to the officer, below 2 = critical to the manager."""
        make_rule(products=[self.p1], threshold=3, critical_threshold=2, recipients=[
            {"recipient_type": "USER", "recipient_value": str(user_id("INV001")), "level": "LOW"},
            {"recipient_type": "USER", "recipient_value": str(user_id("ISUP001")), "level": "CRITICAL"},
        ])
        set_stock(self.p1, 2)
        cycle()
        self.assertEqual(len(self.inbox("INV001")), 1)
        self.assertEqual(self.inbox("ISUP001"), [])
        set_stock(self.p1, 1)
        cycle()
        incidents = self.incidents()
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["severity"], "CRITICAL")
        self.assertEqual(len(self.inbox("ISUP001")), 1)
        self.assertEqual(len(self.inbox("INV001")), 1)
        self.assertEqual([j["recipient_email"] for j in self.jobs()],
                         ["inv@himalaya-airlines.com", "isup@himalaya-airlines.com"])

    def test_category_rule_evaluates_each_product_and_honours_exclusions(self):
        """Scenario D analogue: a category rule is evaluated per product, never aggregated."""
        make_rule(categories=[self.category], exclude_products=[self.cat_b], threshold=10)
        set_stock(self.cat_a, 5)
        set_stock(self.cat_b, 5)
        cycle()
        self.assertEqual([i["product_id"] for i in self.incidents()], [self.cat_a])

    def test_disabled_rule_is_silent_and_disabling_resolves(self):
        rule = make_rule(products=[self.p1], threshold=10, activate=False)
        set_stock(self.p1, 3)
        cycle()
        self.assertEqual(self.incidents(), [])
        call("POST", f"/communication/rules/{rule['id']}/activate", "INV001")
        self.assertEqual(len(self.incidents()), 1)  # applied at once, no sweep needed
        call("POST", f"/communication/rules/{rule['id']}/disable", "INV001")
        self.assertEqual(self.incidents()[0]["status"], "RESOLVED")

    def test_overlapping_rules_are_independent_unless_exclusive(self):
        make_rule(name="General", categories=[self.category], threshold=10)
        specific = make_rule(name="Specific", products=[self.cat_a], threshold=10)
        set_stock(self.cat_a, 5)
        cycle()
        self.assertEqual(len(self.incidents()), 2)

        body = rule_body(name="Specific", products=[self.cat_a], threshold=10, exclusive=True)
        status, _ = call("PUT", f"/communication/rules/{specific['id']}", "INV001", body)
        self.assertEqual(status, 200)
        active = [i for i in self.incidents() if i["status"] != "RESOLVED"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["rule_id"], specific["id"])

    def test_recipients_are_deduplicated(self):
        make_rule(products=[self.p1], threshold=10, recipients=[
            {"recipient_type": "OWNER"},
            {"recipient_type": "USER", "recipient_value": str(user_id("INV001"))},
            {"recipient_type": "ROLE", "recipient_value": "inventory"},
        ])
        set_stock(self.p1, 3)
        cycle()
        emails = [j["recipient_email"] for j in self.jobs()]
        self.assertEqual(emails.count("inv@himalaya-airlines.com"), 1)
        self.assertEqual(len(emails), len(set(emails)))
        self.assertEqual(len(self.inbox("INV001")), 1)

    def test_hysteresis(self):
        make_rule(products=[self.p1], threshold=10, recovery_margin=5)
        set_stock(self.p1, 9)
        cycle()
        set_stock(self.p1, 12)
        cycle()
        self.assertEqual(self.incidents()[0]["status"], "OPEN")
        set_stock(self.p1, 15)
        cycle()
        self.assertEqual(self.incidents()[0]["status"], "RESOLVED")

    def test_duration_condition(self):
        make_rule(products=[self.p1], trigger_type="BELOW_FOR_DURATION", threshold=10, duration_minutes=30)
        set_stock(self.p1, 4)
        cycle()
        self.assertEqual(self.incidents(), [])
        connection = db()
        try:
            with alert_engine.immediate(connection):
                alert_engine.evaluate_product(connection, self.p1, utc_now() + timedelta(minutes=31))
        finally:
            connection.close()
        self.assertEqual(len(self.incidents()), 1)

    def test_rule_edit_while_incident_active(self):
        rule = make_rule(products=[self.p1], threshold=10)
        set_stock(self.p1, 8)
        cycle()
        status, _ = call("PUT", f"/communication/rules/{rule['id']}", "INV001",
                         rule_body(products=[self.p1], threshold=5))
        self.assertEqual(status, 200)
        self.assertEqual(self.incidents()[0]["status"], "RESOLVED")

    def test_product_deactivation_resolves(self):
        make_rule(products=[self.p1], threshold=10)
        set_stock(self.p1, 2)
        cycle()
        status, _ = call("DELETE", f"/products/{self.p1}", "ISUP001")
        self.assertEqual(status, 200)
        cycle()
        self.assertEqual(self.incidents()[0]["status"], "RESOLVED")

    def test_failed_stock_transaction_leaves_no_event(self):
        before = q("SELECT COUNT(*) c FROM stock_change_outbox")[0]["c"]
        status, _ = call("POST", "/main-inventory/adjust", "ISUP001",
                         {"product_id": self.p1, "quantity": -100000, "remarks": "too much"})
        self.assertEqual(status, 400)
        self.assertEqual(q("SELECT COUNT(*) c FROM stock_change_outbox")[0]["c"], before)

    def test_concurrent_workers_create_one_incident(self):
        make_rule(products=[self.p1], threshold=10)
        set_stock(self.p1, 3)
        errors = []

        def run():
            try:
                comms_worker.process_outbox()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=run) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.incidents()), 1)
        self.assertEqual(len(self.jobs()), 1)

    def test_simulation_has_no_side_effects(self):
        set_stock(self.p1, 3)
        cycle()
        status, result = call("POST", "/communication/rules/simulate", "INV001",
                              rule_body(products=[self.p1, self.p2], threshold=10))
        self.assertEqual(status, 200)
        self.assertEqual(result["matched"], 2)
        self.assertEqual(result["would_notify"], 1)
        self.assertEqual(len(result["recipients"]["LOW"]["recipients"]), 1)
        self.assertEqual(self.incidents(), [])
        self.assertEqual(self.jobs(), [])
        self.assertEqual(q("SELECT * FROM comm_rules"), [])


class ScheduleTests(Base):
    def _settings(self):
        connection = db()
        try:
            return load_settings(connection)
        finally:
            connection.close()

    def _run(self, fn, now):
        connection = db()
        try:
            with alert_engine.immediate(connection):
                return fn(connection, now, self._settings())
        finally:
            connection.close()

    def test_daily_reminder_at_most_once_per_day(self):
        """Scenario B: daily while active, never twice on the same local day."""
        make_rule(products=[self.p1], trigger_type="AT_OR_BELOW", threshold=10, frequency="DAILY")
        set_stock(self.p1, 10)
        cycle()
        self.assertEqual(len(self.jobs()), 1)
        now = utc_now()
        self.assertEqual(self._run(alert_engine.run_reminders, now), 0)
        self.assertEqual(self._run(alert_engine.run_reminders, now + timedelta(days=1)), 1)
        self.assertEqual(self._run(alert_engine.run_reminders, now + timedelta(days=1, minutes=5)), 0)
        self.assertEqual(len(self.jobs()), 2)

    def test_repeat_hours_respects_cap(self):
        make_rule(products=[self.p1], threshold=10, frequency="REPEAT_HOURS", repeat_hours=2, max_reminders=2)
        set_stock(self.p1, 3)
        cycle()
        now = utc_now()
        sent = sum(self._run(alert_engine.run_reminders, now + timedelta(hours=3 * i)) for i in range(1, 6))
        self.assertEqual(sent, 2)

    def test_escalation_steps_and_acknowledgement(self):
        isup, mgt = user_id("ISUP001"), user_id("MGT001")
        status, policy = call("POST", "/communication/escalation-policies", "ISUP001", {
            "name": "Stores escalation", "steps": [
                {"step_no": 1, "after_minutes": 240, "recipient_type": "USER", "recipient_value": str(isup)},
                {"step_no": 2, "after_minutes": 1440, "recipient_type": "USER", "recipient_value": str(mgt)},
            ]})
        self.assertEqual(status, 200, policy)
        make_rule(products=[self.p1], threshold=10, escalation_policy_id=policy["id"])
        make_rule(products=[self.p2], threshold=10, escalation_policy_id=policy["id"])
        set_stock(self.p1, 3)
        set_stock(self.p2, 3)
        cycle()
        acked = self.incidents(product_id=self.p2)[0]
        status, _ = call("POST", f"/communication/alerts/{acked['id']}/acknowledge", "INV001", {"comment": "on it"})
        self.assertEqual(status, 200)

        now = utc_now()
        self.assertEqual(self._run(alert_engine.run_escalations, now + timedelta(hours=3)), 0)
        self.assertEqual(self._run(alert_engine.run_escalations, now + timedelta(hours=5)), 1)
        self.assertEqual(len(self.inbox("ISUP001")), 1)
        self.assertEqual(self._run(alert_engine.run_escalations, now + timedelta(hours=25)), 1)
        self.assertEqual(len(self.inbox("MGT001")), 1)
        self.assertEqual(self.incidents(product_id=self.p1)[0]["status"], "ESCALATED")
        self.assertEqual(self.incidents(product_id=self.p2)[0]["status"], "ACKNOWLEDGED")

    def test_daily_digest_replaces_immediate_email(self):
        make_rule(products=[self.p1, self.p2], threshold=10, frequency="DAILY_DIGEST")
        set_stock(self.p1, 3)
        set_stock(self.p2, 4)
        cycle()
        self.assertEqual(self.jobs(), [])
        self.assertEqual(len(self.inbox("INV001")), 2)  # in-app is still immediate
        now = utc_now().replace(hour=6, minute=0)  # 11:45 in Kathmandu, after the 09:00 digest time
        self.assertEqual(self._run(alert_engine.run_digests, now), 1)
        self.assertEqual(self._run(alert_engine.run_digests, now + timedelta(minutes=30)), 0)
        self.assertIn("incident #", self.jobs()[0]["body_text"])

    def test_business_hours_defers_email(self):
        make_rule(products=[self.p1], threshold=10, business_hours_only=True)
        set_stock(self.p1, 3)
        cycle()
        job = self.jobs()[0]
        from comms_core import is_business_time, parse_iso
        self.assertTrue(is_business_time(parse_iso(job["next_attempt_at"]), self._settings()))


class SmtpTests(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.smtp = FakeSMTP().start()

    @classmethod
    def tearDownClass(cls):
        cls.smtp.stop()

    def configure(self, **overrides):
        body = {"enabled": True, "host": "127.0.0.1", "port": self.smtp.port, "security": "none",
                "auth_enabled": True, "username": "obs", "password": "secret",
                "sender_email": "obs@himalaya-airlines.com", "sender_name": "OBS", "timeout_seconds": 3}
        body.update(overrides)
        status, result = call("PUT", "/settings/smtp", "ADMIN001", body)
        self.assertEqual(status, 200, result)
        return result

    def test_secret_is_encrypted_and_never_returned(self):
        result = self.configure()
        self.assertTrue(result["password_set"])
        self.assertNotIn("password", result)
        self.assertNotIn("password_enc", result)
        stored = q("SELECT password_enc FROM smtp_settings")[0]["password_enc"]
        self.assertNotIn("secret", stored)
        status, view = call("GET", "/settings/smtp", "MGT001")
        self.assertEqual(status, 200)
        self.assertNotIn("password_enc", view)
        log = q("SELECT metadata FROM audit_logs WHERE action='UPDATE_SMTP_SETTINGS' ORDER BY id DESC LIMIT 1")[0]
        self.assertNotIn("secret", log["metadata"])
        # Keeping the password: omitting it does not clear it.
        result = self.configure(password=None)
        self.assertTrue(result["password_set"])

    def test_smtp_permissions(self):
        self.assertEqual(call("GET", "/settings/smtp", "INV001")[0], 403)
        self.assertEqual(call("PUT", "/settings/smtp", "MGT001", {"enabled": False})[0], 403)
        self.assertEqual(call("POST", "/settings/smtp/test-connection", "ISUP001")[0], 403)
        self.assertEqual(call("GET", "/settings/smtp")[0], 401)

    def test_connection_results(self):
        self.configure()
        status, ok = call("POST", "/settings/smtp/test-connection", "ADMIN001")
        self.assertTrue(ok["ok"], ok)
        self.assertIn("AUTHENTICATED", ok["stages"])

        self.configure(password="wrong")
        _, bad = call("POST", "/settings/smtp/test-connection", "ADMIN001")
        self.assertFalse(bad["ok"])
        self.assertIn("Authentication failed", bad["message"])
        self.assertNotIn("wrong", bad["message"])

        self.configure(host="no-such-host.invalid")
        _, missing = call("POST", "/settings/smtp/test-connection", "ADMIN001")
        self.assertFalse(missing["ok"])

    def test_timeout(self):
        silent = FakeSMTP(silent=6).start()
        try:
            self.configure(port=silent.port, timeout_seconds=3)
            _, result = call("POST", "/settings/smtp/test-connection", "ADMIN001")
            self.assertFalse(result["ok"])
            self.assertIn("timed out", result["message"].lower())
        finally:
            silent.stop()

    def test_tls_certificate_is_verified(self):
        tls = FakeSMTP(tls_context=self_signed_context(_TMP)).start()
        try:
            self.configure(port=tls.port, security="ssl")
            _, result = call("POST", "/settings/smtp/test-connection", "ADMIN001")
            self.assertFalse(result["ok"])
            self.assertIn("certificate", result["message"].lower())
        finally:
            tls.stop()

    def test_plaintext_refused_without_opt_in(self):
        os.environ["OBS_SMTP_ALLOW_PLAINTEXT"] = "0"
        try:
            status, _ = call("PUT", "/settings/smtp", "ADMIN001", {"enabled": False, "security": "none"})
            self.assertEqual(status, 400)
        finally:
            os.environ["OBS_SMTP_ALLOW_PLAINTEXT"] = "1"

    def test_send_test_email(self):
        self.configure()
        before = len(self.smtp.messages)
        status, job = call("POST", "/settings/smtp/send-test", "ADMIN001", {"to_email": "inv@himalaya-airlines.com"})
        self.assertEqual(status, 200, job)
        self.assertEqual(job["status"], "ACCEPTED")
        self.assertIn("does not prove", job["note"])
        self.assertEqual(len(self.smtp.messages), before + 1)
        self.assertIn("Message-ID: <obs.", self.smtp.messages[-1]["data"])
        status, _ = call("POST", "/settings/smtp/send-test", "ADMIN001", {"to_email": "someone@gmail.com"})
        self.assertEqual(status, 400)

    def test_outage_keeps_jobs_and_retries(self):
        make_rule(products=[self.p1], threshold=10)
        set_stock(self.p1, 3)
        cycle()
        down_port = free_port()
        self.configure(port=down_port)
        result = mailer.deliver_due(get_connection, "t1")
        self.assertEqual(result["temp_failed"], 1)
        job = self.jobs()[0]
        self.assertEqual(job["status"], "TEMP_FAILED")
        self.assertGreater(job["next_attempt_at"], job["updated_at"])
        self.assertEqual(self.incidents()[0]["status"], "OPEN")  # delivery failure never touches the incident

        self.configure(port=self.smtp.port)
        connection = db()
        connection.execute("UPDATE notification_jobs SET next_attempt_at='2000-01-01T00:00:00Z'")
        connection.commit()
        connection.close()
        result = mailer.deliver_due(get_connection, "t2")
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(self.jobs()[0]["status"], "ACCEPTED")

    def test_disabled_smtp_keeps_jobs_pending(self):
        make_rule(products=[self.p1], threshold=10)
        set_stock(self.p1, 3)
        cycle()
        result = mailer.deliver_due(get_connection, "t")
        self.assertIn("skipped", result)
        self.assertEqual(self.jobs()[0]["status"], "PENDING")

    def test_permanent_rejection_and_manual_retry(self):
        self.configure()
        self.smtp.reject_rcpt.add("inv@himalaya-airlines.com")
        try:
            make_rule(products=[self.p1], threshold=10)
            set_stock(self.p1, 3)
            cycle()
            mailer.deliver_due(get_connection, "t")
            job = self.jobs()[0]
            self.assertEqual(job["status"], "FAILED")
            self.assertIn("550", job["last_error"])
        finally:
            self.smtp.reject_rcpt.clear()
        self.assertEqual(call("POST", f"/communication/deliveries/{job['id']}/retry", "INV001")[0], 403)
        status, _ = call("POST", f"/communication/deliveries/{job['id']}/retry", "ADMIN001")
        self.assertEqual(status, 200)
        mailer.deliver_due(get_connection, "t")
        self.assertEqual(self.jobs()[0]["status"], "ACCEPTED")


class AuthorisationTests(Base):
    def test_rule_ownership(self):
        rule = make_rule("ISUP001", products=[self.p1], threshold=10,
                         recipients=[{"recipient_type": "OWNER"}])
        self.assertEqual(call("PUT", f"/communication/rules/{rule['id']}", "INV001",
                              rule_body(products=[self.p1]))[0], 403)
        self.assertEqual(call("POST", f"/communication/rules/{rule['id']}/disable", "INV001")[0], 403)
        self.assertEqual(call("POST", f"/communication/rules/{rule['id']}/disable", "MGT001")[0], 200)

    def test_roles_without_access(self):
        self.assertEqual(call("GET", "/communication/rules", "FIN001")[0], 403)
        self.assertEqual(call("GET", "/communication/alerts", "CREW001")[0], 403)
        self.assertEqual(call("POST", "/communication/rules", "FIN001", rule_body(products=[self.p1]))[0], 403)
        self.assertEqual(call("GET", "/communication/deliveries", "INV001")[0], 403)
        self.assertEqual(call("GET", "/communication/rules")[0], 401)

    def test_recipient_policy(self):
        bad_user = rule_body(products=[self.p1], recipients=[
            {"recipient_type": "USER", "recipient_value": str(user_id("FIN001"))}])
        self.assertEqual(call("POST", "/communication/rules", "INV001", bad_user)[0], 400)
        external = rule_body(products=[self.p1], recipients=[
            {"recipient_type": "EMAIL", "recipient_value": "someone@gmail.com"}])
        self.assertEqual(call("POST", "/communication/rules", "INV001", external)[0], 400)
        other_owner = rule_body(products=[self.p1], owner_user_id=user_id("ISUP001"))
        self.assertEqual(call("POST", "/communication/rules", "INV001", other_owner)[0], 403)
        everything = rule_body()
        self.assertEqual(call("POST", "/communication/rules", "INV001", everything)[0], 400)
        mass_assign = rule_body(products=[self.p1], status="ACTIVE", version=99)
        self.assertEqual(call("POST", "/communication/rules", "INV001", mass_assign)[0], 422)

    def test_notifications_are_private(self):
        make_rule(products=[self.p1], threshold=10)
        set_stock(self.p1, 3)
        cycle()
        mine = self.inbox("INV001")[0]
        self.assertEqual(call("POST", f"/notifications/{mine['id']}/read", "ISUP001")[0], 404)
        status, listing = call("GET", "/notifications", "ISUP001")
        self.assertEqual(listing["items"], [])
        self.assertEqual(call("POST", f"/notifications/{mine['id']}/read", "INV001")[0], 200)

    def test_only_involved_users_act_on_alerts(self):
        make_rule(products=[self.p1], threshold=10)
        set_stock(self.p1, 3)
        cycle()
        incident = self.incidents()[0]
        self.assertEqual(call("POST", f"/communication/alerts/{incident['id']}/acknowledge", "ISUP001", {})[0], 403)
        status, detail = call("POST", f"/communication/alerts/{incident['id']}/acknowledge", "INV001", {})
        self.assertEqual(status, 200)
        self.assertEqual(detail["status"], "ACKNOWLEDGED")

    def test_template_placeholders_validated_and_escaped(self):
        status, _ = call("PUT", "/communication/templates/low_stock", "ADMIN001",
                         {"subject": "{{item_name}}", "body": "{{__import__}}"})
        self.assertEqual(status, 400)
        self.assertEqual(call("PUT", "/communication/templates/low_stock", "INV001",
                              {"subject": "x", "body": "y"})[0], 403)
        from comms_core import render_email
        connection = db()
        try:
            _, text, html = render_email(connection, "low_stock", {"item_name": "<script>x</script>"})
        finally:
            connection.close()
        self.assertIn("<script>", text)
        self.assertNotIn("<script>", html)


class RegressionTests(Base):
    """Existing screens keep working after the upgrade."""

    def test_existing_endpoints(self):
        checks = [
            ("CREW001", "/me"), ("ISUP001", "/main-inventory"), ("ISUP001", "/warehouse/dashboard"),
            ("INV001", "/products"), ("FIN001", "/exchange-rates"), ("ADMIN001", "/users"),
            ("MGT001", "/main-inventory/movements"), (None, "/permissions"), (None, "/health"),
        ]
        for who, path in checks:
            status, _ = call("GET", path, who)
            self.assertEqual(status, 200, f"{who} {path}")
        status, users = call("GET", "/users", "ADMIN001")
        self.assertIn("email", users[0])
        status, health = call("GET", "/health")
        self.assertEqual(health["version"], "7.18")

    def test_stock_movements_unchanged(self):
        before = q("SELECT quantity FROM main_inventory WHERE product_id=?", (self.p1,))[0]["quantity"]
        status, body = call("POST", "/main-inventory/receive", "ISUP001",
                            {"product_id": self.p1, "quantity": 7, "remarks": "regression"})
        self.assertEqual(status, 200)
        self.assertEqual(body["balance"], before + 7)
        txn = q("SELECT reason, change_qty FROM main_inventory_txn ORDER BY id DESC LIMIT 1")[0]
        self.assertEqual((txn["reason"], txn["change_qty"]), ("DIRECT_RECEIPT", 7))

    def test_user_email_validation(self):
        status, _ = call("PUT", f"/users/{user_id('INV001')}", "ADMIN001",
                         {"name": "Inventory Officer", "role": "inventory", "active": True, "email": "not-an-email"})
        self.assertEqual(status, 400)

    def test_embedded_worker_thread(self):
        make_rule(products=[self.p1], threshold=10)
        worker = comms_worker.Worker(interval=1)
        worker.start()
        try:
            set_stock(self.p1, 2)
            for _ in range(50):
                if self.incidents():
                    break
                time.sleep(0.1)
            self.assertEqual(len(self.incidents()), 1)
        finally:
            worker.stop()
            worker.join(timeout=5)


class PaginationTests(Base):
    """Every growing list pages on the server and stays backward compatible."""

    def assertEnvelope(self, body, page, page_size):
        for key in ("items", "total", "page", "page_size", "pages", "has_prev", "has_next"):
            self.assertIn(key, body)
        self.assertEqual(body["page"], page)
        self.assertEqual(body["page_size"], page_size)
        self.assertLessEqual(len(body["items"]), page_size)

    def test_orders_pages_without_overlap(self):
        for i in range(27):
            status, _ = call("POST", "/orders", "INV001", {"items": [{"product_id": self.p1, "quantity": i + 1}]})
            self.assertEqual(status, 200)
        total = len(call("GET", "/orders", "INV001")[1])  # legacy shape: a plain list
        _, first = call("GET", "/orders?page=1&page_size=10", "INV001")
        _, second = call("GET", "/orders?page=2&page_size=10", "INV001")
        self.assertEnvelope(first, 1, 10)
        self.assertEqual(first["total"], total)
        self.assertEqual(first["pages"], -(-total // 10))
        self.assertTrue(first["has_next"])
        self.assertFalse(first["has_prev"])
        self.assertFalse({o["id"] for o in first["items"]} & {o["id"] for o in second["items"]})
        _, pending = call("GET", "/orders?status=PENDING&page=1&page_size=5", "INV001")
        self.assertTrue(all(o["status"] == "PENDING" for o in pending["items"]))

    def test_page_past_end_is_clamped_and_size_is_capped(self):
        _, body = call("GET", "/logs?page=9999&page_size=10", "MGT001")
        self.assertEqual(body["page"], body["pages"])
        self.assertEqual(call("GET", "/logs?page=1&page_size=500", "MGT001")[0], 422)
        self.assertEqual(call("GET", "/logs?page=0", "MGT001")[0], 422)
        self.assertIsInstance(call("GET", "/logs", "MGT001")[1], list)

    def test_movements_and_audit_log(self):
        for qty in (10, 20, 30):
            set_stock(self.p1, qty)
        _, moves = call("GET", f"/main-inventory/movements?product_id={self.p1}&page=1&page_size=2", "ISUP001")
        self.assertEnvelope(moves, 1, 2)
        self.assertGreaterEqual(moves["total"], 3)
        self.assertEqual([m["balance_after"] for m in moves["items"]], [30, 20])  # newest first
        _, logs = call("GET", "/logs?page=1&page_size=5", "MGT001")
        self.assertEnvelope(logs, 1, 5)

    def test_other_lists_return_envelopes(self):
        for who, path in [("ISUP001", "/sales"), ("ISUP001", "/my-sales"), ("ISUP001", "/reconciliations"),
                          ("MGT001", "/flight-reports"), ("INV001", "/trips"), ("INV001", "/trips?status=OPEN")]:
            status, body = call("GET", f"{path}{'&' if '?' in path else '?'}page=1&page_size=10", who)
            self.assertEqual(status, 200, path)
            self.assertEnvelope(body, 1, 10)

    def test_comms_lists_accept_page(self):
        make_rule(products=[self.p1, self.p2, self.p3], threshold=10)
        for p in (self.p1, self.p2, self.p3):
            set_stock(p, 2)
        cycle()
        _, first = call("GET", "/communication/alerts?page=1&page_size=2", "MGT001")
        _, second = call("GET", "/communication/alerts?page=2&page_size=2", "MGT001")
        self.assertEqual((first["total"], first["pages"], len(first["items"]), len(second["items"])), (3, 2, 2, 1))
        _, notes = call("GET", "/notifications?page=2&page_size=2", "INV001")
        self.assertEqual((notes["page"], len(notes["items"])), (2, 1))
        _, jobs = call("GET", "/communication/deliveries?page=1&page_size=2", "ADMIN001")
        self.assertEqual(jobs["pages"], 2)


class EndToEndTests(Base):
    def test_full_workflow(self):
        smtp = FakeSMTP().start()
        try:
            # 1. Administrator configures SMTP.
            status, _ = call("PUT", "/settings/smtp", "ADMIN001", {
                "enabled": True, "host": "127.0.0.1", "port": smtp.port, "security": "none",
                "auth_enabled": True, "username": "obs", "password": "secret",
                "sender_email": "obs@himalaya-airlines.com"})
            self.assertEqual(status, 200)
            # 2-3. Administrator creates an inventory officer with an email address.
            status, officer = call("POST", "/users", "ADMIN001", {
                "employee_id": "INV777", "name": "Test Officer", "password": "Stock#Count2026", "role": "inventory",
                "email": "officer@himalaya-airlines.com"})
            self.assertEqual(status, 200, officer)
            # 4-5. The officer logs in and creates a low-stock rule.
            rule = make_rule("INV777", products=[self.p1], threshold=10,
                             recipients=[{"recipient_type": "OWNER"}])
            self.assertEqual(rule["status"], "ACTIVE")
            # 6. A stock transaction drops below the threshold; an unrelated product also moves.
            set_stock(self.p1, 4)
            set_stock(self.p2, 1)
            # 7-10. Worker cycle: incident, queue, in-app, email.
            comms_worker.run_cycle(owner="e2e")
            incidents = self.incidents()
            self.assertEqual(len(incidents), 1)
            self.assertEqual(incidents[0]["product_id"], self.p1)
            status, inbox = call("GET", "/notifications", "INV777")
            self.assertEqual(inbox["unread"], 1)
            self.assertEqual(len(smtp.messages), 1)
            self.assertEqual(smtp.messages[0]["to"], ["officer@himalaya-airlines.com"])
            self.assertEqual(self.jobs()[0]["status"], "ACCEPTED")
            # 11. The recipient acknowledges.
            status, detail = call("POST", f"/communication/alerts/{incidents[0]['id']}/acknowledge", "INV777",
                                  {"comment": "Ordering more"})
            self.assertEqual(detail["status"], "ACKNOWLEDGED")
            # 12. Replenishment resolves it.
            status, _ = call("POST", "/main-inventory/receive", "ISUP001",
                             {"product_id": self.p1, "quantity": 50, "remarks": "delivery"})
            self.assertEqual(status, 200)
            comms_worker.run_cycle(owner="e2e")
            self.assertEqual(self.incidents()[0]["status"], "RESOLVED")
            # 13. Unauthorised users cannot see or change it.
            self.assertEqual(call("GET", f"/communication/rules/{rule['id']}", "FIN001")[0], 403)
            self.assertEqual(call("GET", f"/communication/alerts/{incidents[0]['id']}", "CREW001")[0], 403)
            self.assertEqual(call("PUT", f"/communication/rules/{rule['id']}", "INV001",
                                  rule_body(products=[self.p1]))[0], 403)
            self.assertEqual(len(smtp.messages), 1)  # the unrelated product never emailed anyone
        finally:
            smtp.stop()



# --------------------------------------------------------------------------- #
# 7.4 Stock Alert Settings
# --------------------------------------------------------------------------- #
def item_setting(product_id, who="ISUP001", expect=200, **body):
    current = q("SELECT version FROM stock_alert_item_settings WHERE product_id=?", (product_id,))
    body.setdefault("expected_version", current[0]["version"] if current else None)
    status, result = call("PUT", f"/communication/items/{product_id}/alert-settings", who, body)
    assert status == expect, (status, result)
    return result


def raw(method, path, who, body=None):
    headers = {"Authorization": f"Bearer {token(who)}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, response.headers.get("Content-Type"), response.read().decode()


class ItemSettingsTests(Base):
    def test_override_replaces_rule_threshold_and_resets(self):
        make_rule(categories=[self.category], threshold=10)
        item_setting(self.cat_a, mode="OVERRIDE", low_threshold=20)
        set_stock(self.cat_a, 15)
        set_stock(self.cat_b, 15)
        cycle()
        self.assertEqual([i["product_id"] for i in self.incidents()], [self.cat_a])
        self.assertEqual(self.incidents()[0]["threshold_at_open"], 20)
        detail = call("GET", f"/communication/items/{self.cat_a}", "ISUP001")[1]
        self.assertEqual(detail["configuration"]["status"], "CONFIGURED")
        self.assertEqual(detail["configuration"]["rules"][0]["sources"]["low"]["code"], "ITEM_OVERRIDE")
        other = call("GET", f"/communication/items/{self.cat_b}", "ISUP001")[1]
        self.assertEqual(other["configuration"]["status"], "INHERITED")
        self.assertEqual(other["configuration"]["rules"][0]["sources"]["low"]["code"], "RULE")
        # Reset to inherit: back to the rule's 10, so 15 recovers.
        item_setting(self.cat_a, mode="INHERIT")
        self.assertEqual(self.incidents()[0]["status"], "RESOLVED")

    def test_zero_is_not_unset(self):
        make_rule(products=[self.p1], trigger_type="AT_OR_BELOW", threshold=10)
        item_setting(self.p1, mode="OVERRIDE", low_threshold=0)
        row = q("SELECT low_threshold, critical_threshold FROM stock_alert_item_settings WHERE product_id=?",
                (self.p1,))[0]
        self.assertEqual(row["low_threshold"], 0)
        self.assertIsNone(row["critical_threshold"])
        set_stock(self.p1, 1)
        cycle()
        self.assertEqual(self.incidents(), [])  # 1 is not at or below 0
        set_stock(self.p1, 0)
        cycle()
        self.assertEqual(self.incidents()[0]["severity"], "OUT")
        items = call("GET", "/communication/items?mode=OVERRIDE", "ISUP001")[1]["items"]
        self.assertEqual(items[0]["low"], 0)
        self.assertTrue(items[0]["low_is_override"])

    def test_validation(self):
        make_rule(products=[self.p1], threshold=10, critical_threshold=5)
        for body, text in (
                ({"mode": "OVERRIDE", "low_threshold": 5, "critical_threshold": 5}, "lower than"),
                ({"mode": "OVERRIDE", "low_threshold": 10, "recovery_threshold": 10}, "recovery"),
                ({"mode": "OVERRIDE", "low_threshold": 2.5}, "whole number"),
                ({"mode": "OVERRIDE"}, "at least one"),
                ({"mode": "DISABLED", "low_threshold": 4}, "only be set"),
                ({"mode": "OVERRIDE", "low_threshold": 4}, "not below"),  # rule critical 5 >= new low 4
                ({"mode": "OVERRIDE", "low_threshold": -1}, "")):
            status, result = call("PUT", f"/communication/items/{self.p1}/alert-settings", "ISUP001",
                                  {**body, "expected_version": None})
            self.assertIn(status, (400, 422), (body, result))
            if text:
                self.assertIn(text, json.dumps(result), body)
        self.assertEqual(q("SELECT * FROM stock_alert_item_settings"), [])

    def test_optimistic_locking_and_permissions(self):
        item_setting(self.p1, mode="DISABLED")
        status, _ = call("PUT", f"/communication/items/{self.p1}/alert-settings", "ISUP001",
                         {"mode": "INHERIT", "expected_version": None})
        self.assertEqual(status, 409)
        self.assertEqual(call("PUT", f"/communication/items/{self.p1}/alert-settings", "INV001",
                              {"mode": "INHERIT", "expected_version": 1})[0], 403)
        self.assertEqual(call("GET", "/communication/items", "FIN001")[0], 403)
        self.assertEqual(call("GET", "/communication/items", "CREW001")[0], 403)
        self.assertEqual(call("GET", "/communication/items", "INV001")[0], 200)
        item_setting(self.p1, who="ADMIN001", mode="INHERIT")
        self.assertEqual(q("SELECT * FROM stock_alert_item_settings"), [])
        logs = q("SELECT action FROM audit_logs WHERE action='ITEM_ALERT_SETTINGS'")
        self.assertGreaterEqual(len(logs), 2)

    def test_disabled_item_is_silent_and_resolves(self):
        make_rule(categories=[self.category], threshold=10)
        set_stock(self.cat_a, 3)
        cycle()
        self.assertEqual(self.incidents()[0]["status"], "OPEN")
        item_setting(self.cat_a, mode="DISABLED")
        self.assertEqual(self.incidents()[0]["status"], "RESOLVED")
        jobs_before = len(self.jobs())
        set_stock(self.cat_a, 1)
        set_stock(self.cat_a, 0)
        cycle()
        comms_worker.run_scheduled(force_sweep=True, owner="t")
        self.assertEqual(len(self.incidents()), 1)
        self.assertEqual(len(self.jobs()), jobs_before)
        self.assertEqual(call("GET", f"/communication/items/{self.cat_a}", "ISUP001")[1]["item"]["status"],
                         "DISABLED")

    def test_item_recovery_threshold(self):
        make_rule(products=[self.p1], threshold=10)
        item_setting(self.p1, mode="OVERRIDE", recovery_threshold=12)
        set_stock(self.p1, 8)
        cycle()
        set_stock(self.p1, 11)
        cycle()
        self.assertEqual(self.incidents()[0]["status"], "OPEN")
        set_stock(self.p1, 12)
        cycle()
        self.assertEqual(self.incidents()[0]["status"], "RESOLVED")

    def test_master_switch(self):
        make_rule(products=[self.p1], threshold=10)
        status, settings = call("GET", "/communication/settings", "MGT001")
        settings.pop("last_sweep_at")
        settings["alerts_enabled"] = False
        self.assertEqual(call("PUT", "/communication/settings", "MGT001", settings)[0], 200)
        set_stock(self.p1, 3)
        cycle()
        self.assertEqual(self.incidents(), [])
        self.assertEqual(call("GET", f"/communication/items/{self.p1}", "MGT001")[1]["item"]["status"], "GLOBAL_OFF")
        settings["alerts_enabled"] = True
        call("PUT", "/communication/settings", "MGT001", settings)
        comms_worker.run_scheduled(force_sweep=True, owner="t")
        self.assertEqual(len(self.incidents()), 1)

    def test_coverage_and_list_filters(self):
        make_rule(products=[self.p1], threshold=10)
        make_rule(categories=[self.category], threshold=10, exclude_products=[self.cat_b])
        spare = q("SELECT id FROM products WHERE active=1 AND id NOT IN (?,?,?) LIMIT 1",
                  (self.p1, self.cat_a, self.cat_b))[0]["id"]
        item_setting(spare, mode="DISABLED")
        set_stock(self.p1, 5)
        cov = call("GET", "/communication/coverage", "INV001")[1]
        total = len(q("SELECT id FROM products WHERE active=1"))
        self.assertEqual(cov["total_items"], total)
        self.assertEqual(cov["explicitly_configured"] + cov["inheriting"] + cov["excluded"]
                         + cov["missing"] + cov["invalid"], total)
        self.assertGreaterEqual(cov["missing"], 1)
        self.assertIn(self.cat_b, [g["product_id"] for g in cov["gaps"]])
        below = call("GET", "/communication/items?below=true", "INV001")[1]
        self.assertEqual([i["product_id"] for i in below["items"]], [self.p1])
        self.assertEqual(call("GET", f"/communication/items/{spare}", "INV001")[1]["item"]["status"], "DISABLED")
        page = call("GET", "/communication/items?page=1&page_size=2", "INV001")[1]
        self.assertEqual(len(page["items"]), 2)
        self.assertEqual(page["total"], total)
        code = q("SELECT product_code FROM products WHERE id=?", (self.p1,))[0]["product_code"]
        found = call("GET", f"/communication/items?q={code}", "INV001")[1]
        self.assertEqual(found["items"][0]["product_id"], self.p1)

    def test_simulation_writes_nothing(self):
        make_rule(products=[self.p1], threshold=10, critical_threshold=5,
                  recipients=[{"recipient_type": "USER", "recipient_value": str(user_id("INV001")), "level": "LOW"},
                              {"recipient_type": "USER", "recipient_value": str(user_id("ISUP001")),
                               "level": "CRITICAL"}])
        before = (q("SELECT quantity FROM main_inventory WHERE product_id=?", (self.p1,)),
                  self.incidents(), self.jobs())
        status, sim = call("POST", f"/communication/items/{self.p1}/simulate", "INV001", {"quantity": 4})
        self.assertEqual(status, 200, sim)
        result = sim["results"][0]
        self.assertEqual((result["outcome"], result["severity"]), ("OPEN", "CRITICAL"))
        self.assertEqual([r["name"] for r in result["recipients"]], ["Inventory Supervisor"])
        self.assertIn("CRITICAL", result["email_preview"]["subject"])
        self.assertEqual(before, (q("SELECT quantity FROM main_inventory WHERE product_id=?", (self.p1,)),
                                  self.incidents(), self.jobs()))
        set_stock(self.p1, 8)
        cycle()
        sim = call("POST", f"/communication/items/{self.p1}/simulate", "INV001", {"quantity": 7})[1]
        self.assertEqual(sim["results"][0]["outcome"], "SUPPRESSED")
        sim = call("POST", f"/communication/items/{self.p1}/simulate", "INV001", {"quantity": 20})[1]
        self.assertEqual(sim["results"][0]["outcome"], "RESOLVE")
        self.assertEqual(len(self.jobs()), 1)

    def test_bulk_preview_and_apply(self):
        make_rule(categories=[self.category], threshold=10)
        body = {"product_ids": [self.cat_a, self.cat_b], "mode": "OVERRIDE", "low_threshold": 30,
                "dry_run": True}
        preview = call("POST", "/communication/items/bulk", "ISUP001", body)[1]
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["items"][0]["after"]["low"], 30)
        self.assertEqual(q("SELECT * FROM stock_alert_item_settings"), [])
        body["dry_run"] = False
        applied = call("POST", "/communication/items/bulk", "ISUP001", body)[1]
        self.assertTrue(applied["applied"])
        self.assertEqual(len(q("SELECT * FROM stock_alert_item_settings WHERE low_threshold=30")), 2)
        bad = call("POST", "/communication/items/bulk", "ISUP001",
                   {"product_ids": [self.cat_a], "mode": "OVERRIDE", "critical_threshold": 40, "dry_run": False})
        self.assertEqual(bad[0], 200)
        self.assertFalse(bad[1]["applied"])  # critical 40 is not below the rule's 10: reported, nothing written
        self.assertTrue(bad[1]["errors"])
        self.assertEqual(len(q("SELECT * FROM stock_alert_item_settings WHERE low_threshold=30")), 2)

    def test_export_and_import(self):
        make_rule(products=[self.p1, self.p2], threshold=10)
        item_setting(self.p1, mode="OVERRIDE", low_threshold=15)
        status, kind, text = raw("GET", "/communication/items-export", "ISUP001")
        self.assertIn("text/csv", kind)
        self.assertTrue(text.startswith("product_code,product_name,category,mode"))
        codes = {r["id"]: r["product_code"] for r in q("SELECT id, product_code FROM products")}
        csv_text = ("product_code,mode,low_threshold,critical_threshold,recovery_threshold,note\n"
                    f"{codes[self.p1]},OVERRIDE,18,6,,\n{codes[self.p2]},DISABLED,,,,seasonal\n")
        dry = call("POST", "/communication/items-import", "ISUP001", {"csv": csv_text, "dry_run": True})[1]
        self.assertEqual((len(dry["changes"]), dry["errors"], dry["applied"]), (2, [], False))
        self.assertEqual(q("SELECT low_threshold FROM stock_alert_item_settings WHERE product_id=?",
                           (self.p1,))[0]["low_threshold"], 15)
        broken = csv_text + f"{codes[self.p1]},OVERRIDE,5,,,\nNOPE,DISABLED,,,,\n"
        bad = call("POST", "/communication/items-import", "ISUP001", {"csv": broken, "dry_run": False})[1]
        self.assertFalse(bad["applied"])
        self.assertEqual(len(bad["errors"]), 2)  # duplicate code + unknown code; nothing applied
        self.assertEqual(q("SELECT low_threshold FROM stock_alert_item_settings WHERE product_id=?",
                           (self.p1,))[0]["low_threshold"], 15)
        done = call("POST", "/communication/items-import", "ISUP001", {"csv": csv_text, "dry_run": False})[1]
        self.assertTrue(done["applied"])
        self.assertEqual(q("SELECT mode FROM stock_alert_item_settings WHERE product_id=?",
                           (self.p2,))[0]["mode"], "DISABLED")

    def test_suggestion_needs_real_history(self):
        detail = call("GET", f"/communication/items/{self.p1}", "INV001")[1]
        self.assertFalse(detail["suggestion"]["available"])
        self.assertIn("Not enough usage history", detail["suggestion"]["reason"])


class ItemQuickAlertTests(Base):
    def test_set_up_update_and_disable_from_item_page(self):
        officer, manager = user_id("INV001"), user_id("ISUP001")
        detail = call("GET", f"/communication/items/{self.p1}", "ADMIN001")[1]
        self.assertEqual(detail["item"]["status"], "NOT_CONFIGURED")
        self.assertIsNone(detail["item_rule"])
        body = {"low_threshold": 10, "critical_threshold": 5, "recovery_threshold": 12,
                "low_recipient_user_ids": [officer], "critical_recipient_user_ids": [manager]}
        status, detail = call("PUT", f"/communication/items/{self.p1}/alert-rule", "ADMIN001", body)
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["item"]["status"], "CONFIGURED")
        self.assertEqual((detail["item"]["low"], detail["item"]["critical"], detail["item"]["recovery"]), (10, 5, 12))
        rule = detail["item_rule"]
        self.assertEqual((rule["status"], rule["owner_user_id"]), ("ACTIVE", user_id("ADMIN001")))  # admin sees stock (7.5), so owns it
        self.assertEqual((rule["low_recipient_user_ids"], rule["critical_recipient_user_ids"]), ([officer], [manager]))
        configured = call("GET", "/communication/items?status=CONFIGURED", "ADMIN001")[1]
        self.assertEqual([i["product_id"] for i in configured["items"]], [self.p1])
        set_stock(self.p1, 9)
        cycle()
        self.assertEqual(len(self.inbox("INV001")), 1)
        self.assertEqual(self.inbox("ISUP001"), [])
        set_stock(self.p1, 4)
        cycle()
        self.assertEqual(len(self.inbox("ISUP001")), 1)
        # Update: same rule, new version, no duplicate rule.
        body["low_threshold"] = 15
        body["recovery_threshold"] = 16
        detail = call("PUT", f"/communication/items/{self.p1}/alert-rule", "ADMIN001", body)[1]
        self.assertEqual(detail["item_rule"]["rule_id"], rule["rule_id"])
        self.assertEqual(detail["item_rule"]["low_threshold"], 15)
        self.assertGreater(detail["item_rule"]["version"], rule["version"])
        self.assertEqual(len(q("SELECT id FROM comm_rules")), 1)
        # Switch off.
        body["active"] = False
        detail = call("PUT", f"/communication/items/{self.p1}/alert-rule", "ADMIN001", body)[1]
        self.assertEqual(detail["item_rule"]["status"], "DISABLED")
        self.assertEqual(detail["item"]["status"], "NOT_CONFIGURED")
        self.assertEqual(self.incidents(status="OPEN"), [])

    def test_at_or_below_recovers_exactly_at_level(self):
        officer = user_id("INV001")
        detail = call("PUT", f"/communication/items/{self.p1}/alert-rule", "ADMIN001",
                      {"low_threshold": 10, "recovery_threshold": 12, "comparison": "AT_OR_BELOW",
                       "low_recipient_user_ids": [officer]})[1]
        self.assertEqual((detail["item"]["recovery"], detail["item_rule"]["recovery_threshold"]), (12, 12))
        set_stock(self.p1, 10)
        cycle()
        set_stock(self.p1, 11)
        cycle()
        self.assertEqual(self.incidents()[0]["status"], "OPEN")
        set_stock(self.p1, 12)
        cycle()
        self.assertEqual(self.incidents()[0]["status"], "RESOLVED")

    def test_quick_alert_validation_and_permission(self):
        officer = user_id("INV001")
        for body, text in (({"low_threshold": 10, "low_recipient_user_ids": []}, "at least one person"),
                           ({"low_threshold": 10, "critical_threshold": 10, "low_recipient_user_ids": [officer]}, "lower"),
                           ({"low_threshold": 10, "critical_recipient_user_ids": [officer]}, "critical threshold"),
                           ({"low_threshold": 10, "recovery_threshold": 9, "low_recipient_user_ids": [officer]}, "recovery"),
                           ({"low_threshold": 10, "low_recipient_user_ids": [user_id("FIN001")]}, "cannot")):
            status, result = call("PUT", f"/communication/items/{self.p1}/alert-rule", "ADMIN001", body)
            self.assertEqual(status, 400, (body, result))
            self.assertIn(text, result["detail"])
        self.assertEqual(call("PUT", f"/communication/items/{self.p1}/alert-rule", "FIN001",
                              {"low_threshold": 10, "low_recipient_user_ids": [officer]})[0], 403)
        self.assertEqual(q("SELECT id FROM comm_rules"), [])


class SnoozeAndVersionTests(Base):
    def test_snooze_pauses_reminders_until_severity_rises(self):
        make_rule(products=[self.p1], threshold=10, critical_threshold=5, frequency="REPEAT_HOURS", repeat_hours=1)
        set_stock(self.p1, 8)
        cycle()
        incident = self.incidents()[0]
        status, detail = call("POST", f"/communication/alerts/{incident['id']}/snooze", "INV001",
                              {"hours": 6, "reason": "Delivery booked for tomorrow"})
        self.assertEqual(status, 200, detail)
        connection = db()
        try:
            with alert_engine.immediate(connection):
                sent = alert_engine.run_reminders(connection, utc_now() + timedelta(hours=2),
                                                  load_settings(connection))
        finally:
            connection.close()
        self.assertEqual(sent, 0)
        self.assertEqual(call("POST", f"/communication/alerts/{incident['id']}/snooze", "INV001",
                              {"hours": 500, "reason": "too long"})[0], 400)
        set_stock(self.p1, 3)  # critical: ends the snooze and notifies
        cycle()
        incident = self.incidents()[0]
        self.assertIsNone(incident["snoozed_until"])
        self.assertEqual(incident["severity"], "CRITICAL")
        self.assertEqual(len(self.jobs()), 2)
        status, result = call("POST", f"/communication/alerts/{incident['id']}/snooze", "INV001",
                              {"hours": 2, "reason": "Please be quiet"})
        self.assertEqual(status, 400)
        self.assertIn("cannot be snoozed", result["detail"])
        events = [e["event_type"] for e in q("SELECT event_type FROM alert_events WHERE incident_id=?",
                                             (incident["id"],))]
        self.assertIn("SNOOZED", events)

    def test_rule_versions_and_incident_version(self):
        rule = make_rule(products=[self.p1], threshold=10)
        body = rule_body(products=[self.p1], threshold=15)
        self.assertEqual(call("PUT", f"/communication/rules/{rule['id']}", "INV001", body)[0], 200)
        call("POST", f"/communication/rules/{rule['id']}/disable", "INV001")
        call("POST", f"/communication/rules/{rule['id']}/activate", "INV001")
        versions = call("GET", f"/communication/rules/{rule['id']}/versions", "INV001")[1]
        self.assertEqual([v["action"] for v in versions], ["ACTIVATE_RULE", "DISABLE_RULE", "UPDATE", "CREATE"])
        self.assertEqual(versions[-1]["threshold"], 10)
        self.assertEqual(versions[-2]["threshold"], 15)
        set_stock(self.p1, 12)
        cycle()
        self.assertEqual(self.incidents()[0]["rule_version"], versions[0]["version"])

    def test_critical_can_skip_business_hours(self):
        make_rule(products=[self.p1], threshold=10, critical_threshold=5, business_hours_only=True)
        connection = db()
        connection.execute("UPDATE app_settings SET value='1' WHERE key='critical_ignores_business_hours'")
        connection.commit()
        connection.close()
        set_stock(self.p1, 2)
        cycle()
        job = self.jobs()[0]
        self.assertLessEqual(job["next_attempt_at"], job["created_at"])


class StockAlertAcceptanceTests(Base):
    """Section 21 of the Stock Alert Settings brief, through the real APIs and a fake SMTP server."""

    def test_business_acceptance(self):
        smtp = FakeSMTP().start()
        try:
            self.assertEqual(call("PUT", "/settings/smtp", "ADMIN001", {
                "enabled": True, "host": "127.0.0.1", "port": smtp.port, "security": "none",
                "auth_enabled": True, "username": "obs", "password": "secret",
                "sender_email": "obs@himalaya-airlines.com"})[0], 200)
            officer, manager = user_id("INV001"), user_id("ISUP001")
            # The administrator searches the item in Stock Alert Settings.
            code = q("SELECT product_code FROM products WHERE id=?", (self.p1,))[0]["product_code"]
            found = call("GET", f"/communication/items?q={code}", "ADMIN001")[1]["items"]
            self.assertEqual(found[0]["status"], "NOT_CONFIGURED")
            # Low 10 / critical 5, officer for low, manager for critical, once, email + in-app.
            status, rule = call("POST", "/communication/rules", "ADMIN001", rule_body(
                name="Acceptance", owner_user_id=officer, products=[self.p1], threshold=10, critical_threshold=5,
                recipients=[{"recipient_type": "USER", "recipient_value": str(officer), "level": "LOW"},
                            {"recipient_type": "USER", "recipient_value": str(manager), "level": "CRITICAL"}],
                frequency="ONCE", channel_email=True, channel_in_app=True, notify_on_recovery=False))
            self.assertEqual(status, 200, rule)
            # Recovery threshold 12 on the item.
            item_setting(self.p1, who="ADMIN001", mode="OVERRIDE", recovery_threshold=12)
            self.assertEqual(q("SELECT recovery_threshold FROM stock_alert_item_settings WHERE product_id=?",
                               (self.p1,))[0]["recovery_threshold"], 12)
            set_stock(self.p1, 20)
            comms_worker.run_cycle(owner="acc")
            self.assertEqual(smtp.messages, [])

            set_stock(self.p1, 9)
            comms_worker.run_cycle(owner="acc")
            self.assertEqual(self.incidents()[0]["severity"], "LOW", self.jobs())
            self.assertEqual([m["to"] for m in smtp.messages], [["inv@himalaya-airlines.com"]])
            self.assertEqual(len(self.inbox("INV001")), 1)
            self.assertEqual(self.inbox("MGT001"), [])

            set_stock(self.p1, 4)
            comms_worker.run_cycle(owner="acc")
            self.assertEqual(self.incidents()[0]["severity"], "CRITICAL")
            self.assertEqual([m["to"] for m in smtp.messages],
                             [["inv@himalaya-airlines.com"], ["isup@himalaya-airlines.com"]])

            set_stock(self.p1, 11)
            comms_worker.run_cycle(owner="acc")
            self.assertEqual(self.incidents()[0]["status"], "OPEN")
            set_stock(self.p1, 12)
            comms_worker.run_cycle(owner="acc")
            self.assertEqual(self.incidents()[0]["status"], "RESOLVED")
            self.assertEqual(len(smtp.messages), 2)  # no recovery message configured

            # Threshold changed to 15 in the UI: used at once, no deployment.
            item_setting(self.p1, who="ADMIN001", mode="OVERRIDE", low_threshold=15, recovery_threshold=16)
            set_stock(self.p1, 14)
            comms_worker.run_cycle(owner="acc")
            self.assertEqual(len(self.incidents(status="OPEN")), 1)
            self.assertEqual(self.incidents(status="OPEN")[0]["threshold_at_open"], 15)
            self.assertEqual(len(smtp.messages), 3)

            # Alerts disabled for the item: nothing new goes out.
            item_setting(self.p1, who="ADMIN001", mode="DISABLED")
            set_stock(self.p1, 3)
            set_stock(self.p1, 0)
            comms_worker.run_cycle(owner="acc")
            comms_worker.run_scheduled(force_sweep=True, owner="acc")
            self.assertEqual(len(smtp.messages), 3)
            self.assertEqual(self.incidents(status="OPEN"), [])
        finally:
            smtp.stop()



# --------------------------------------------------------------------------- #
# 7.5 Accounts and sign-in security
# --------------------------------------------------------------------------- #
import email as _email  # noqa: E402
import re as _re  # noqa: E402
import urllib.parse as _urlparse  # noqa: E402


def login_raw(identifier, password):
    return call("POST", "/login", body={"identifier": identifier, "password": password})


def set_setting(key, value):
    connection = db()
    connection.execute("UPDATE app_settings SET value=? WHERE key=?", (value, key))
    connection.commit()
    connection.close()


def mail_text(message):
    parsed = _email.message_from_string(message["data"])
    parts = [p for p in parsed.walk() if p.get_content_type() == "text/plain"]
    return parts[0].get_payload(decode=True).decode() if parts else message["data"]


class AccountSecurityTests(Base):
    STRONG = "Blue#Harbour77"

    def setUp(self):
        super().setUp()
        TOKENS.clear()
        connection = db()
        for table in ("user_sessions", "auth_tokens"):
            connection.execute(f"DELETE FROM {table}")
        connection.execute("DELETE FROM users WHERE employee_id LIKE 'NEW%'")
        connection.execute("UPDATE users SET two_factor=0, email_verified=0")
        connection.commit()
        connection.close()
        set_setting("app_url", "https://obs.example.test")
        set_setting("session_idle_minutes", "60")
        self.smtp = FakeSMTP().start()
        status, _ = call("PUT", "/settings/smtp", "ADMIN001", {
            "enabled": True, "host": "127.0.0.1", "port": self.smtp.port, "security": "none",
            "auth_enabled": True, "username": "obs", "password": "secret",
            "sender_email": "obs@himalaya-airlines.com"})
        assert status == 200
        self.smtp.messages.clear()

    def tearDown(self):
        self.smtp.stop()

    def test_communication_manager_is_admin_only(self):
        config = {p for p in ("comms_view", "comms_rules", "comms_groups", "comms_admin", "comms_templates",
                              "comms_item_settings", "smtp_view")}
        for role, perms in SHIPPED.items():
            if role != "admin":
                self.assertFalse(perms & config, role)
        self.assertLessEqual(config, SHIPPED["admin"])
        # Recipients can still acknowledge alerts from the bell.
        for role in ("inventory", "inventory_supervisor", "management"):
            self.assertIn("alerts_handle", SHIPPED[role])

    def test_admin_set_password_forces_change_except_shared_devices(self):
        status, dev = call("POST", "/users", "ADMIN001", {
            "employee_id": "NEWPAD1", "name": "OBS iPad 09", "password": self.STRONG, "role": "crew_supervisor"})
        self.assertEqual(status, 200, dev)
        base = {"name": dev["name"], "role": "crew_supervisor", "active": True}
        status, view = call("PUT", f"/users/{dev['id']}", "ADMIN001", {**base, "password": "Cabin#Device2026"})
        self.assertEqual(status, 200, view)
        self.assertFalse(view["must_change_password"])
        status, view = call("PUT", f"/users/{dev['id']}", "ADMIN001",
                            {**base, "password": "Cabin#Device2027", "require_change": True})
        self.assertTrue(view["must_change_password"])
        status, person = call("POST", "/users", "ADMIN001", {
            "employee_id": "NEWINV1", "name": "Sita Gurung", "password": self.STRONG, "role": "inventory"})
        self.assertEqual(status, 200, person)
        status, view = call("PUT", f"/users/{person['id']}", "ADMIN001",
                            {"name": "Sita Gurung", "role": "inventory", "active": True, "password": "Stock#Count2031"})
        self.assertTrue(view["must_change_password"])

    def test_login_by_email_and_uniform_failures(self):
        status, body = login_raw("isup@himalaya-airlines.com", "1234")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["employee_id"], "ISUP001")
        unknown = login_raw("NOBODY99", "x")
        wrong = login_raw("ISUP001", "wrong")
        self.assertEqual(unknown, wrong)  # same status, same text: no account discovery
        for _ in range(5):
            login_raw("ISUP001", "wrong")
        locked = login_raw("ISUP001", "1234")
        self.assertEqual(locked, wrong)  # a locked account answers the same way
        row = q("SELECT lock_reason FROM users WHERE employee_id='ISUP001'")[0]
        self.assertEqual(row["lock_reason"], "AUTO")
        # The temporary lock runs out on its own.
        connection = db()
        connection.execute("UPDATE users SET locked_at='2000-01-01T00:00:00Z' WHERE employee_id='ISUP001'")
        connection.commit()
        connection.close()
        self.assertEqual(login_raw("ISUP001", "1234")[0], 200)
        events = [r["action"] for r in q("SELECT action FROM audit_logs WHERE action IN "
                                         "('LOGIN_FAILED','ACCOUNT_LOCKED','LOGIN_BLOCKED')")]
        self.assertIn("ACCOUNT_LOCKED", events)
        self.assertIn("LOGIN_BLOCKED", events)

    def test_sessions_change_password_and_idle(self):
        a = login_raw("INV001", "1234")[1]["token"]
        b = login_raw("INV001", "1234")[1]["token"]
        sessions = call("GET", "/account/sessions", b)[1]
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sum(1 for s in sessions if s["current"]), 1)
        status, body = call("POST", "/change-password", b, {"old_password": "1234", "new_password": self.STRONG,
                                                             "confirm_password": self.STRONG})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["other_sessions_ended"], 1)
        self.assertEqual(call("GET", "/me", a)[0], 401)
        self.assertEqual(call("GET", "/me", b)[0], 200)
        self.assertEqual(len(self.smtp.messages), 1)  # security notice to inv@
        self.assertIn("was changed", mail_text(self.smtp.messages[0]))
        # Idle timeout.
        connection = db()
        connection.execute("UPDATE user_sessions SET last_seen_at='2000-01-01T00:00:00Z'")
        connection.commit()
        connection.close()
        self.assertEqual(call("GET", "/me", b)[0], 401)
        # Put the demo password back for the other tests.
        connection = db()
        from security import hash_password
        connection.execute("UPDATE users SET password=? WHERE employee_id='INV001'", (hash_password("1234"),))
        connection.commit()
        connection.close()

    def test_revoke_other_devices_and_logout(self):
        a = login_raw("MGT001", "1234")[1]["token"]
        b = login_raw("MGT001", "1234")[1]["token"]
        self.assertEqual(call("POST", "/account/sessions/revoke-others", b)[1]["ended"], 1)
        self.assertEqual(call("GET", "/me", a)[0], 401)
        call("POST", "/logout", b)
        self.assertEqual(call("GET", "/me", b)[0], 401)
        stored = q("SELECT token_hash FROM user_sessions")
        self.assertTrue(all(b not in r["token_hash"] and len(r["token_hash"]) == 64 for r in stored))

    def test_weak_password_must_be_changed(self):
        set_setting("require_strong_passwords", "1")
        status, body = login_raw("FIN001", "1234")
        self.assertTrue(body["must_change_password"])
        token_ = body["token"]
        self.assertEqual(call("GET", "/exchange-rates/active", token_)[0], 403)
        self.assertEqual(call("GET", "/account", token_)[0], 200)
        status, _ = call("POST", "/change-password", token_, {"old_password": "1234", "new_password": self.STRONG,
                                                               "confirm_password": self.STRONG})
        self.assertEqual(status, 200)
        self.assertEqual(call("GET", "/exchange-rates/active", token_)[0], 200)
        weak = call("POST", "/change-password", token_, {"old_password": self.STRONG, "new_password": "finance12",
                                                          "confirm_password": "finance12"})
        self.assertEqual(weak[0], 400)
        connection = db()
        from security import hash_password
        connection.execute("UPDATE users SET password=?, must_change_password=0 WHERE employee_id='FIN001'",
                           (hash_password("1234"),))
        connection.commit()
        connection.close()

    def test_two_factor_sign_in(self):
        mgt = user_id("MGT001")
        self.assertEqual(call("POST", f"/users/{mgt}/two-factor", "ADMIN001", {"enabled": True})[0], 200)
        status, body = login_raw("MGT001", "1234")
        self.assertEqual(status, 200)
        self.assertTrue(body["otp_required"])
        self.assertNotIn("token", body)
        self.assertTrue(body["email"].startswith("mg") and "*" in body["email"])
        code = _re.search(r"\b(\d{6})\b", mail_text(self.smtp.messages[-1])).group(1)
        wrong = call("POST", "/login/verify", body={"challenge": body["challenge"], "code": "000000" if code != "000000" else "111111"})
        self.assertEqual(wrong[0], 400)
        self.assertEqual(call("POST", "/login/resend", body={"challenge": body["challenge"]})[0], 429)
        ok = call("POST", "/login/verify", body={"challenge": body["challenge"], "code": code})
        self.assertEqual(ok[0], 200, ok)
        self.assertEqual(call("GET", "/me", ok[1]["token"])[0], 200)
        self.assertEqual(call("POST", "/login/verify", body={"challenge": body["challenge"], "code": code})[0], 400)
        jobs = q("SELECT body_text FROM notification_jobs WHERE kind='SECURITY'")
        self.assertTrue(all(code not in j["body_text"] for j in jobs))  # wiped after sending
        sessions = call("GET", "/account/sessions", ok[1]["token"])[1]
        self.assertTrue(sessions[0]["two_factor"])
        call("POST", f"/users/{mgt}/two-factor", "ADMIN001", {"enabled": False})

    def test_forgot_and_reset_password(self):
        old = login_raw("ISUP001", "1234")[1]["token"]
        generic = call("POST", "/password/forgot", body={"identifier": "nobody@himalaya-airlines.com"})
        real = call("POST", "/password/forgot", body={"identifier": "isup@himalaya-airlines.com"})
        self.assertEqual(generic, real)
        self.assertEqual(len(self.smtp.messages), 1)
        text = mail_text(self.smtp.messages[0])
        link = _re.search(r"https://obs\.example\.test/reset-password\?token=([\w-]+)", text)
        self.assertTrue(link, text)
        token_ = link.group(1)
        info = call("GET", f"/password/reset-info?token={token_}")[1]
        self.assertEqual(info["kind"], "RESET")
        self.assertEqual(call("POST", "/password/reset", body={"token": token_, "new_password": "short",
                                                               "confirm_password": "short"})[0], 400)
        done = call("POST", "/password/reset", body={"token": token_, "new_password": self.STRONG,
                                                     "confirm_password": self.STRONG})
        self.assertEqual(done[0], 200, done)
        self.assertEqual(call("GET", "/me", old)[0], 401)  # every session ended
        self.assertEqual(call("POST", "/password/reset", body={"token": token_, "new_password": self.STRONG,
                                                               "confirm_password": self.STRONG})[0], 400)
        self.assertEqual(login_raw("ISUP001", self.STRONG)[0], 200)
        self.assertIn("reset", mail_text(self.smtp.messages[-1]))  # security notice
        self.assertTrue(q("SELECT email_verified FROM users WHERE employee_id='ISUP001'")[0]["email_verified"])
        self.assertTrue(all(token_ not in j["body_text"] for j in q("SELECT body_text FROM notification_jobs")))
        connection = db()
        from security import hash_password
        connection.execute("UPDATE users SET password=? WHERE employee_id='ISUP001'", (hash_password("1234"),))
        connection.commit()
        connection.close()

    def test_invite_flow_and_admin_controls(self):
        status, new = call("POST", "/users/invite", "ADMIN001", {
            "employee_id": "NEW001", "name": "Asha Rai", "email": "asha.rai@himalaya-airlines.com",
            "role": "inventory"})
        self.assertEqual(status, 200, new)
        self.assertEqual(new["status"], "INVITED")
        self.assertEqual(login_raw("NEW001", "")[0], 401)
        token_ = _urlparse.parse_qs(_urlparse.urlparse(_re.search(r"https://\S+", mail_text(
            self.smtp.messages[-1])).group(0)).query)["token"][0]
        self.assertEqual(call("GET", f"/password/reset-info?token={token_}")[1]["kind"], "INVITE")
        bad = call("POST", "/password/reset", body={"token": token_, "new_password": "AshaRai#2026",
                                                    "confirm_password": "AshaRai#2026"})
        self.assertEqual(bad[0], 400)  # contains her name
        self.assertEqual(call("POST", "/password/reset", body={"token": token_, "new_password": self.STRONG,
                                                               "confirm_password": self.STRONG})[0], 200)
        users = call("GET", "/users?page=1&q=NEW001", "ADMIN001")[1]
        self.assertEqual(users["items"][0]["status"], "ACTIVE")
        signed = login_raw("asha.rai@himalaya-airlines.com", self.STRONG)
        self.assertEqual(signed[0], 200)
        # Lock by administrator ends the session and blocks sign-in until unlocked.
        self.assertEqual(call("POST", f"/users/{new['id']}/lock", "ADMIN001", {"reason": "test"})[1]["status"], "LOCKED")
        self.assertEqual(call("GET", "/me", signed[1]["token"])[0], 401)
        self.assertEqual(login_raw("NEW001", self.STRONG)[0], 401)
        call("POST", f"/users/{new['id']}/unlock", "ADMIN001")
        self.assertEqual(login_raw("NEW001", self.STRONG)[0], 200)
        # Duplicate e-mail refused; a regular user cannot manage accounts.
        dup = call("POST", "/users/invite", "ADMIN001", {"employee_id": "NEW002", "name": "X Y",
                                                         "email": "asha.rai@himalaya-airlines.com", "role": "finance"})
        self.assertEqual(dup[0], 400)
        self.assertEqual(call("POST", "/users/invite", "MGT001", {"employee_id": "NEW003", "name": "X Y",
                                                                  "email": "x@himalaya-airlines.com", "role": "admin"})[0], 403)
        # Deactivating ends sessions.
        again = login_raw("NEW001", self.STRONG)[1]["token"]
        call("DELETE", f"/users/{new['id']}", "ADMIN001")
        self.assertEqual(call("GET", "/me", again)[0], 401)

    def test_audit_log_and_overview(self):
        login_raw("FIN001", "wrong-password")
        call("GET", "/users", "FIN001")  # refused: logged as PERMISSION_DENIED
        status, audit = call("GET", "/security/audit?event=LOGIN_FAILED", "ADMIN001")
        self.assertEqual(status, 200)
        self.assertTrue(audit["items"])
        self.assertEqual(audit["items"][0]["account"], "FIN001")
        self.assertEqual(audit["items"][0]["ip_address"], "127.0.0.1")
        self.assertIn("LOGIN_FAILED", audit["events"])
        denied = call("GET", "/security/audit?event=PERMISSION_DENIED", "ADMIN001")[1]["items"]
        self.assertTrue(any(d["by"] == "FIN001" for d in denied))
        self.assertEqual(call("GET", "/security/audit", "MGT001")[0], 403)
        status, ov = call("GET", "/security/overview", "ADMIN001")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(ov["sessions"]["active"], 1)
        self.assertEqual(len(ov["sign_ins"]["trend"]), 14)
        self.assertGreaterEqual(ov["sign_ins"]["failed_24h"], 1)

    def test_recovery_code_needs_configuration(self):
        self.assertFalse(call("GET", "/admin-recovery")[1]["available"])
        self.assertEqual(call("POST", "/admin-recovery", body={
            "employee_id": "ADMIN001", "recovery_code": "anything", "new_password": self.STRONG,
            "confirm_password": self.STRONG})[0], 404)


if __name__ == "__main__":
    unittest.main()
