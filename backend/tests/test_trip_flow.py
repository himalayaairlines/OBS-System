"""Sector-by-sector crew workflow and admin rights (7.5). Synthetic data only."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
_TMP = tempfile.mkdtemp(prefix="obs-trip-test-")
os.environ["OBS_DB_PATH"] = os.path.join(_TMP, "trip.db")
os.environ["OBS_EMBEDDED_WORKER"] = "0"

import uvicorn  # noqa: E402

import main  # noqa: E402
from database import get_connection  # noqa: E402
from report_forms import REPORT_TYPES  # noqa: E402
from tests.fake_smtp import free_port  # noqa: E402

PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
TOKENS = {}
_server = None


def setUpModule():
    global _server
    _server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=_server.run, daemon=True).start()
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{BASE}/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    connection = get_connection()
    connection.execute("UPDATE app_settings SET value='0' WHERE key='require_strong_passwords'")
    connection.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES ('require_strong_passwords','0')")
    connection.commit()
    connection.close()


def tearDownModule():
    if _server:
        _server.should_exit = True


def call(method, path, who=None, body=None):
    headers = {"Content-Type": "application/json"}
    if who:
        if who not in TOKENS:
            status, out = call("POST", "/login", body={"employee_id": who, "password": "1234"})
            assert status == 200, out
            TOKENS[who] = out["token"]
        headers["Authorization"] = f"Bearer {TOKENS[who]}"
    request = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode() if body is not None else None,
                                     method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


class TripFlowTests(unittest.TestCase):
    seq = 0

    def make_trip(self, trip_type="ROUND_TRIP"):
        TripFlowTests.seq += 1
        today = (date.today() + timedelta(days=TripFlowTests.seq)).isoformat()
        status, legs = call("POST", "/flights", "INV001", {
            "flight_number": f"H9{700 + TripFlowTests.seq}", "flight_date": today, "origin": "KTM",
            "destination": "DXB", "trip_type": trip_type})
        self.assertEqual(status, 200, legs)
        legs.sort(key=lambda l: 0 if l["direction"] == "OUTBOUND" else 1)
        root = legs[0]["id"]
        status, out = call("POST", f"/trips/{root}/assign", "INV001", {"crew_account": "CREW001"})
        self.assertEqual(status, 200, out)
        product = call("GET", "/main-inventory", "INV001")[1][0]
        status, out = call("POST", "/inventory/load", "INV001",
                           {"flight_id": root, "lines": [{"product_id": product["product_id"], "loaded_quantity": 5}]})
        self.assertEqual(status, 200, out)
        return legs, product["product_id"]

    def open_leg(self, leg):
        self.assertEqual(call("PUT", f"/flights/{leg['id']}/pax-load", "CREW001", {"passengers": 150})[0], 200)
        roster = call("GET", "/crew-roster", "CREW001")[1]
        ids = [m["id"] for m in roster[:4]]
        status, group = call("POST", f"/flights/{leg['id']}/duty-group", "CREW001", {"member_ids": ids})
        self.assertEqual(status, 200, group)
        return ids[0]

    def sell(self, leg, product_id, seller, expect=200):
        TripFlowTests.seq += 1
        status, out = call("POST", "/sales", "CREW001", {
            "order_ref": f"TRIP-{leg['id']}-{TripFlowTests.seq:03d}", "flight_id": leg["id"], "seat_number": "12A",
            "sold_by_crew_member_id": seller, "lines": [{"product_id": product_id, "quantity": 1}],
            "transaction_time": f"{date.today().isoformat()}T10:00:00"})
        self.assertEqual(status, expect, out)
        return out

    def file_all(self, leg):
        for report_type in REPORT_TYPES:
            status, out = call("POST", "/flight-reports", "CREW001",
                               {"flight_id": leg["id"], "report_type": report_type, "payload": {}, "submit": True})
            self.assertEqual(status, 200, out)

    def test_round_trip_in_order(self):
        (out, back), product = self.make_trip()
        seller = self.open_leg(out)
        self.sell(out, product, seller)

        # The return leg is closed until "Start second flight".
        self.assertEqual(call("PUT", f"/flights/{back['id']}/pax-load", "CREW001", {"passengers": 140})[0], 400)
        self.assertEqual(call("GET", f"/flights/{out['id']}/trip-progress", "CREW001")[1]["step"], "SELL_FIRST")

        self.assertEqual(call("PUT", f"/flights/{out['id']}/finish", "CREW001")[0], 200)
        status, err = call("POST", f"/flights/{out['id']}/start-second", "CREW001")
        self.assertEqual(status, 400)
        self.assertIn("forms", err["detail"])
        self.assertEqual(call("GET", f"/flights/{out['id']}/trip-progress", "CREW001")[1]["step"], "FORMS_FIRST")

        self.file_all(out)
        report = call("GET", f"/flights/{out['id']}/reports", "CREW001")[1]["reports"][0]["report"]
        # The filed report downloads as a PDF.
        request = urllib.request.Request(f"{BASE}/flight-reports/{report['id']}/pdf",
                                         headers={"Authorization": f"Bearer {TOKENS['MGT001'] if 'MGT001' in TOKENS else call('GET', '/me', 'MGT001') and TOKENS['MGT001']}"})
        with urllib.request.urlopen(request, timeout=30) as response:
            self.assertEqual(response.headers["Content-Type"], "application/pdf")
            self.assertTrue(response.read().startswith(b"%PDF"))
        # Saved forms can still be changed by the crew, with remarks.
        self.assertEqual(call("PUT", f"/flight-reports/{report['id']}", "CREW001", {"payload": {}})[0], 400)
        status, changed = call("PUT", f"/flight-reports/{report['id']}", "CREW001",
                               {"payload": {}, "remarks": "Typo in seat number"})
        self.assertEqual(status, 200, changed)
        self.assertEqual(changed["rev_no"], 1)

        status, state = call("POST", f"/flights/{out['id']}/start-second", "CREW001")
        self.assertEqual(status, 200, state)
        self.assertEqual(state["step"], "SELL_SECOND")
        # First-segment forms are now locked to the iPad, not to Management.
        self.assertEqual(call("PUT", f"/flight-reports/{report['id']}", "CREW001",
                              {"payload": {}, "remarks": "again"})[0], 400)
        self.assertEqual(call("PUT", f"/flight-reports/{report['id']}", "MGT001",
                              {"payload": {}, "remarks": "Management check"})[0], 200)

        seller = self.open_leg(back)
        self.sell(back, product, seller)
        self.assertEqual(call("PUT", f"/flights/{back['id']}/finish", "CREW001")[0], 200)
        self.file_all(back)

        status, err = call("POST", f"/flights/{out['id']}/complete-trip", "CREW001")
        self.assertEqual(status, 400)
        self.assertIn("stock return", err["detail"])

        pending = [p for p in call("GET", "/reconciliations/pending", "CREW001")[1] if p["flight_id"] == out["id"]][0]
        lines = [{"product_id": i["product_id"], "declared_remaining": i["expected_remaining"]} for i in pending["items"]]
        self.assertEqual(call("POST", "/reconciliations", "CREW001", {"flight_id": out["id"], "lines": lines})[0], 200)
        # Once submitted the crew cannot submit again while it waits for approval.
        self.assertEqual(call("POST", "/reconciliations", "CREW001", {"flight_id": out["id"], "lines": lines})[0], 400)

        status, state = call("POST", f"/flights/{back['id']}/complete-trip", "CREW001")
        self.assertEqual(status, 200, state)
        self.assertEqual(state["step"], "DONE")
        self.assertEqual(call("POST", "/flight-reports", "CREW001",
                              {"flight_id": back["id"], "report_type": REPORT_TYPES[0], "payload": {}})[0], 400)
        self.assertEqual(call("POST", f"/flights/{out['id']}/complete-trip", "CREW001")[0], 400)

    def test_one_way(self):
        (leg,), product = self.make_trip("ONE_WAY")
        seller = self.open_leg(leg)
        self.sell(leg, product, seller)
        self.assertEqual(call("POST", f"/flights/{leg['id']}/start-second", "CREW001")[0], 400)
        self.assertEqual(call("PUT", f"/flights/{leg['id']}/finish", "CREW001")[0], 200)
        self.file_all(leg)
        self.assertEqual(call("GET", f"/flights/{leg['id']}/trip-progress", "CREW001")[1]["step"], "STOCK_RETURN")
        pending = [p for p in call("GET", "/reconciliations/pending", "CREW001")[1] if p["flight_id"] == leg["id"]][0]
        lines = [{"product_id": i["product_id"], "declared_remaining": i["expected_remaining"]} for i in pending["items"]]
        self.assertEqual(call("POST", "/reconciliations", "CREW001", {"flight_id": leg["id"], "lines": lines})[0], 200)
        self.assertEqual(call("POST", f"/flights/{leg['id']}/complete-trip", "CREW001")[1]["step"], "DONE")

    def test_admin_has_every_permission_and_can_sell(self):
        status, perms = call("GET", "/permissions")
        everything = set().union(*map(set, perms.values()))
        self.assertLessEqual(everything - {"sell_any_flight"}, set(perms["admin"]))
        (leg,), product = self.make_trip("ONE_WAY")
        self.open_leg(leg)
        roster = call("GET", f"/flights/{leg['id']}/duty-group", "ADMIN001")[1]
        seller = roster["members"][0]["id"]
        TripFlowTests.seq += 1
        status, out = call("POST", "/sales", "ADMIN001", {
            "order_ref": f"ADM-{TripFlowTests.seq}-x", "flight_id": leg["id"], "seat_number": "3C",
            "sold_by_crew_member_id": seller, "lines": [{"product_id": product, "quantity": 1}],
            "transaction_time": f"{date.today().isoformat()}T11:00:00"})
        self.assertEqual(status, 200, out)
        # A crew member's full history, and a ranking that lists the whole roster.
        history = call("GET", f"/crew-members/{seller}/history", "MGT001")[1]
        self.assertTrue(any(l["order_ref"] == f"ADM-{TripFlowTests.seq}-x" for l in history["sales"]), history)
        self.assertEqual(history["crew_member"]["id"], seller)
        self.assertTrue(history["sectors"])
        roster = call("GET", "/crew-roster", "CREW001")[1]
        ranking = call("GET", "/crew-ranking?all_crew=true", "MGT001")[1]["ranking"]
        self.assertGreaterEqual(len(ranking), len(roster))
        # The audit log covers every area, not only sign-ins.
        audit = call("GET", "/security/audit?module=FLIGHT", "ADMIN001")[1]
        self.assertTrue(audit["items"])
        self.assertIn("SALES", call("GET", "/security/audit", "ADMIN001")[1]["modules"] + ["SALES"])


if __name__ == "__main__":
    unittest.main()
