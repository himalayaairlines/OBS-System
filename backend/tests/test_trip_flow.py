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

    def tearDown(self):
        # A device works one trip at a time (7.11), so each test starts with the
        # previous tests' trips closed off, as "Finish whole flight" would.
        connection = get_connection()
        connection.execute("UPDATE flights SET trip_completed_at=COALESCE(trip_completed_at, '2026-01-01T00:00:00'), "
                           "trip_completed_by=COALESCE(trip_completed_by, 'TEST')")
        connection.commit()
        connection.close()

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

    def open_leg(self, leg, size=4):
        self.assertEqual(call("PUT", f"/flights/{leg['id']}/pax-load", "CREW001", {"passengers": 150})[0], 200)
        roster = call("GET", "/crew-roster", "CREW001")[1]
        ids = [m["id"] for m in roster[:size]]
        status, group = call("POST", f"/flights/{leg['id']}/duty-group", "CREW001", {"member_ids": ids})
        self.assertEqual(status, 200, group)
        return ids[0]

    def sell(self, leg, product_id, seller, expect=200):
        TripFlowTests.seq += 1
        body = {
            "order_ref": f"TRIP-{leg['id']}-{TripFlowTests.seq:03d}", "flight_id": leg["id"], "seat_number": "12A",
            "lines": [{"product_id": product_id, "quantity": 1}],
            "transaction_time": f"{date.today().isoformat()}T10:00:00"}
        if seller:
            body["sold_by_crew_member_id"] = seller
        status, out = call("POST", "/sales", "CREW001", body)
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
        # 7.11 order: remaining stock first, then the forms.
        status, err = call("POST", f"/flights/{out['id']}/start-second", "CREW001")
        self.assertEqual(status, 400)
        self.assertIn("remaining stock", err["detail"])
        self.assertEqual(call("GET", f"/flights/{out['id']}/trip-progress", "CREW001")[1]["step"], "STOCK_FIRST")
        status, position = call("GET", f"/flights/{out['id']}/sector-stock", "CREW001")
        self.assertEqual(status, 200, position)
        line = [i for i in position["items"] if i["product_id"] == product][0]
        self.assertEqual((line["loaded_quantity"], line["sold_quantity"], line["expected_remaining"]), (5, 1, 4))
        self.assertFalse(position["submitted"])
        status, saved = call("POST", f"/flights/{out['id']}/sector-stock", "CREW001",
                             {"lines": [{"product_id": product, "declared_remaining": 3}], "remarks": "One damaged"})
        self.assertEqual(status, 200, saved)
        self.assertEqual((saved["submitted"], saved["total_variance"], saved["remarks"]), (True, -1, "CREW001: One damaged"))
        for who in ("MGT001", "ADMIN001", "INV001"):
            self.assertEqual(call("GET", f"/flights/{out['id']}/sector-stock", who)[0], 200, who)
        self.assertEqual(call("GET", "/sector-stock-counts", "FIN001")[0], 403)
        listed = call("GET", "/sector-stock-counts?page=1", "MGT001")[1]
        self.assertTrue(any(c["flight_id"] == out["id"] and c["total_variance"] == -1 for c in listed["items"]))
        # The commission for the sector is visible to the crew and to management.
        status, comm = call("GET", f"/flights/{out['id']}/commission", "CREW001")
        self.assertEqual(status, 200, comm)
        self.assertEqual(len(comm["members"]), 4)
        self.assertEqual(call("GET", f"/flights/{out['id']}/commission", "MGT001")[0], 200)
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
        # Locked with the forms once the second flight has started.
        self.assertEqual(call("POST", f"/flights/{out['id']}/sector-stock", "CREW001", {"lines": []})[0], 400)
        # The second sector shows what was sold before it.
        back_position = call("GET", f"/flights/{back['id']}/sector-stock", "CREW001")[1]
        self.assertEqual([i for i in back_position["items"] if i["product_id"] == product][0]["sold_before"], 1)
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
        self.assertEqual(call("GET", f"/flights/{back['id']}/trip-progress", "CREW001")[1]["step"], "STOCK_RETURN")

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
        # Pressing it again (an offline replay) is harmless.
        status, again = call("POST", f"/flights/{out['id']}/complete-trip", "CREW001")
        self.assertEqual((status, again["step"]), (200, "DONE"))

    def test_forms_any_time_and_finish_reminds(self):
        (leg,), product = self.make_trip("ONE_WAY")
        seller = self.open_leg(leg)
        self.sell(leg, product, seller)
        # 7.10: a form can be saved while the sector is still selling.
        status, out = call("POST", "/flight-reports", "CREW001",
                           {"flight_id": leg["id"], "report_type": REPORT_TYPES[0], "payload": {}, "submit": True})
        self.assertEqual(status, 200, out)
        status, done = call("PUT", f"/flights/{leg['id']}/finish", "CREW001")
        self.assertEqual(status, 200, done)
        self.assertEqual((done["reports_outstanding"], done["reports_total"]), (len(REPORT_TYPES) - 1, len(REPORT_TYPES)))

    def test_device_reassigned_same_day_after_trip_finished(self):
        (leg,), product = self.make_trip("ONE_WAY")
        TripFlowTests.seq += 1
        status, legs2 = call("POST", "/flights", "INV001", {
            "flight_number": f"H9{800 + TripFlowTests.seq}", "flight_date": leg["flight_date"], "origin": "KTM",
            "destination": "DEL", "trip_type": "ONE_WAY"})
        self.assertEqual(status, 200, legs2)
        second = legs2[0]
        # 7.11: the same iPad may be rostered to a second trip on the same day,
        # but it does not see it until the first trip is finished.
        status, out = call("POST", f"/trips/{second['id']}/assign", "INV001", {"crew_account": "CREW001"})
        self.assertEqual(status, 200, out)
        visible = {f["id"] for f in call("GET", "/flights", "CREW001")[1]}
        self.assertIn(leg["id"], visible)
        self.assertNotIn(second["id"], visible)
        seller = self.open_leg(leg)
        self.sell(leg, product, seller)
        self.assertEqual(call("PUT", f"/flights/{leg['id']}/finish", "CREW001")[0], 200)
        self.file_all(leg)
        pending = [p for p in call("GET", "/reconciliations/pending", "CREW001")[1] if p["flight_id"] == leg["id"]][0]
        lines = [{"product_id": i["product_id"], "declared_remaining": i["expected_remaining"]} for i in pending["items"]]
        self.assertEqual(call("POST", "/reconciliations", "CREW001", {"flight_id": leg["id"], "lines": lines})[0], 200)
        self.assertEqual(call("POST", f"/flights/{leg['id']}/complete-trip", "CREW001")[1]["step"], "DONE")
        visible = {f["id"] for f in call("GET", "/flights", "CREW001")[1]}
        self.assertIn(second["id"], visible)

    def test_price_and_sale_edits_are_recorded(self):
        (leg,), product = self.make_trip("ONE_WAY")
        seller = self.open_leg(leg)
        sale_tx = self.sell(leg, product, seller)
        # Price edit: inventory officer, with remarks, kept in the history.
        self.assertEqual(call("PUT", f"/products/{product}/price", "INV001", {"selling_price": 999, "remarks": ""})[0], 422)
        self.assertEqual(call("PUT", f"/products/{product}/price", "FIN001", {"selling_price": 999, "remarks": "x"})[0], 403)
        status, updated = call("PUT", f"/products/{product}/price", "INV001", {"selling_price": 999, "remarks": "New supplier price"})
        self.assertEqual((status, updated["selling_price"]), (200, 999))
        history = call("GET", f"/products/{product}/price-history", "MGT001")[1]
        self.assertEqual((history[0]["new_price"], history[0]["remarks"], history[0]["changed_by"]), (999, "New supplier price", "INV001"))
        # Sale edit by the inventory officer, with remarks, recorded line by line.
        sales = call("GET", f"/sales?flight_id={leg['id']}", "INV001")[1]
        sale = sales[0] if isinstance(sales, list) else sales["items"][0]
        self.assertIn("segment", sale)
        self.assertEqual(call("PUT", f"/sales/{sale['id']}", "INV001", {"seat_number": "14C", "edit_remarks": ""})[0], 400)
        status, edited = call("PUT", f"/sales/{sale['id']}", "INV001", {"seat_number": "14C", "edit_remarks": "Seat mis-keyed"})
        self.assertEqual((status, edited["seat_number"], edited["edit_count"]), (200, "14C", 1))
        status, edited = call("PUT", f"/sales/{sale['id']}", "ISUP001", {"quantity": 2, "edit_remarks": "Two were sold"})
        self.assertEqual((status, edited["edit_count"]), (200, 2))
        hist = call("GET", f"/sales/{sale['id']}/history", "INV001")[1]
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["changed_by"], "ISUP001")
        self.assertTrue(any(c.startswith("quantity") for c in hist[0]["changes"]))
        self.assertTrue(any(c.startswith("seat") for c in hist[1]["changes"]))

    def test_two_ipads_share_a_trip(self):
        """7.11: both iPads sell, both declare what is left, the sum is the return;
        a form marked 'submitted by the other iPad' counts once that iPad files it."""
        (leg,), product = self.make_trip("ONE_WAY")
        status, out = call("POST", f"/trips/{leg['id']}/assign", "INV001",
                           {"crew_account": "CREW001", "crew_account_2": "CSUP001"})
        self.assertEqual(status, 200, out)
        self.assertEqual(sorted(out["devices"]), ["CREW001", "CSUP001"])
        # Both devices see the flight; the second one can sell too.
        self.assertTrue(any(f["id"] == leg["id"] for f in call("GET", "/flights", "CSUP001")[1]))
        seller = self.open_leg(leg)
        TripFlowTests.seq += 1
        status, sale = call("POST", "/sales", "CSUP001", {
            "order_ref": f"TRIP-{leg['id']}-{TripFlowTests.seq:03d}", "flight_id": leg["id"], "seat_number": "2B",
            "lines": [{"product_id": product, "quantity": 1}],
            "transaction_time": f"{date.today().isoformat()}T10:00:00"})
        self.assertEqual(status, 200, sale)       # no seller needed for a normal sale
        self.sell(leg, product, seller)
        self.assertEqual(call("PUT", f"/flights/{leg['id']}/finish", "CSUP001")[0], 200)
        # 7.12: each iPad finishes for itself. iPad 1 is still selling, so the
        # flight stays OPEN for Management and iPad 1 has no return due yet.
        self.assertEqual(call("GET", "/reconciliations/pending", "CREW001")[1], [])
        self.assertEqual(call("GET", f"/flights/{leg['id']}/trip-progress", "MGT001")[1]["legs"][0]["finished"], False)
        self.assertEqual(call("GET", f"/flights/{leg['id']}/trip-progress", "CSUP001")[1]["step"], "STOCK_RETURN")
        self.sell(leg, product, seller)
        self.assertEqual(call("PUT", f"/flights/{leg['id']}/finish", "CREW001")[0], 200)
        self.assertEqual(call("GET", f"/flights/{leg['id']}/trip-progress", "MGT001")[1]["legs"][0]["finished"], True)
        # Final stock return: 5 loaded, 3 sold -> 2 expected. iPad 1 holds 2, iPad 2 holds 1 (a +1 variance).
        pending_all = call("GET", "/reconciliations/pending", "CREW001")[1]
        # Only the trip this iPad is working now is listed (7.11).
        self.assertEqual([p["flight_id"] for p in pending_all], [leg["id"]])
        pending = pending_all[0]
        self.assertEqual((pending["waiting_for"], pending["my_submitted"]), (["CREW001", "CSUP001"], False))
        status, r1 = call("POST", "/reconciliations", "CREW001",
                          {"flight_id": leg["id"], "lines": [{"product_id": product, "declared_remaining": 2}]})
        self.assertEqual((status, r1["status"], r1["waiting_for"]), (200, "WAITING", ["CSUP001"]), r1)
        # Not approvable until the second iPad has declared.
        self.assertIn(call("PUT", f"/reconciliations/{r1['id']}/review", "ISUP001", {"approve": True})[0], (400, 409))
        self.assertEqual(call("GET", f"/flights/{leg['id']}/trip-progress", "CREW001")[1]["my_stock_return_done"], True)
        self.assertEqual(call("GET", f"/flights/{leg['id']}/trip-progress", "CSUP001")[1]["my_stock_return_done"], False)
        status, r2 = call("POST", "/reconciliations", "CSUP001",
                          {"flight_id": leg["id"], "lines": [{"product_id": product, "declared_remaining": 1}]})
        self.assertEqual((status, r2["status"], r2["waiting_for"]), (200, "PENDING", []), r2)
        line = [i for i in r2["items"] if i["product_id"] == product][0]
        self.assertEqual((line["expected_remaining"], line["declared_remaining"], line["by_device"]),
                         (2, 3, {"CREW001": 2, "CSUP001": 1}))
        self.assertEqual(call("GET", f"/flights/{leg['id']}/trip-progress", "CREW001")[1]["step"], "FORMS_LAST")
        # Forms: iPad 1 files three, marks the fourth as the other iPad's.
        for report_type in REPORT_TYPES[:-1]:
            self.assertEqual(call("POST", "/flight-reports", "CREW001",
                                  {"flight_id": leg["id"], "report_type": report_type, "payload": {}, "submit": True})[0], 200)
        status, out = call("POST", "/flight-reports/defer", "CREW001", {"flight_id": leg["id"], "report_type": REPORT_TYPES[-1]})
        self.assertEqual(status, 200, out)
        sector = call("GET", f"/flights/{leg['id']}/reports", "CSUP001")[1]
        last = [e for e in sector["reports"] if e["report_type"] == REPORT_TYPES[-1]][0]
        self.assertEqual((last["status"], last["deferred_by"]), ("AWAITING_OTHER", ["CREW001"]))
        self.assertEqual(sector["awaiting_other"], [REPORT_TYPES[-1]])
        progress = call("GET", f"/flights/{leg['id']}/trip-progress", "CREW001")[1]
        self.assertEqual((progress["step"], progress["legs"][0]["reports_awaiting_other"]), ("COMPLETE", [REPORT_TYPES[-1]]))
        # The other iPad files it when it syncs.
        self.assertEqual(call("POST", "/flight-reports", "CSUP001",
                              {"flight_id": leg["id"], "report_type": REPORT_TYPES[-1], "payload": {}, "submit": True})[0], 200)
        self.assertEqual(call("GET", f"/flights/{leg['id']}/trip-progress", "CSUP001")[1]["legs"][0]["reports_awaiting_other"], [])
        self.assertEqual(call("POST", f"/flights/{leg['id']}/complete-trip", "CSUP001")[1]["step"], "DONE")

    def test_pooled_and_combo_commission(self):
        (leg,), product = self.make_trip("ONE_WAY")
        seller = self.open_leg(leg)          # 4 crew
        price = call("GET", "/products", "INV001")[1]
        price = [p for p in price if p["id"] == product][0]["selling_price"]
        self.sell(leg, product, None)        # normal sale: pooled, no seller
        self.sell(leg, product, None)
        # A combo needs a seller.
        TripFlowTests.seq += 1
        status, bundle = call("POST", "/bundles", "ISUP001", {"bundle_code": f"TB{TripFlowTests.seq}", "bundle_name": "Test combo",
                                                              "bundle_price": 1000, "items": [{"product_id": product, "quantity": 1}]})
        self.assertEqual(status, 200, bundle)
        TripFlowTests.seq += 1
        status, err = call("POST", "/sales", "CREW001", {
            "order_ref": f"TRIP-{leg['id']}-{TripFlowTests.seq:03d}", "flight_id": leg["id"], "seat_number": "5A",
            "lines": [{"bundle_id": bundle["id"], "quantity": 1}], "transaction_time": f"{date.today().isoformat()}T10:00:00"})
        self.assertIn(status, (400, 422), err)
        TripFlowTests.seq += 1
        status, ok = call("POST", "/sales", "CREW001", {
            "order_ref": f"TRIP-{leg['id']}-{TripFlowTests.seq:03d}", "flight_id": leg["id"], "seat_number": "5A",
            "sold_by_crew_member_id": seller,
            "lines": [{"bundle_id": bundle["id"], "quantity": 1}], "transaction_time": f"{date.today().isoformat()}T10:00:00"})
        self.assertEqual(status, 200, ok)
        comm = call("GET", f"/flights/{leg['id']}/commission", "CREW001")[1]
        pool = round(2 * price * 0.10, 2)
        self.assertAlmostEqual(comm["sector"]["commission_pool_npr"], pool, places=2)
        self.assertAlmostEqual(comm["sector"]["share_npr"], round(pool / 4, 2), places=2)
        self.assertAlmostEqual(comm["sector"]["combo_commission_npr"], 100.0, places=2)
        me = [m for m in comm["members"] if m["crew_member_id"] == seller][0]
        self.assertAlmostEqual(me["total_npr"], round(pool / 4 + 100, 2), places=2)
        others = [m for m in comm["members"] if m["crew_member_id"] != seller]
        self.assertTrue(all(abs(m["total_npr"] - round(pool / 4, 2)) < 0.01 for m in others))
        ranking = call("GET", "/crew-ranking", "MGT001")[1]
        self.assertEqual(ranking["combo_ranking"][0]["crew_member_id"], seller)
        self.assertAlmostEqual(ranking["combo_ranking"][0]["combo_commission_npr"], 100.0, places=2)

    def test_duty_group_size_2_to_10(self):
        (leg,), _ = self.make_trip("ONE_WAY")
        roster = call("GET", "/crew-roster", "CREW001")[1]
        ids = [m["id"] for m in roster]
        self.assertEqual(call("PUT", f"/flights/{leg['id']}/pax-load", "CREW001", {"passengers": 150})[0], 200)
        self.assertEqual(call("POST", f"/flights/{leg['id']}/duty-group", "CREW001", {"member_ids": ids[:1]})[0], 400)
        self.assertEqual(call("POST", f"/flights/{leg['id']}/duty-group", "CREW001", {"member_ids": ids[:11]})[0], 400)
        status, group = call("POST", f"/flights/{leg['id']}/duty-group", "CREW001", {"member_ids": ids[:10]})
        self.assertEqual(status, 200, group)
        self.assertEqual((len(group["members"]), group["min_size"], group["max_size"]), (10, 2, 10))

    def test_one_way(self):
        (leg,), product = self.make_trip("ONE_WAY")
        seller = self.open_leg(leg, size=2)
        self.sell(leg, product, seller)
        self.assertEqual(call("POST", f"/flights/{leg['id']}/start-second", "CREW001")[0], 400)
        self.assertEqual(call("PUT", f"/flights/{leg['id']}/finish", "CREW001")[0], 200)
        self.assertEqual(call("GET", f"/flights/{leg['id']}/trip-progress", "CREW001")[1]["step"], "STOCK_RETURN")
        self.file_all(leg)
        # A one-way flight has no sector count: the final stock return covers it.
        self.assertEqual(call("POST", f"/flights/{leg['id']}/sector-stock", "CREW001", {"lines": []})[0], 400)
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


