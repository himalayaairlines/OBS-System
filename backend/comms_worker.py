"""Background processing for stock alerts and email.

One cycle:
  1. Drain stock_change_outbox: evaluate each changed product once.
  2. Under the "scheduler" lease (so only one process does it): reminders,
     escalations and digests every cycle, and a full re-evaluation of every
     product in scope every `sweep_minutes`.
  3. Deliver due email jobs.

Run it either embedded in the API (default; set OBS_EMBEDDED_WORKER=0 to turn
off) or as its own process with `python worker.py`. Running both, or several
API workers, is safe: outbox rows and email jobs are claimed under a write
lock, incidents are unique per rule and product, and the sweep is leased.
"""

from datetime import timedelta
import logging
import os
import socket
import threading
import time
import uuid

from database import get_connection
from comms_core import iso, load_settings, parse_iso, utc_now
import alert_engine
import mailer

logger = logging.getLogger("obs.worker")
CYCLE_SECONDS = int(os.getenv("OBS_WORKER_INTERVAL", "15") or 15)
LEASE_SECONDS = 90


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def process_outbox(connect=get_connection, limit: int = 200) -> int:
    connection = connect()
    try:
        rows = connection.execute(
            "SELECT id, product_id FROM stock_change_outbox WHERE processed_at IS NULL "
            "ORDER BY id LIMIT ?", (limit,)).fetchall()
        by_product: dict[int, list[int]] = {}
        for row in rows:
            by_product.setdefault(row["product_id"], []).append(row["id"])
        handled = 0
        for product_id, ids in by_product.items():
            marks = ",".join("?" * len(ids))
            try:
                with alert_engine.immediate(connection):
                    pending = [r["id"] for r in connection.execute(
                        f"SELECT id FROM stock_change_outbox WHERE id IN ({marks}) AND processed_at IS NULL",
                        ids).fetchall()]
                    if not pending:
                        continue  # another worker took them
                    alert_engine.evaluate_product(connection, product_id)
                    connection.execute(
                        f"UPDATE stock_change_outbox SET processed_at=?, attempts=attempts+1 "
                        f"WHERE id IN ({','.join('?' * len(pending))})", (iso(utc_now()), *pending))
                handled += len(pending)
            except Exception as exc:  # noqa: BLE001 - one bad product must not stall the rest
                logger.exception("Alert evaluation failed for product %s", product_id)
                with alert_engine.immediate(connection):
                    connection.execute(
                        f"UPDATE stock_change_outbox SET attempts=attempts+1, last_error=? WHERE id IN ({marks})",
                        (str(exc)[:500], *ids))
        return handled
    finally:
        connection.close()


def acquire_lease(connection, name: str, owner: str, seconds: int = LEASE_SECONDS) -> bool:
    now = utc_now()
    with alert_engine.immediate(connection):
        connection.execute("INSERT OR IGNORE INTO worker_leases(name, owner, expires_at) VALUES (?,NULL,?)",
                           (name, iso(now)))
        changed = connection.execute(
            "UPDATE worker_leases SET owner=?, expires_at=? WHERE name=? AND (owner=? OR expires_at < ? OR owner IS NULL)",
            (owner, iso(now + timedelta(seconds=seconds)), name, owner, iso(now))).rowcount
    return changed == 1


def run_scheduled(connect=get_connection, owner: str = "local", force_sweep: bool = False) -> dict:
    connection = connect()
    summary = {"leased": False}
    try:
        if not acquire_lease(connection, "scheduler", owner):
            return summary
        summary["leased"] = True
        now = utc_now()
        settings = load_settings(connection)
        last = parse_iso(settings.get("_last_sweep_at"))
        every = timedelta(minutes=max(1, int(settings.get("sweep_minutes") or 5)))
        if force_sweep or not last or now - last >= every:
            rules = alert_engine.active_rules(connection, now, settings)
            products = alert_engine.products_in_scope(connection, rules)
            for product_id in products:
                with alert_engine.immediate(connection):
                    alert_engine.evaluate_product(connection, product_id, now, settings, rules)
            with alert_engine.immediate(connection):
                connection.execute(
                    "INSERT INTO app_settings(key,value,updated_by,updated_at) VALUES ('_last_sweep_at',?,'WORKER',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (iso(now), iso(now)))
            summary["swept"] = len(products)
        if not alert_engine.alerts_enabled(settings):
            summary["alerts_enabled"] = False  # master switch off: no reminders, escalation or digests
            return summary
        with alert_engine.immediate(connection):
            summary["reminders"] = alert_engine.run_reminders(connection, now, settings)
        with alert_engine.immediate(connection):
            summary["escalations"] = alert_engine.run_escalations(connection, now, settings)
        with alert_engine.immediate(connection):
            summary["digests"] = alert_engine.run_digests(connection, now, settings)
        return summary
    finally:
        connection.close()


def run_cycle(connect=get_connection, owner: str = "local") -> dict:
    result = {"outbox": process_outbox(connect)}
    result["scheduled"] = run_scheduled(connect, owner)
    result["delivery"] = mailer.deliver_due(connect, owner)
    return result


class Worker(threading.Thread):
    def __init__(self, connect=get_connection, interval: int = CYCLE_SECONDS):
        super().__init__(name="obs-comms-worker", daemon=True)
        self.connect = connect
        self.interval = interval
        self.owner = worker_id()
        self.stop_event = threading.Event()

    def run(self):
        logger.info("Communication worker %s started (every %ss).", self.owner, self.interval)
        while not self.stop_event.is_set():
            try:
                run_cycle(self.connect, self.owner)
            except Exception:  # noqa: BLE001 - keep the loop alive, the log has the detail
                logger.exception("Communication worker cycle failed")
            self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()


_embedded: Worker | None = None


def start_embedded():
    global _embedded
    if os.getenv("OBS_EMBEDDED_WORKER", "1").strip() == "0" or _embedded is not None:
        return None
    _embedded = Worker()
    _embedded.start()
    return _embedded


def stop_embedded():
    global _embedded
    if _embedded is not None:
        _embedded.stop()
        _embedded.join(timeout=5)
        _embedded = None
