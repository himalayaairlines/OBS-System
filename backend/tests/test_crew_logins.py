"""7.18: own crew logins, offline multi-phone selling, currency rounding,
cash sheets, forms outside the flight flow, diversions, monthly rates, and a
permission sweep over every route. Synthetic data only, throw-away database."""

import json
import math
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
_TMP = tempfile.mkdtemp(prefix="obs-crew-test-")
os.environ.setdefault("OBS_DB_PATH", os.path.join(_TMP, "crew.db"))
os.environ["OBS_EMBEDDED_WORKER"] = "0"

import uvicorn  # noqa: E402

import main  # noqa: E402
from database import DEFAULT_CREW_PASSWORD, get_connection  # noqa: E402
from report_forms import REPORT_TYPES  # noqa: E402
from tests.fake_smtp import free_port  # noqa: E402

PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
TOKENS = {}
NEW_PASSWORD = "Trolley#Count2026"
CREW = ["10046", "10131", "10043", "10049"]      # four crew members from the roster
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
    connection.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES ('require_strong_passwords','0')")
    connection.commit()
    connection.close()


def tearDownModule():
    if _server:
        _server.should_exit = True


def raw(method, path, token=None, body=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode() if body is not None else None,
                                     method=method, headers=h)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
            ctype = response.headers.get("Content-Type", "")
            return response.status, (json.loads(data or b"null") if "json" in ctype else data.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        data = exc.read()
        try:
            return exc.code, json.loads(data or b"null")
        except ValueError:
            return exc.code, data.decode("utf-8", "replace")


def login(who):
    """Sign in; a crew login changes its default password first, as the app forces."""
    if who in TOKENS:
        return TOKENS[who]
    from routers import auth as _auth
    _auth._ip_failures.clear()          # other test modules share this server's address
    candidates = (NEW_PASSWORD, DEFAULT_CREW_PASSWORD) if who.isdigit() else ("1234", NEW_PASSWORD)
    for password in candidates:
        status, out = raw("POST", "/login", body={"employee_id": who, "password": password})
        if status == 200:
            break
    assert status == 200, (who, out)
    token = out["token"]
    if out.get("user", {}).get("must_change_password") or out.get("must_change_password"):
        status, changed = raw("POST", "/change-password", token, {
            "old_password": password, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
        assert status == 200, changed
        status, out = raw("POST", "/login", body={"employee_id": who, "password": NEW_PASSWORD})
        assert status == 200, out
        token = out["token"]
    TOKENS[who] = token
    return token


def call(method, path, who=None, body=None):
    return raw(method, path, login(who) if who else None, body)


class CrewSystemTests(unittest.TestCase):
    seq = 0

    @classmethod
    def setUpClass(cls):
        connection = get_connection()
        cls.member = {r["crew_code"]: r["id"] for r in connection.execute("SELECT id, crew_code FROM crew_members")}
        connection.close()

    def tearDown(self):
        connection = get_connection()
        connection.execute("UPDATE flights SET trip_completed_at=COALESCE(trip_completed_at, '2026-01-01T00:00:00')")
        connection.commit()
        connection.close()

    # ------------------------------------------------------------------ helpers
    def trip(self, trip_type="ROUND_TRIP", loaded=10, crew=CREW, days=0):
        CrewSystemTests.seq += 1
        flight_date = (date.today() + timedelta(days=days)).isoformat()
        status, legs = call("POST", "/flights", "INV001", {
            "flight_number": f"H9{300 + CrewSystemTests.seq}", "flight_date": flight_date, "origin": "KTM",
            "destination": "DXB", "trip_type": trip_type})
        self.assertEqual(status, 200, legs)
        legs.sort(key=lambda l: 0 if l["direction"] == "OUTBOUND" else 1)
        status, out = call("POST", f"/trips/{legs[0]['id']}/assign", "INV001", {"crew_accounts": list(crew)})
        self.assertEqual(status, 200, out)
        products = call("GET", "/main-inventory", "INV001")[1]
        product = max(products, key=lambda p: p["quantity"])
        status, out = call("POST", "/inventory/load", "INV001",
                           {"flight_id": legs[0]["id"], "lines": [{"product_id": product["product_id"], "loaded_quantity": loaded}]})
        self.assertEqual(status, 200, out)
        price = [p for p in call("GET", "/products", "INV001")[1] if p["id"] == product["product_id"]][0]["selling_price"]
        return legs, product["product_id"], float(price)

    def open_leg(self, leg, who=CREW[0]):
        self.assertEqual(call("PUT", f"/flights/{leg['id']}/pax-load", who, {"passengers": 150})[0], 200)
        status, group = call("POST", f"/flights/{leg['id']}/duty-group", who,
                             {"member_ids": [self.member[c] for c in CREW]})
        self.assertEqual(status, 200, group)
        return group

    def offline_sale(self, who, leg, product, qty=1, currency="NPR", amount=None, seat="12A"):
        CrewSystemTests.seq += 1
        tx = {"order_ref": f"OFF-{who}-{leg['id']}-{CrewSystemTests.seq:04d}", "flight_id": leg["id"],
              "seat_number": seat, "lines": [{"product_id": product, "quantity": qty}],
              "payment_currency": currency, "transaction_time": f"{date.today().isoformat()}T10:00:00"}
        if amount is not None:
            tx["payment_amount"] = amount
        status, out = call("POST", "/sales/sync", who, {"transactions": [tx]})
        self.assertEqual(status, 200, out)
        return out["results"][0]

    # ------------------------------------------------------------------ tests
    def test_01_every_crew_member_has_a_login(self):
        # A new roster entry: its login starts on the default, which must be changed first.
        code = f"98{int(time.time()) % 1000:03d}"
        self.assertEqual(call("POST", "/crew-members", "ADMIN001", {"crew_code": code, "name": "NEW JOINER", "position": "Cabin Crew"})[0], 200)
        status, out = raw("POST", "/login", body={"employee_id": code, "password": DEFAULT_CREW_PASSWORD})
        self.assertEqual(status, 200, out)
        token = out["token"]
        self.assertTrue(out.get("user", {}).get("must_change_password", out.get("must_change_password")), out)
        # Nothing but the password change works until then.
        self.assertEqual(raw("GET", "/flights", token)[0], 403)
        token = login(CREW[0])
        status, me = raw("GET", "/me", token)
        self.assertEqual((status, me["role"]), (200, "crew"), me)
        accounts = {a["employee_id"] for a in call("GET", "/crew-accounts", "INV001")[1]}
        self.assertTrue(set(CREW) <= accounts)
        # A new roster entry gets a login straight away; deactivating it disables the login.
        status, member = call("POST", "/crew-members", "ADMIN001", {"crew_code": "99901", "name": "TEST CREW", "position": "Cabin Crew"})
        self.assertEqual(status, 200, member)
        self.assertEqual(raw("POST", "/login", body={"employee_id": "99901", "password": DEFAULT_CREW_PASSWORD})[0], 200)
        self.assertEqual(call("PUT", f"/crew-members/{member['id']}", "ADMIN001",
                              {"crew_code": "99901", "name": "TEST CREW", "position": "Cabin Crew", "active": False})[0], 200)
        self.assertNotEqual(raw("POST", "/login", body={"employee_id": "99901", "password": DEFAULT_CREW_PASSWORD})[0], 200)

    def test_02_offline_phones_share_one_trolley(self):
        (out, back), product, price = self.trip(loaded=10)
        self.open_leg(out)
        # Another phone entering a different list does not replace the first one.
        status, again = call("POST", f"/flights/{out['id']}/duty-group", CREW[1],
                             {"member_ids": [self.member[c] for c in CREW[:2]]})
        self.assertEqual((status, again.get("already_formed")), (200, True), again)
        # Four phones, each sold 2 of the 10 offline, upload when they land.
        for who in CREW:
            for _ in range(2):
                self.assertEqual(self.offline_sale(who, out, product)["status"], "success")
        position = call("GET", f"/flights/{out['id']}/sector-stock", "MGT001")[1]
        line = [i for i in position["items"] if i["product_id"] == product][0]
        self.assertEqual((line["sold_quantity"], line["expected_remaining"]), (8, 2))
        # The seller of each sale is the login; the ranking shows all four.
        ranking = call("GET", f"/crew-ranking?year={out['flight_date'][:4]}", "MGT001")[1]["sales_ranking"]
        mine = {r["crew_code"]: r for r in ranking}
        for who in CREW:
            self.assertGreaterEqual(mine[who]["units"], 2)
        # A fifth upload claiming more than is left is kept (the cash is real) and flagged.
        result = self.offline_sale(CREW[0], out, product, qty=3)
        self.assertEqual(result["status"], "success", result)
        diff = call("GET", f"/sales/flights/{out['id']}/differences", "MGT001")[1]
        self.assertTrue(any(c["field"] == "oversell" for c in diff["conflicts"]), diff)
        # A live (online) sale beyond the stock is still refused.
        CrewSystemTests.seq += 1
        status, err = call("POST", "/sales", CREW[1], {
            "order_ref": f"LIVE-{out['id']}-{CrewSystemTests.seq:04d}", "flight_id": out["id"], "seat_number": "3C",
            "lines": [{"product_id": product, "quantity": 1}], "transaction_time": f"{date.today().isoformat()}T11:00:00"})
        self.assertEqual(status, 422, err)

    def test_03_currency_is_rounded_up_to_the_next_unit(self):
        month = date.today().strftime("%Y-%m")
        self.assertEqual(call("POST", "/exchange-rates/month", "FIN001", {
            "month": month, "rates": [{"currency_code": "AED", "unit": 1, "rate_to_npr": 40.80}]})[0], 200)
        (leg,), product, price = self.trip("ONE_WAY")
        self.open_leg(leg)
        result = self.offline_sale(CREW[0], leg, product, qty=1, currency="AED")
        expected = math.ceil(price / 40.80 - 1e-9)
        self.assertEqual((result["currency"], result["total_currency"]), ("AED", float(expected)), result)
        self.assertEqual(result["total_currency"], float(int(result["total_currency"])))   # a whole number
        sales = call("GET", f"/sales?flight_id={leg['id']}&page=1", "MGT001")[1]["items"]
        self.assertAlmostEqual(sum(s["transaction_currency_amount"] for s in sales), expected)
        # 3.0 stays 3; anything above goes up.
        from routers.sales import currency_amount
        self.assertEqual(currency_amount(122.4, 40.8, "AED"), 3.0)
        self.assertEqual(currency_amount(122.5, 40.8, "AED"), 4.0)
        self.assertEqual(currency_amount(102.0, 40.8, "AED"), 3.0)    # 2.5 -> 3
        self.assertEqual(currency_amount(150.0, 1.0, "NPR"), 150.0)

    def test_04_stock_counts_once_for_the_trolley_and_the_flow_needs_no_forms(self):
        (out, back), product, price = self.trip(loaded=10)
        self.open_leg(out)
        for who in CREW:
            self.offline_sale(who, out, product)
        for who in CREW:
            self.assertEqual(call("PUT", f"/flights/{out['id']}/finish", who)[0], 200)
            self.assertEqual(call("GET", f"/flights/{out['id']}/trip-progress", who)[1]["step"], "STOCK_FIRST")
        # A counts the trolley: 10 loaded, 4 sold -> 6 expected; A finds 6.
        status, count = call("POST", f"/flights/{out['id']}/sector-stock", CREW[0],
                             {"lines": [{"product_id": product, "declared_remaining": 6}]})
        self.assertEqual((status, count["submitted"]), (200, True), count)
        # B counted 5 on another phone while offline: A's stands, B's is a difference.
        status, b = call("POST", f"/flights/{out['id']}/sector-stock", CREW[1],
                         {"lines": [{"product_id": product, "declared_remaining": 5}]})
        self.assertEqual((status, b.get("conflict")), (200, True), b)
        self.assertEqual([i for i in b["items"] if i["product_id"] == product][0]["declared_remaining"], 6)
        # C says another crew member did it.
        status, c = call("POST", f"/flights/{out['id']}/sector-stock", CREW[2], {"by_other": True})
        self.assertEqual(status, 200, c)
        for who in CREW[:3]:
            self.assertEqual(call("GET", f"/flights/{out['id']}/trip-progress", who)[1]["step"], "START_SECOND", who)
        # No form was filed and the second flight still starts.
        for who in CREW:
            self.assertEqual(call("POST", f"/flights/{back['id']}/start-second", who)[0], 200, who)
        self.assertEqual(call("PUT", f"/flights/{back['id']}/pax-load", CREW[0], {"passengers": 140})[0], 200)
        self.assertEqual(call("POST", f"/flights/{back['id']}/duty-group", CREW[0],
                              {"member_ids": [self.member[c] for c in CREW]})[0], 200)
        for who in CREW:
            self.offline_sale(who, back, product, seat="20A")
            self.assertEqual(call("PUT", f"/flights/{back['id']}/finish", who)[0], 200)
        # Final return: 10 - 4 - 4 = 2 expected, A declares 2.
        status, r1 = call("POST", "/reconciliations", CREW[0], {"flight_id": out["id"], "lines": [{"product_id": product, "declared_remaining": 2}]})
        self.assertEqual((status, r1["status"]), (200, "PENDING"), r1)
        line = [i for i in r1["items"] if i["product_id"] == product][0]
        self.assertEqual((line["expected_remaining"], line["declared_remaining"], line["variance"]), (2, 2, 0))
        status, r2 = call("POST", "/reconciliations", CREW[1], {"flight_id": out["id"], "lines": [{"product_id": product, "declared_remaining": 3}]})
        self.assertEqual((status, r2.get("conflict")), (200, True), r2)
        self.assertEqual(call("POST", f"/flights/{out['id']}/stock-return-by-other", CREW[2])[0], 200)
        for who in CREW:
            status, done = call("POST", f"/flights/{out['id']}/complete-trip", who)
            self.assertEqual((status, done["step"]), (200, "DONE"), (who, done))
        diff = call("GET", f"/sales/flights/{out['id']}/differences", "MGT001")[1]
        self.assertTrue(any(c["field"] == "stock_count" for c in diff["conflicts"]))
        diff = call("GET", f"/sales/flights/{back['id']}/differences", "MGT001")[1]
        merged = call("GET", f"/flights/{out['id']}/trip-progress", "MGT001")[1]
        self.assertTrue(merged["completed"])

    def test_05_forms_outside_the_flow_draft_then_final(self):
        (leg,), product, price = self.trip("ONE_WAY")
        sectors = call("GET", "/flight-reports/my-sectors", CREW[0])[1]
        self.assertTrue(any(s["id"] == leg["id"] for s in sectors))
        t = REPORT_TYPES[0]
        # A draft, before the flight has even been opened.
        status, draft = call("POST", "/flight-reports", CREW[0], {"flight_id": leg["id"], "report_type": t, "payload": {}, "submit": False})
        self.assertEqual((status, draft["status"]), (200, "DRAFT"), draft)
        status, draft = call("POST", "/flight-reports", CREW[1], {"flight_id": leg["id"], "report_type": t, "payload": {}, "submit": False})
        self.assertEqual(status, 200, draft)
        # B leaves the rest to a colleague with one press.
        status, sector = call("POST", "/flight-reports/defer-all", CREW[2], {"flight_id": leg["id"]})
        self.assertEqual(status, 200, sector)
        # Final submission locks it for the crew; the audit log names who.
        status, sector = call("POST", "/flight-reports/final-submit", CREW[0], {"flight_id": leg["id"]})
        self.assertEqual(status, 200, sector)
        entry = [e for e in sector["reports"] if e["report_type"] == t][0]
        self.assertEqual((entry["status"], entry["report"]["submitted_by"]), ("SUBMITTED", CREW[0]))
        self.assertEqual(call("POST", "/flight-reports", CREW[1], {"flight_id": leg["id"], "report_type": t, "payload": {}, "submit": False})[0], 400)
        self.assertEqual(call("PUT", f"/flight-reports/{entry['report']['id']}", CREW[0], {"payload": {}, "remarks": "x"})[0], 403)
        self.assertEqual(call("POST", f"/flight-reports/{entry['report']['id']}/unsubmit", CREW[0])[0], 403)
        again = call("POST", "/flight-reports/final-submit", CREW[0], {"flight_id": leg["id"]})
        self.assertEqual((again[0], again[1].get("already_submitted")), (200, True))
        # Management: trip by trip, with pending forms.
        trips = call("GET", f"/flight-reports/trips?date={leg['flight_date']}", "MGT001")[1]
        mine = [x for x in trips if x["root_id"] == leg["id"]][0]
        self.assertEqual(mine["label"], f"KTM-DXB ({leg['flight_date']})")
        states = {f["report_type"]: f["status"] for f in mine["legs"][0]["forms"]}
        self.assertEqual(states[t], "SUBMITTED")
        self.assertTrue(all(v in ("PENDING", "AWAITING_OTHER") for k, v in states.items() if k != t))
        # Inventory reads but cannot write or switch forms off.
        self.assertEqual(call("GET", f"/flight-reports/trips?date={leg['flight_date']}", "INV001")[0], 200)
        # Inventory reads the form definitions (to open a form read-only), Finance does not.
        self.assertEqual(call("GET", "/report-forms", "INV001")[0], 200)
        self.assertEqual(call("GET", "/report-forms", "FIN001")[0], 403)
        self.assertEqual(call("GET", f"/flights/{leg['id']}/reports", "INV001")[0], 200)
        self.assertEqual(call("POST", "/flight-reports", "INV001", {"flight_id": leg["id"], "report_type": t, "payload": {}})[0], 403)
        self.assertEqual(call("PUT", "/report-forms/visibility", "INV001", {"enabled": REPORT_TYPES})[0], 403)
        # A crew member not on the sector cannot see it.
        self.assertEqual(call("GET", f"/flights/{leg['id']}/reports", "10295")[0], 403)
        self.assertEqual(call("GET", "/flight-reports/trips", CREW[0])[0], 403)

    def test_06_diversion(self):
        (leg,), product, price = self.trip("ONE_WAY")
        self.open_leg(leg)
        self.assertEqual(call("POST", f"/flights/{leg['id']}/diversion", CREW[0],
                              {"diverted_to": "12", "reason": "Weather", "remarks": "x"})[0], 422)
        self.assertEqual(call("POST", f"/flights/{leg['id']}/diversion", CREW[0],
                              {"diverted_to": "KTM", "reason": "Weather", "remarks": "Fog at DXB"})[0], 400)
        status, out = call("POST", f"/flights/{leg['id']}/diversion", CREW[0],
                           {"diverted_to": "mct", "reason": "Weather", "remarks": "Fog at DXB", "client_ref": f"DV-{leg['id']}"})
        self.assertEqual(status, 200, out)
        replay = call("POST", f"/flights/{leg['id']}/diversion", CREW[0],
                      {"diverted_to": "MCT", "reason": "Weather", "remarks": "Fog at DXB", "client_ref": f"DV-{leg['id']}"})
        self.assertEqual((replay[0], len(replay[1]["diversions"])), (200, 1))
        flight = [f for f in call("GET", "/flights", "MGT001")[1] if f["id"] == leg["id"]][0]
        self.assertEqual((flight["destination"], flight["original_destination"]), ("MCT", "DXB"))
        self.assertEqual(call("POST", f"/flights/{leg['id']}/diversion", "10295",
                              {"diverted_to": "BKK", "reason": "x1", "remarks": "x1"})[0], 403)
        self.assertEqual(call("POST", f"/flights/{leg['id']}/diversion", "FIN001",
                              {"diverted_to": "BKK", "reason": "x1", "remarks": "x1"})[0], 403)

    def test_07_monthly_rates(self):
        month = "2031-02"
        self.assertEqual(call("POST", "/exchange-rates/month", "FIN001", {
            "month": "2031-01", "rates": [{"currency_code": "USD", "rate_to_npr": 134.5}, {"currency_code": "AED", "rate_to_npr": 36.6}]})[0], 200)
        self.assertEqual(call("POST", "/exchange-rates/month", "INV001", {"month": month, "rates": []})[0], 403)
        status, copied = call("POST", "/exchange-rates/month/copy", "ADMIN001", {"month": month})
        self.assertEqual(status, 200, copied)
        rates = {r["currency_code"]: r for r in call("GET", f"/exchange-rates/month?month={month}", "FIN001")[1]["rates"]}
        self.assertEqual((rates["USD"]["rate_to_npr"], rates["USD"]["set_for_month"]), (134.5, True))
        self.assertEqual((rates["USD"]["effective_from"], rates["USD"]["effective_to"]), ("2031-02-01", "2031-02-28"))
        self.assertEqual(call("GET", "/exchange-rates/month?month=2031-13", "FIN001")[0], 400)

    def test_08_cash_sheet(self):
        (leg,), product, price = self.trip("ONE_WAY", crew=CREW[:2])
        self.open_leg(leg)
        a, b = CREW[0], CREW[1]
        for _ in range(3):
            self.offline_sale(a, leg, product)                 # A: 3 x price NPR
        self.offline_sale(b, leg, product)                     # B: 1 x price NPR
        # Inventory hands out the floating cash.
        status, sheet = call("PUT", f"/cash/flights/{leg['id']}/blocks", "INV001", {
            "crew_account": a, "section": "float_open", "denominations": {"100": 25, "50": 25, "20": 25, "10": 25, "5": 25}})
        self.assertEqual(status, 200, sheet)
        self.assertEqual([x for x in sheet["blocks"] if x["crew_account"] == a][0]["float_open_total"], 4625)
        # A crew member fills their own block only; finance never.
        self.assertEqual(call("PUT", f"/cash/flights/{leg['id']}/blocks", a, {
            "crew_account": b, "section": "collection", "denominations": {}})[0], 403)
        self.assertEqual(call("PUT", f"/cash/flights/{leg['id']}/blocks", "FIN001", {
            "crew_account": a, "section": "collection", "denominations": {}})[0], 403)
        self.assertEqual(call("GET", f"/cash/flights/{leg['id']}", "FIN001")[0], 200)
        self.assertEqual(call("GET", f"/cash/flights/{leg['id']}", "10295")[0], 403)
        # A saves a collection that is NPR 100 short.
        short = int(price * 3) - 100
        notes = {"100": short // 100} if short % 100 == 0 else {"5": short // 5}
        status, sheet = call("PUT", f"/cash/flights/{leg['id']}/blocks", a, {"crew_account": a, "section": "collection", "denominations": notes})
        self.assertEqual(status, 200, sheet)
        # Saved: only the administrator may change it, and only with remarks.
        self.assertEqual(call("PUT", f"/cash/flights/{leg['id']}/blocks", a, {"crew_account": a, "section": "collection", "denominations": notes})[0], 400)
        # No notice until every rostered phone has uploaded (finished the sector).
        self.assertFalse([x for x in call("GET", "/cash/alerts", "MGT001")[1] if x["flight_id"] == leg["id"]])
        for who in (a, b):
            self.assertEqual(call("PUT", f"/flights/{leg['id']}/finish", who)[0], 200)
        sheet = call("GET", f"/cash/flights/{leg['id']}", "MGT001")[1]
        block = [x for x in sheet["blocks"] if x["crew_account"] == a][0]
        self.assertEqual(block["differences"], [{"currency": "NPR", "entered": float(short), "system": price * 3, "difference": -100.0}])
        alerts = [x for x in call("GET", "/cash/alerts", a)[1] if x["flight_id"] == leg["id"]]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(call("POST", f"/cash/alerts/{alerts[0]['id']}/fix", "MGT001", {"remarks": "system"})[0], 403)
        # Approval with an open difference needs remarks.
        status, sheet = call("PUT", f"/cash/flights/{leg['id']}/blocks", b, {"crew_account": b, "section": "collection",
                                                                            "denominations": {"5": int(price) // 5} if int(price) % 5 == 0 else {}})
        self.assertEqual(status, 200, sheet)
        self.assertEqual(call("POST", f"/cash/flights/{leg['id']}/approve", "INV001", {})[0], 400)
        # The administrator corrects A's block with remarks; the notice closes by itself.
        full = int(price * 3)
        fixed_notes = {"100": full // 100} if full % 100 == 0 else {"5": full // 5}
        self.assertEqual(call("PUT", f"/cash/flights/{leg['id']}/blocks", "ADMIN001", {
            "crew_account": a, "section": "collection", "denominations": fixed_notes})[0], 400)
        status, sheet = call("PUT", f"/cash/flights/{leg['id']}/blocks", "ADMIN001", {
            "crew_account": a, "section": "collection", "denominations": fixed_notes, "remarks": "Recounted the bag"})
        self.assertEqual(status, 200, sheet)
        self.assertFalse([x for x in call("GET", "/cash/alerts", "MGT001")[1] if x["flight_id"] == leg["id"]])
        self.assertTrue(any(e["action"] == "CORRECTED" for e in sheet["events"]))
        status, sheet = call("POST", f"/cash/flights/{leg['id']}/approve", "INV001", {"remarks": "OK"})
        self.assertEqual((status, sheet["status"]), (200, "APPROVED"), sheet)
        self.assertEqual(call("PUT", f"/cash/flights/{leg['id']}/blocks", "INV001", {
            "crew_account": a, "section": "float_close", "denominations": {}})[0], 400)
        # The printed sheet escapes what people typed.
        status, sheet = call("PUT", f"/cash/flights/{leg['id']}/blocks", "ADMIN001", {
            "crew_account": "GUEST1", "crew_name": "<script>alert(1)</script>", "section": "float_open",
            "denominations": {"1000": 1}, "remarks": "extra float"})
        self.assertEqual(status, 200, sheet)
        status, page = call("GET", f"/cash/flights/{leg['id']}/print", "MGT001")
        self.assertEqual(status, 200)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)
        # Bad notes are refused.
        self.assertEqual(call("PUT", f"/cash/flights/{leg['id']}/blocks", "ADMIN001", {
            "crew_account": a, "section": "float_close", "denominations": {"300": 1}, "remarks": "x"})[0], 400)
        # Paper-form FCY line: Deno x Qty gives the amount when no amount is written.
        status, sheet = call("PUT", f"/cash/flights/{leg['id']}/blocks", "ADMIN001", {
            "crew_account": "GUEST1", "section": "collection", "denominations": {},
            "fcy": [{"currency": "AED", "amount": 0, "deno": 50, "qty": 3, "rate": 40.8}], "remarks": "paper form"})
        self.assertEqual(status, 200, sheet)
        guest = [x for x in sheet["blocks"] if x["crew_account"] == "GUEST1"][0]
        self.assertEqual((guest["fcy_collect"][0]["amount"], guest["fcy_npr_total"]), (150, 6120))
        page = call("GET", f"/cash/flights/{leg['id']}/print", "MGT001")[1]
        for heading in ("FLOATING CASH BALANCE (NPR)", "Opening Balance", "Closing Balance", "CASH SALES COLLECTION",
                        "Currency: NPR", "Exchange Rate", "NPR Currency", "Net Cash", "Grand Total"):
            self.assertIn(heading, page)

    def test_09_finance_sees_the_sales_report_only(self):
        year = date.today().year
        self.assertEqual(call("GET", f"/reports/sales-report?year={year}", "FIN001")[0], 200)
        self.assertEqual(call("GET", f"/dashboard?year={year}", "FIN001")[0], 200)
        self.assertEqual(call("GET", f"/crew-ranking?year={year}", "FIN001")[0], 403)
        self.assertEqual(call("GET", "/sales?page=1", "FIN001")[0], 403)
        self.assertEqual(call("GET", "/logs?page=1", "FIN001")[0], 403)
        self.assertEqual(call("GET", f"/reports/sales-report?year={year}", CREW[0])[0], 403)


# ---------------------------------------------------------------------------
# Security sweep
# ---------------------------------------------------------------------------
PUBLIC = {"/", "/permissions", "/health", "/login", "/version", "/public/menu", "/public/menu/media/{name}", "/public/menu/koseli-menu.pdf",
          "/register", "/password-reset/request", "/password-reset/confirm", "/invite/accept", "/invite/{token}",
          "/account/verify-email", "/login/two-factor", "/login/2fa", "/forgot-password", "/reset-password",
          "/accept-invite", "/verify-email", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


class SecuritySweepTests(unittest.TestCase):
    def _routes(self):
        for route in main.app.routes:
            methods = getattr(route, "methods", None) or set()
            for method in methods - {"HEAD", "OPTIONS"}:
                yield method, route.path

    def _fill(self, path):
        out = path
        for name in ("flight_id", "report_id", "sale_id", "user_id", "member_id", "root_id", "conflict_id", "alert_id",
                     "rate_id", "product_id", "bundle_id", "order_id", "recon_id", "upload_id", "id"):
            out = out.replace("{" + name + "}", "1")
        import re
        return re.sub(r"\{[^}]+\}", "1", out)

    def test_no_route_answers_without_a_session(self):
        leaks = []
        for method, path in self._routes():
            if path in PUBLIC or path.startswith("/public/") or path.startswith("/menu"):
                continue
            status, _ = raw(method, self._fill(path), None, {} if method in ("POST", "PUT", "PATCH") else None)
            if status < 400:
                leaks.append((method, path, status))
        self.assertEqual(leaks, [], f"Routes answering without a session: {leaks}")

    def test_a_forged_token_is_refused(self):
        for path in ("/flights", "/sales?page=1", "/cash/alerts", "/users"):
            self.assertEqual(raw("GET", path, "forged-token-" + "a" * 40)[0], 401, path)

    def test_crew_cannot_reach_management_routes(self):
        crew = login(CREW[3])
        for method, path, body in (
                ("GET", "/users", None), ("GET", "/logs?page=1", None), ("GET", "/sales?page=1", None),
                ("GET", "/sales/flights", None), ("POST", "/exchange-rates/month", {"month": "2031-03", "rates": []}),
                ("PUT", "/report-forms/visibility", {"enabled": []}), ("POST", "/cash/alerts/1/fix", {"remarks": "xx"}),
                ("POST", "/cash/flights/1/approve", {}), ("GET", "/crew-ranking", None),
                ("POST", "/inventory/load", {"flight_id": 1, "lines": []}), ("GET", "/reports/sales-report", None),
                ("POST", "/sales-history/upload", None), ("PUT", "/sales/1", {"edit_remarks": "x"}),
                ("POST", "/users", {"employee_id": "X", "name": "X", "role": "admin", "password": "x"})):
            status, _ = raw(method, path, crew, body)
            self.assertIn(status, (401, 403, 422), (method, path, status))
            self.assertNotEqual(status, 200)

    def test_sql_in_parameters_is_data(self):
        token = login("MGT001")
        for path in ("/sales/flights?flight_number=%27%20OR%201%3D1--", "/cash/sheets?flight_number=%27%3BDROP%20TABLE%20users--",
                     "/flight-reports/trips?flight_number=%22%29%20OR%20%281%3D1"):
            status, out = raw("GET", path, token)
            self.assertEqual(status, 200, (path, out))
            self.assertEqual(out if isinstance(out, list) else out.get("items", []), [])
        self.assertEqual(raw("GET", "/users", login("ADMIN001"))[0], 200)   # the table is still there

    def test_crew_cannot_claim_another_crew_members_sale(self):
        # The seller is always the signed-in crew member, whatever the phone sends.
        crew = login(CREW[2])
        connection = get_connection()
        other = connection.execute("SELECT id FROM crew_members WHERE crew_code=?", (CREW[3],)).fetchone()["id"]
        connection.close()
        status, legs = call("POST", "/flights", "INV001", {"flight_number": "H9399", "flight_date": date.today().isoformat(),
                                                          "origin": "KTM", "destination": "BKK", "trip_type": "ONE_WAY"})
        if status != 200:
            self.skipTest(legs)
        leg = legs[0]
        call("POST", f"/trips/{leg['id']}/assign", "INV001", {"crew_accounts": CREW})
        product = max(call("GET", "/main-inventory", "INV001")[1], key=lambda p: p["quantity"])
        call("POST", "/inventory/load", "INV001", {"flight_id": leg["id"], "lines": [{"product_id": product["product_id"], "loaded_quantity": 3}]})
        call("PUT", f"/flights/{leg['id']}/pax-load", CREW[2], {"passengers": 100})
        status, out = raw("POST", "/sales/sync", crew, {"transactions": [{
            "order_ref": f"CLAIM-{leg['id']}-0001", "flight_id": leg["id"], "seat_number": "1A",
            "sold_by_crew_member_id": other, "lines": [{"product_id": product["product_id"], "quantity": 1}],
            "transaction_time": f"{date.today().isoformat()}T10:00:00"}]})
        self.assertEqual(status, 200, out)
        sale = call("GET", f"/sales?flight_id={leg['id']}&page=1", "MGT001")[1]["items"][0]
        self.assertEqual(sale["seller_code"], CREW[2])

    def test_security_headers(self):
        request = urllib.request.Request(f"{BASE}/health")
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")


if __name__ == "__main__":
    unittest.main()