class IndependentDevicesTests(TripFlowTests.__bases__[0]):
    """7.12: the two iPads on a trip never wait for each other."""

    seq = 500
    tearDown = TripFlowTests.tearDown
    make_trip = TripFlowTests.make_trip
    open_leg = TripFlowTests.open_leg

    def _sale(self, who, leg, product, seat="20C"):
        TripFlowTests.seq += 1
        return call("POST", "/sales", who, {
            "order_ref": f"IND-{leg['id']}-{TripFlowTests.seq:03d}", "flight_id": leg["id"], "seat_number": seat,
            "lines": [{"product_id": product, "quantity": 1}],
            "transaction_time": f"{date.today().isoformat()}T10:00:00"})

    def _return(self, who, root, product, qty):
        return call("POST", "/reconciliations", who,
                    {"flight_id": root["id"], "lines": [{"product_id": product, "declared_remaining": qty}]})

    def test_each_ipad_runs_the_whole_trip_alone(self):
        (out, back), product = self.make_trip("ROUND_TRIP")
        self.assertEqual(call("POST", f"/trips/{out['id']}/assign", "INV001",
                              {"crew_account": "CREW001", "crew_account_2": "CSUP001"})[0], 200)
        self.open_leg(out)
        a, b = "CREW001", "CSUP001"
        self.assertEqual(self._sale(a, out, product)[0], 200)
        self.assertEqual(self._sale(b, out, product, "21A")[0], 200)

        # iPad A finishes the first sector on its own and carries straight on.
        self.assertEqual(call("PUT", f"/flights/{out['id']}/finish", a)[0], 200)
        self.assertEqual(call("PUT", f"/flights/{out['id']}/finish", a)[1]["already_finished"], True)  # replay
        self.assertEqual(self._sale(a, out, product)[0], 422)      # A can no longer sell on it...
        self.assertEqual(self._sale(b, out, product, "22A")[0], 200)  # ...but B still can
        pa = call("GET", f"/flights/{out['id']}/trip-progress", a)[1]
        pb = call("GET", f"/flights/{out['id']}/trip-progress", b)[1]
        pm = call("GET", f"/flights/{out['id']}/trip-progress", "MGT001")[1]
        self.assertEqual((pa["step"], pb["step"], pm["legs"][0]["finished"]), ("STOCK_FIRST", "SELL_FIRST", False))
        self.assertEqual([d["finished"] for d in pm["legs"][0]["devices_state"]], [True, False])

        # A confirms what IT has left (no waiting for B), saves three forms and
        # leaves the fourth to B, then starts the second flight.
        status, count = call("POST", f"/flights/{out['id']}/sector-stock", a, {"lines": [{"product_id": product, "declared_remaining": 1}]})
        self.assertEqual((status, count["my_submitted"]), (200, True), count)
        self.assertEqual(call("GET", f"/flights/{out['id']}/trip-progress", a)[1]["step"], "FORMS_FIRST")
        for report_type in REPORT_TYPES[:-1]:
            self.assertEqual(call("POST", "/flight-reports", a, {"flight_id": out["id"], "report_type": report_type,
                                                                "payload": {}, "submit": True, "client_ref": f"A-{report_type}-{out['id']}"})[0], 200)
        self.assertEqual(call("POST", "/flight-reports/defer", a, {"flight_id": out["id"], "report_type": REPORT_TYPES[-1]})[0], 200)
        self.assertEqual(call("GET", f"/flights/{out['id']}/trip-progress", a)[1]["step"], "START_SECOND")
        # B has not deferred anything, so for B the forms are not done yet.
        self.assertEqual(call("GET", f"/flights/{out['id']}/trip-progress", b)[1]["legs"][0]["reports_done"], False)
        status, state = call("POST", f"/flights/{back['id']}/start-second", a)
        self.assertEqual((status, state["step"]), (200, "SELL_SECOND"), state)
        self.assertEqual(call("POST", f"/flights/{back['id']}/start-second", a)[1]["step"], "SELL_SECOND")  # replay
        # A's first-sector forms are locked on A, but B can still file its own.
        self.assertEqual(call("POST", "/flight-reports", a, {"flight_id": out["id"], "report_type": REPORT_TYPES[-1], "payload": {}, "submit": True})[0], 400)
        self.assertEqual(call("POST", "/flight-reports", b, {"flight_id": out["id"], "report_type": REPORT_TYPES[-1],
                                                            "payload": {}, "submit": True, "client_ref": f"B-last-{out['id']}"})[0], 200)
        # B also filled in a form A already saved while offline: kept as a merged revision, not refused.
        status, merged = call("POST", "/flight-reports", b, {"flight_id": out["id"], "report_type": REPORT_TYPES[0],
                                                            "payload": {}, "submit": True, "client_ref": f"B-first-{out['id']}"})
        self.assertEqual((status, merged["rev_no"]), (200, 1), merged)

        # A sells on the second flight; B cannot, because B has not started it.
        self.assertEqual(call("PUT", f"/flights/{back['id']}/pax-load", a, {"passengers": 120})[0], 200)
        roster = call("GET", "/crew-roster", a)[1]
        self.assertEqual(call("POST", f"/flights/{back['id']}/duty-group", a, {"member_ids": [m["id"] for m in roster[:3]]})[0], 200)
        self.assertEqual(self._sale(a, back, product)[0], 200)
        self.assertEqual(self._sale(b, back, product, "3A")[0], 422)
        self.assertEqual(call("PUT", f"/flights/{back['id']}/finish", a)[0], 200)
        self.assertEqual(call("GET", f"/flights/{back['id']}/trip-progress", a)[1]["step"], "STOCK_RETURN")
        # A's own final return goes in although B is still on the first sector.
        status, r1 = self._return(a, out, product, 0)
        self.assertEqual((status, r1["status"]), (200, "WAITING"), r1)
        self.assertEqual(call("GET", f"/flights/{back['id']}/trip-progress", a)[1]["step"], "FORMS_LAST")
        for report_type in REPORT_TYPES:
            self.assertEqual(call("POST", "/flight-reports", a, {"flight_id": back["id"], "report_type": report_type, "payload": {}, "submit": True})[0], 200)
        status, done = call("POST", f"/flights/{out['id']}/complete-trip", a)
        self.assertEqual((status, done["step"], done["completed"]), (200, "DONE", True), done)
        # The trip has left iPad A; Management still sees it as live because B is not done.
        self.assertEqual(call("GET", "/flights", a)[1], [])
        self.assertEqual(call("GET", "/reconciliations/pending", a)[1], [])
        self.assertIsNone(call("GET", f"/flights/{out['id']}/trip-progress", "MGT001")[1]["completed_at"])
        self.assertEqual(call("GET", f"/flights/{out['id']}/trip-progress", "MGT001")[1]["completed_devices"], [a])

        # B works through its own flow afterwards, as it would when it comes back on Wi-Fi.
        self.assertEqual(sorted(f["id"] for f in call("GET", "/flights", b)[1]), sorted([out["id"], back["id"]]))
        self.assertEqual(call("PUT", f"/flights/{out['id']}/finish", b)[0], 200)
        self.assertEqual(call("GET", f"/flights/{out['id']}/trip-progress", "MGT001")[1]["legs"][0]["finished"], True)
        self.assertEqual(call("POST", f"/flights/{out['id']}/sector-stock", b, {"lines": [{"product_id": product, "declared_remaining": 1}]})[0], 200)
        # Every first-sector form is already in (A's three, B's one), so B goes straight on.
        self.assertEqual(call("GET", f"/flights/{out['id']}/trip-progress", b)[1]["step"], "START_SECOND")
        self.assertEqual(call("POST", f"/flights/{back['id']}/start-second", b)[1]["step"], "SELL_SECOND")
        self.assertEqual(self._sale(b, back, product, "4A")[0], 200)
        self.assertEqual(call("PUT", f"/flights/{back['id']}/finish", b)[0], 200)
        status, r2 = self._return(b, out, product, 0)
        self.assertEqual((status, r2["status"]), (200, "PENDING"), r2)
        # 5 loaded, 5 sold (A 2, B 3) -> 0 expected, 0 declared in total.
        line = [i for i in r2["items"] if i["product_id"] == product][0]
        self.assertEqual((line["expected_remaining"], line["declared_remaining"]), (0, 0))
        status, done = call("POST", f"/flights/{out['id']}/complete-trip", b)
        self.assertEqual((status, done["step"]), (200, "DONE"), done)
        merged = call("GET", f"/flights/{out['id']}/trip-progress", "MGT001")[1]
        self.assertTrue(merged["completed"] and merged["completed_at"])
        self.assertEqual(sorted(merged["completed_devices"]), [a, b])
        # Management sees the sales of both iPads on the trip.
        sales = call("GET", f"/sales?flight_id={out['id']}", "MGT001")[1]
        rows = sales if isinstance(sales, list) else sales["items"]
        self.assertEqual(sorted({r["crew_id"] for r in rows} if rows and "crew_id" in rows[0] else {a, b}), [a, b])


