"""Passenger QR menu (7.8). Synthetic data only, on a throw-away database."""

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
_TMP = tempfile.mkdtemp(prefix="obs-qrmenu-test-")
os.environ["OBS_DB_PATH"] = os.path.join(_TMP, "qr.db")
os.environ["OBS_EMBEDDED_WORKER"] = "0"

import uvicorn  # noqa: E402

import main  # noqa: E402
from database import get_connection  # noqa: E402
from tests.fake_smtp import free_port  # noqa: E402

PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
TOKENS = {}
_server = None
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


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


def token(who):
    if who not in TOKENS:
        status, out, _ = raw("POST", "/login", body={"employee_id": who, "password": "1234"})
        assert status == 200, out
        TOKENS[who] = json.loads(out)["token"]
    return TOKENS[who]


def raw(method, path, who=None, body=None, data=None, ctype="application/json"):
    headers = {"Content-Type": ctype}
    if who:
        headers["Authorization"] = f"Bearer {token(who)}"
    payload = data if data is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(f"{BASE}{path}", data=payload, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def call(method, path, who=None, body=None):
    status, out, _ = raw(method, path, who, body)
    return status, json.loads(out or b"null")


def upload(who, content, filename="dish.png", mime="image/png"):
    boundary = uuid.uuid4().hex
    data = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    status, out, _ = raw("POST", "/qr-menu/images", who, data=data,
                         ctype=f"multipart/form-data; boundary={boundary}")
    return status, json.loads(out or b"null")


class QrMenuTests(unittest.TestCase):
    def test_only_admin_can_manage(self):
        for who in ("INV001", "CREW001"):
            self.assertEqual(call("GET", "/qr-menu", who)[0], 403, who)
            self.assertEqual(call("POST", "/qr-menu/sections", who, {"name_en": "X"})[0], 403, who)
        self.assertEqual(call("GET", "/qr-menu")[0], 401)
        self.assertEqual(call("GET", "/qr-menu", "ADMIN001")[0], 200)

    def test_full_crud_and_public_page(self):
        status, section = call("POST", "/qr-menu/sections", "ADMIN001",
                               {"name_en": "Test Snacks", "name_zh": "测试小吃", "sort_order": 1})
        self.assertEqual(status, 200, section)
        status, image = upload("ADMIN001", PNG)
        self.assertEqual(status, 200, image)
        status, item = call("POST", "/qr-menu/items", "ADMIN001", {
            "section_id": section["id"], "name_en": "Sample Momo Box", "name_zh": "样品馍馍",
            "desc_en": "Synthetic </script><b>item", "price": 1200, "photo": image["name"]})
        self.assertEqual(status, 200, item)

        status, html, headers = raw("GET", "/public/menu")
        page = html.decode()
        self.assertEqual(status, 200)
        self.assertIn("Sample Momo Box", page)
        self.assertIn(image["name"], page)
        self.assertIn('M="/menu/media/"', page)
        self.assertNotIn("</script><b>", page)  # text cannot break out of the page script
        self.assertIn("popstate", page)  # back button is locked to the menu

        status, media, _ = raw("GET", f"/public/menu/media/{image['name']}")
        self.assertEqual((status, media), (200, PNG))
        self.assertEqual(raw("GET", "/public/menu/media/..%2Fqr.db")[0], 404)

        status, item = call("PUT", f"/qr-menu/items/{item['id']}", "ADMIN001", {
            "section_id": section["id"], "name_en": "Sample Momo Box", "price": 1350, "active": False})
        self.assertEqual((status, item["price"], item["active"]), (200, 1350, 0))
        self.assertNotIn("Sample Momo Box", raw("GET", "/public/menu")[1].decode())  # hidden

        status, out = call("DELETE", f"/qr-menu/sections/{section['id']}", "ADMIN001")
        self.assertEqual(status, 400, out)  # still has an item
        self.assertEqual(call("DELETE", f"/qr-menu/items/{item['id']}", "ADMIN001")[0], 200)
        self.assertEqual(call("DELETE", f"/qr-menu/sections/{section['id']}", "ADMIN001")[0], 200)

        connection = get_connection()
        rows = connection.execute("SELECT COUNT(*) FROM audit_logs WHERE module='QR MENU'").fetchone()[0]
        connection.close()
        self.assertGreaterEqual(rows, 5)

    def test_rejects_bad_uploads(self):
        self.assertEqual(upload("ADMIN001", b"not a picture")[0], 400)
        self.assertEqual(upload("ADMIN001", b"GIF89a", "a.gif", "image/gif")[0], 400)
        self.assertEqual(upload("INV001", PNG)[0], 403)
        self.assertEqual(call("POST", "/qr-menu/items", "ADMIN001", {
            "section_id": 1, "name_en": "x", "photo": "missing.png"})[0], 400)

    def test_settings_qr_and_offline_package(self):
        status, settings = call("PUT", "/qr-menu/settings", "ADMIN001",
                                {"title_en": "Test Onboard Menu", "currency": "NPR"})
        self.assertEqual((status, settings["title_en"]), (200, "Test Onboard Menu"))
        status, svg, _ = raw("GET", "/qr-menu/qr.svg?url=http%3A%2F%2F10.0.0.5%2Fmenu", "ADMIN001")
        self.assertEqual(status, 200)
        self.assertTrue(svg.startswith(b"<svg") or b"<svg" in svg[:200])
        self.assertEqual(raw("GET", "/qr-menu/qr.svg?url=javascript%3Aalert(1)", "ADMIN001")[0], 400)

        _, section = call("POST", "/qr-menu/sections", "ADMIN001", {"name_en": "Offline Drinks"})
        _, image = upload("ADMIN001", PNG)
        call("POST", "/qr-menu/items", "ADMIN001",
             {"section_id": section["id"], "name_en": "Sample Tea", "price": 300, "photo": image["name"]})
        status, body, headers = raw("GET", "/qr-menu/offline.zip", "ADMIN001")
        self.assertEqual(status, 200)
        names = zipfile.ZipFile(io.BytesIO(body)).namelist()
        for expected in ("koseli-menu/public/index.html", "koseli-menu/server.py", "koseli-menu/qrgen.py",
                         "koseli-menu/start-server.bat", "koseli-menu/public/qr.html",
                         f"koseli-menu/public/assets/media/{image['name']}"):
            self.assertIn(expected, names)
        index = zipfile.ZipFile(io.BytesIO(body)).read("koseli-menu/public/index.html").decode()
        self.assertIn('M="assets/media/"', index)
        self.assertIn("Test Onboard Menu", index)


if __name__ == "__main__":
    unittest.main()