class SalesOverviewAndFormsTests(TripFlowTests.__bases__[0]):
    """7.13: the flight-wise Sales page and the administrator's form switches."""

    tearDown = TripFlowTests.tearDown
    make_trip = TripFlowTests.make_trip
    open_leg = TripFlowTests.open_leg
    sell = TripFlowTests.sell

    def test_sales_by_flight_and_summary(self):
        (leg,), product = self.make_trip("ONE_WAY")
        seller = self.open_leg(leg)
        self.sell(leg, product, seller)
        self.sell(leg, product, seller)
        status, rows = call("GET", f"/sales/flights?flight_number={leg['flight_number']}", "MGT001")
        self.assertEqual(status, 200, rows)
        row = [r for r in rows if r["id"] == leg["id"]][0]
        self.assertEqual((row["transactions"], row["units"], row["devices"]), (2, 2, ["CREW001"]))
        self.assertGreater(row["revenue_npr"], 0)
        status, summary = call("GET", f"/sales/flights/{leg['id']}/summary", "MGT001")
        self.assertEqual(status, 200, summary)
        self.assertEqual(summary["totals"]["transactions"], 2)
        self.assertEqual(summary["by_device"][0]["device"], "CREW001")
        self.assertEqual(summary["by_product"][0]["product_id"], product)
        self.assertEqual(summary["by_product"][0]["units"], 2)
        # Filters on the sales list: by device and by product.
        page = call("GET", f"/sales?flight_id={leg['id']}&device=CREW001&product_id={product}&page=1&page_size=10", "MGT001")[1]
        self.assertEqual(page["total"], 2)
        self.assertEqual(call("GET", f"/sales?flight_id={leg['id']}&device=CSUP001&page=1", "MGT001")[1]["total"], 0)
        # Crew cannot read the overview.
        self.assertEqual(call("GET", "/sales/flights", "CREW001")[0], 403)

    def test_admin_switches_forms_for_crew(self):
        (leg,), product = self.make_trip("ONE_WAY")
        self.open_leg(leg)
        self.assertEqual(len(call("GET", "/report-forms", "CREW001")[1]), len(REPORT_TYPES))
        # Only the administrator may change the switches.
        self.assertEqual(call("PUT", "/report-forms/visibility", "MGT001", {"enabled": REPORT_TYPES[:2]})[0], 403)
        status, out = call("PUT", "/report-forms/visibility", "ADMIN001", {"enabled": REPORT_TYPES[:2]})
        self.assertEqual(status, 200, out)
        self.assertEqual([f["enabled"] for f in out], [True, True, False, False])
        try:
            self.assertEqual([f["type"] for f in call("GET", "/report-forms", "CREW001")[1]], REPORT_TYPES[:2])
            self.assertEqual(len(call("GET", "/report-forms", "MGT001")[1]), len(REPORT_TYPES))
            sector = call("GET", f"/flights/{leg['id']}/reports", "CREW001")[1]
            self.assertEqual([e["report_type"] for e in sector["reports"]], REPORT_TYPES[:2])
            # A switched-off form cannot be filed by the crew, and does not count.
            self.assertEqual(call("POST", "/flight-reports", "CREW001",
                                  {"flight_id": leg["id"], "report_type": REPORT_TYPES[-1], "payload": {}})[0], 400)
            progress = call("GET", f"/flights/{leg['id']}/trip-progress", "CREW001")[1]
            self.assertEqual(progress["legs"][0]["reports_total"], 2)
            for report_type in REPORT_TYPES[:2]:
                self.assertEqual(call("POST", "/flight-reports", "CREW001",
                                      {"flight_id": leg["id"], "report_type": report_type, "payload": {}})[0], 200)
            self.assertTrue(call("GET", f"/flights/{leg['id']}/trip-progress", "CREW001")[1]["legs"][0]["reports_done"])
        finally:
            call("PUT", "/report-forms/visibility", "ADMIN001", {"enabled": REPORT_TYPES})

    def test_queued_sale_edits_are_kept_in_history(self):
        (leg,), product = self.make_trip("ONE_WAY")
        self.open_leg(leg)
        TripFlowTests.seq += 1
        ref = f"QE-{leg['id']}-{TripFlowTests.seq:03d}"
        status, out = call("POST", "/sales/sync", "CREW001", {"transactions": [{
            "order_ref": ref, "flight_id": leg["id"], "seat_number": "9C",
            "lines": [{"product_id": product, "quantity": 1}],
            "transaction_time": f"{date.today().isoformat()}T10:00:00",
            "device_edits": [{"at": "2026-11-18T10:05:00", "remarks": "Passenger took one, not two",
                              "before": {"seat_number": "9C", "lines": [{"product_id": product, "quantity": 2}]},
                              "after": {"seat_number": "9C", "lines": [{"product_id": product, "quantity": 1}]}}],
        }]})
        self.assertEqual((status, out["results"][0]["status"]), (200, "success"), out)
        rows = call("GET", f"/sales?flight_id={leg['id']}&page=1", "MGT001")[1]["items"]
        sale = [r for r in rows if r["order_ref"] == ref][0]
        self.assertEqual((sale["edit_count"], sale["edit_remarks"]), (1, "Passenger took one, not two"))
        hist = call("GET", f"/sales/{sale['id']}/history", "MGT001")[1]
        self.assertTrue(hist[0]["on_device"])
        self.assertTrue(any(c.startswith("items:") for c in hist[0]["changes"]), hist)

    def test_differences_between_ipads_are_highlighted(self):
        (leg,), product = self.make_trip("ONE_WAY")
        self.assertEqual(call("POST", f"/trips/{leg['id']}/assign", "INV001",
                              {"crew_account": "CREW001", "crew_account_2": "CSUP001"})[0], 200)
        self.open_leg(leg)   # CREW001 sets PAX 150
        # The other iPad, offline, had typed 160: the first value is kept and the difference noted.
        status, out = call("PUT", f"/flights/{leg['id']}/pax-load", "CSUP001", {"passengers": 160})
        self.assertEqual((status, out.get("conflict"), out["passengers"]), (200, True, 150), out)
        row = [r for r in call("GET", f"/sales/flights?flight_number={leg['flight_number']}", "MGT001")[1] if r["id"] == leg["id"]][0]
        self.assertEqual(row["differences"], 1)
        diff = call("GET", f"/sales/flights/{leg['id']}/differences", "MGT001")[1]
        self.assertEqual((diff["total"], diff["conflicts"][0]["field"], diff["conflicts"][0]["device_value"]), (1, "passengers", "160"))
        # Management settles it by applying the other iPad's value, with remarks.
        cid = diff["conflicts"][0]["id"]
        self.assertEqual(call("PUT", f"/flight-conflicts/{cid}/resolve", "MGT001", {"apply_device_value": True, "remarks": ""})[0], 422)
        self.assertEqual(call("PUT", f"/flight-conflicts/{cid}/resolve", "MGT001", {"apply_device_value": True, "remarks": "Load sheet says 160"})[0], 200)
        flight = [f for f in call("GET", "/flights", "MGT001")[1] if f["id"] == leg["id"]][0]
        self.assertEqual(flight["passengers"], 160)
        self.assertEqual(call("GET", f"/sales/flights/{leg['id']}/differences", "MGT001")[1]["total"], 0)
        # A stock return with a variance shows as a difference and opens from the ledger.
        self.sell(leg, product, None)
        for who in ("CREW001", "CSUP001"):
            self.assertEqual(call("PUT", f"/flights/{leg['id']}/finish", who)[0], 200)
        self.assertEqual(call("POST", "/reconciliations", "CREW001", {"flight_id": leg["id"], "lines": [{"product_id": product, "declared_remaining": 3}]})[0], 200)
        status, r2 = call("POST", "/reconciliations", "CSUP001", {"flight_id": leg["id"], "lines": [{"product_id": product, "declared_remaining": 0}]})
        self.assertEqual((status, r2["status"], r2["total_variance"]), (200, "PENDING", -1), r2)   # 5 loaded, 1 sold, 3 returned
        diff = call("GET", f"/sales/flights/{leg['id']}/differences", "MGT001")[1]
        self.assertEqual(diff["stock_return"]["items"][0]["by_device"], {"CREW001": 3, "CSUP001": 0})
        self.assertEqual(call("PUT", f"/reconciliations/{r2['id']}/review", "ISUP001", {"approve": True, "review_remarks": "ok"})[0], 200)
        moves = call("GET", "/main-inventory/movements?reason=OBS_RETURN&page=1&page_size=5", "ISUP001")[1]["items"]
        mine = [m for m in moves if m["reconciliation_id"] == r2["id"]][0]
        self.assertEqual((mine["change_qty"], mine["line_variance"]), (3, -1))
        detail = call("GET", f"/reconciliations/{r2['id']}", "ISUP001")[1]
        self.assertEqual(detail["device_sectors"]["CSUP001"], [f"{leg['flight_number']} KTM-DXB"])
        # The Inventory Supervisor corrects the approved return with remarks; the ledger moves the difference.
        status, fixed = call("PUT", f"/reconciliations/{r2['id']}/adjust", "ISUP001",
                             {"product_id": product, "declared_remaining": 4, "remarks": "One more found in the trolley"})
        self.assertEqual((status, fixed["items"][0]["declared_remaining"], fixed["items"][0]["overridden"]), (200, 4, True), fixed)
        adj = call("GET", "/main-inventory/movements?reason=OBS_RETURN_ADJ&page=1&page_size=5", "ISUP001")[1]["items"]
        self.assertEqual(adj[0]["change_qty"], 1)


class SalesHistoryTests(TripFlowTests.__bases__[0]):
    """7.16: the administrator uploads the consumption-report workbook; the dashboard reads it."""

    def _workbook(self):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "JAN 2025"
        ws.append([" Sales Report \nJan 01-31, 2025"])
        ws.append(["SL.No", "Items Name", "Cost Price\nInclusive Vat", "Total Sold Qty (in pcs.)", "Total Cost", "Sales Price", "Total Sold Amount"])
        ws.append([1, "Gorkha", 251.14, 100, 25114, 900, 90000])
        ws.append([2, "Coke", 41.34, 50, 2067, 150, 7500])
        ws.append(["Total", None, None, None, 27181, None, 97500])
        ws2 = wb.create_sheet("FEB")
        ws2.append([" Sales Report \nFeb 01-14, 2025", None, None, None, None, None, None, " Sales Report \nFeb 15-28, 2025"])
        ws2.append(["SL.No", "Product Name", "Cost Price\nInclusive Vat", "Total Sold Qty (in pcs.)", "Total Cost", "Sales Price", None,
                    "SL.No", "Product Name", "Cost Price\nInclusive Vat", "Total Sold Qty (in pcs.)", "Total Cost", "Sales Price"])
        ws2.append([1, "Gorkha", 251.14, 10, 2511.4, 900, None, 1, "Gorkha", 251.14, 20, 5022.8, 900])
        ws2.append([None] * 13)
        ws2.append([" Sales Report \nFeb 01-28, 2025"])
        ws2.append(["SL.No", "Product Name", "Cost Price\nInclusive Vat", "Total Sold Qty (in pcs.)", "Total Cost", "Sales Price", "Total Sold Amount"])
        ws2.append([1, "Gorkha", 251.14, 30, 7534.2, 900, 27000])
        ws2.append(["Total", None, None, None, 7534.2, None, 27000])
        from io import BytesIO
        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def _upload(self, who):
        import urllib.request
        boundary = "----obsboundary"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"report.xlsx\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode() + self._workbook() + f"\r\n--{boundary}--\r\n".encode()
        if who not in TOKENS:
            call("GET", "/health", who)
        request = urllib.request.Request(f"{BASE}/sales-history/upload", data=body, method="POST", headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}", "Authorization": f"Bearer {TOKENS[who]}"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def test_upload_and_dashboard_history(self):
        self.assertEqual(self._upload("MGT001")[0], 403)      # administrator only
        status, out = self._upload("ADMIN001")
        self.assertEqual(status, 200, out)
        # The whole-month February block wins over the two half-month blocks.
        self.assertEqual((out["periods"], out["rows"], out["months"]), (2, 3, ["2025-01", "2025-02"]))
        self.assertAlmostEqual(out["revenue"], 97500 + 27000)
        summary = call("GET", "/sales-history/summary?year=2025", "MGT001")[1]
        self.assertEqual((summary["available"], summary["units"], summary["years"]), (True, 180, [2025]))
        self.assertEqual([m["month"] for m in summary["months"]], [1, 2])
        self.assertEqual(summary["products"][0]["product_name"], "Gorkha")
        self.assertAlmostEqual(summary["products"][0]["revenue"], 117000)
        feb = call("GET", "/sales-history/summary?year=2025&month=2", "FIN001")[1]
        self.assertAlmostEqual(feb["revenue"], 27000)
        # Uploading again replaces, not duplicates.
        self.assertEqual(self._upload("ADMIN001")[0], 200)
        self.assertAlmostEqual(call("GET", "/sales-history/summary?year=2025", "MGT001")[1]["revenue"], 124500)
        uploads = call("GET", "/sales-history/uploads", "MGT001")[1]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(call("DELETE", f"/sales-history/uploads/{uploads[0]['id']}", "ADMIN001")[0], 200)
        self.assertFalse(call("GET", "/sales-history/summary?year=2025", "MGT001")[1]["available"])


class SalesReportTests(TripFlowTests.__bases__[0]):
    """7.17: the standard onboard sales report, on screen and as a workbook."""

    tearDown = TripFlowTests.tearDown
    make_trip = TripFlowTests.make_trip
    open_leg = TripFlowTests.open_leg
    sell = TripFlowTests.sell

    def test_sales_report_and_workbook(self):
        (leg,), product = self.make_trip("ONE_WAY")
        seller = self.open_leg(leg)
        self.sell(leg, product, seller)
        year = int(leg["flight_date"][:4])
        status, rep = call("GET", f"/reports/sales-report?year={year}", "MGT001")
        self.assertEqual(status, 200, rep)
        self.assertIn("Onboard Sales Report", rep["title"])
        sector = [x for x in rep["sectors"] if x["id"] == leg["id"]][0]
        self.assertEqual((sector["transactions"], sector["units"], sector["devices"]), (1, 1, "CREW001"))
        self.assertTrue(rep["summary"]["revenue"] > 0)
        self.assertEqual(call("GET", f"/reports/sales-report?year={year}", "CREW001")[0], 403)
        # The workbook: six sheets, summary first.
        import urllib.request
        request = urllib.request.Request(f"{BASE}/reports/sales-report.xlsx?year={year}",
                                         headers={"Authorization": f"Bearer {TOKENS['MGT001']}"})
        with urllib.request.urlopen(request, timeout=30) as response:
            self.assertEqual(response.status, 200)
            body = response.read()
            self.assertIn("H9-Onboard-Sales-Report", response.headers.get("Content-Disposition", ""))
        from io import BytesIO
        from openpyxl import load_workbook
        wb = load_workbook(BytesIO(body))
        self.assertEqual(wb.sheetnames, ["Summary", "By sector", "By route", "By product", "By iPad", "Currency & daily"])
        self.assertIn("Onboard Sales Report", str(wb["Summary"]["A1"].value))
