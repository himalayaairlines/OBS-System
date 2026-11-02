#!/usr/bin/env python3
"""
Himalaya Airlines - Koseli onboard menu server
================================================

Serves the menu in ./public to every phone/tablet/laptop on the same Wi-Fi.

* Detects the laptop's current LAN IP automatically (no hard-coded address).
* Generates the QR code live for that IP -> the QR never goes stale.
* Watches for IP changes (e.g. new DHCP lease) and refreshes the QR on the fly.
* Falls back to the next free port if 8080 is busy.
* Standard library only - needs nothing but Python 3.8+.

Usage:
    python server.py                 # port 8080, opens the QR page
    python server.py --port 9000
    python server.py --no-browser
"""

from __future__ import annotations

import argparse
import datetime as _dt
import errno
import json
import mimetypes
import os
import socket
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

if sys.version_info < (3, 8):
    sys.exit("Python 3.8 or newer is required.")

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
sys.path.insert(0, str(ROOT))
import qrgen  # noqa: E402

APP_NAME = "Himalaya Airlines - Koseli Menu"
DEFAULT_PORT = 8080
PORT_ATTEMPTS = 10
DEVICE_WINDOW_SEC = 15 * 60

for ext, mime in {".webp": "image/webp", ".svg": "image/svg+xml",
                  ".webmanifest": "application/manifest+json", ".js": "text/javascript"}.items():
    mimetypes.add_type(mime, ext)

# Make the console handle Unicode and ANSI colours on Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass
if os.name == "nt":
    os.system("")  # enables VT100 escape processing in conhost

_COLOR = sys.stdout.isatty()
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _COLOR else s
BOLD, DIM, GREEN, YELLOW, CYAN, RED = (lambda s: _c("1", s), lambda s: _c("2", s),
    lambda s: _c("32", s), lambda s: _c("33", s), lambda s: _c("36", s), lambda s: _c("31", s))


# --------------------------------------------------------------------------- network
def _is_usable(ip: str) -> bool:
    return not (ip.startswith("127.") or ip.startswith("169.254.") or ip == "0.0.0.0")


def _is_private(ip: str) -> bool:
    a, b = (int(x) for x in ip.split(".")[:2])
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def primary_ip() -> str | None:
    """IP of the interface that owns the default route. No packets are sent."""
    for target in ("192.168.0.1", "10.255.255.255", "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((target, 80))
                ip = s.getsockname()[0]
                if _is_usable(ip):
                    return ip
        except OSError:
            continue
    return None


def all_ips() -> list[str]:
    found: list[str] = []
    p = primary_ip()
    if p:
        found.append(p)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_usable(ip) and ip not in found:
                found.append(ip)
    except OSError:
        pass
    # Private LAN addresses first (after the primary one), virtual adapters last.
    head, rest = found[:1], found[1:]
    rest.sort(key=lambda ip: (not _is_private(ip), ip))
    return head + rest


# --------------------------------------------------------------------------- state
class State:
    def __init__(self, port: int):
        self.port = port
        self.lock = threading.Lock()
        self.ips = all_ips()
        self.devices: dict[str, float] = {}
        self.started = time.time()

    @property
    def ip(self) -> str:
        return self.ips[0] if self.ips else "127.0.0.1"

    def url_for(self, ip: str) -> str:
        return f"http://{ip}:{self.port}/"

    @property
    def url(self) -> str:
        return self.url_for(self.ip)

    def seen(self, client_ip: str) -> None:
        if client_ip.startswith("127.") or client_ip == "::1":
            return
        with self.lock:
            self.devices[client_ip] = time.time()

    def active_devices(self) -> int:
        cutoff = time.time() - DEVICE_WINDOW_SEC
        with self.lock:
            self.devices = {k: v for k, v in self.devices.items() if v >= cutoff}
            return len(self.devices)

    def info(self) -> dict:
        return {
            "app": APP_NAME,
            "url": self.url,
            "ip": self.ip,
            "port": self.port,
            "urls": [self.url_for(i) for i in self.ips],
            "hostname": socket.gethostname(),
            "hostnameUrl": f"http://{socket.gethostname().lower()}.local:{self.port}/",
            "devices": self.active_devices(),
            "uptimeSec": int(time.time() - self.started),
        }


STATE: State  # set in main()


# --------------------------------------------------------------------------- HTTP
class MenuHandler(SimpleHTTPRequestHandler):
    server_version = "KoseliMenu/2.0"
    sys_version = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    # Routing -----------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/info":
            return self._json(STATE.info())
        if path == "/qr.svg":
            return self._qr_svg()
        if path == "/healthz":
            return self._send(b"ok", "text/plain")
        if path in ("/", "/index.html"):
            STATE.seen(self.client_address[0])
        return super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802
        return super().do_HEAD()

    def _qr_svg(self) -> None:
        q = parse_qs(urlsplit(self.path).query)
        target = STATE.url
        ip = (q.get("ip") or [""])[0]
        if ip and ip in STATE.ips:  # only our own addresses -> not an open QR service
            target = STATE.url_for(ip)
        svg = qrgen.to_svg(qrgen.encode(target, "Q"), border=4).encode()
        self._send(svg, "image/svg+xml", cache="no-store")

    # Helpers -----------------------------------------------------------------
    def _send(self, body: bytes, ctype: str, status: int = 200, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/json")) else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: dict) -> None:
        self._send(json.dumps(data).encode(), "application/json")

    def list_directory(self, path):  # never expose folder listings
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        return None

    def send_header(self, keyword: str, value: str) -> None:
        if keyword.lower() == "cache-control":
            self._cache_set = True
        super().send_header(keyword, value)

    def end_headers(self) -> None:
        path = urlsplit(self.path).path
        if not getattr(self, "_cache_set", False):
            if path.startswith("/assets/"):
                self.send_header("Cache-Control", "public, max-age=86400")
            else:
                self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self._cache_set = False
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # Concise access log: only page views and errors, not every image.
        try:
            status = int(args[1]) if len(args) > 1 else 0
        except (ValueError, TypeError):
            status = 0
        path = urlsplit(self.path).path if hasattr(self, "path") else ""
        if status >= 400 or path in ("/", "/index.html"):
            ts = _dt.datetime.now().strftime("%H:%M:%S")
            who = self.client_address[0]
            label = GREEN("menu opened") if status < 400 else RED(f"HTTP {status}")
            print(f"  {DIM(ts)}  {who:<15}  {label}  {DIM(path)}", flush=True)

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass  # phones drop connections all the time; not an error


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"  # on Windows this would allow port hijacking


# --------------------------------------------------------------------------- console UI
def print_banner(state: State, note: str = "") -> None:
    line = "=" * 62
    print()
    print(CYAN(line))
    print(BOLD(f"  HIMALAYA AIRLINES  |  Koseli onboard menu is LIVE"))
    print(CYAN(line))
    if note:
        print(YELLOW(f"  {note}"))
    print(f"  Phones on the same Wi-Fi:  {BOLD(GREEN(state.url))}")
    for extra in state.ips[1:]:
        print(f"  Other adapter:             {DIM(state.url_for(extra))}")
    print(f"  On this laptop:            http://localhost:{state.port}/")
    print(f"  Printable QR page:         http://localhost:{state.port}/qr.html")
    print()
    print(qrgen.to_terminal(qrgen.encode(state.url, "Q")))
    print()
    print(DIM("  Scan the QR above, or open the printable QR page."))
    print(DIM("  Phones can't connect? Run fix-network-access.bat once (as admin)."))
    print(DIM("  Press Ctrl+C to stop."))
    print()


def watch_ip(state: State, stop: threading.Event) -> None:
    while not stop.wait(8):
        ips = all_ips()
        if ips and ips[0] != state.ip:
            old = state.ip
            with state.lock:
                state.ips = ips
            print_banner(state, note=f"Network changed: {old} -> {state.ip}. QR updated automatically.")
        elif ips != state.ips:
            with state.lock:
                state.ips = ips or state.ips


def bind(port: int) -> tuple[Server, int]:
    last_err: OSError | None = None
    for p in range(port, port + PORT_ATTEMPTS):
        try:
            return Server(("0.0.0.0", p), MenuHandler), p
        except OSError as e:
            last_err = e
            in_use = e.errno in (errno.EADDRINUSE, 10048) or getattr(e, "winerror", None) in (10048, 10013)
            if not in_use:
                raise
            print(YELLOW(f"  Port {p} is busy, trying {p + 1}..."))
    raise SystemExit(f"No free port between {port} and {port + PORT_ATTEMPTS - 1}: {last_err}")


def main() -> None:
    global STATE
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--port", type=int, default=int(os.environ.get("MENU_PORT", DEFAULT_PORT)))
    ap.add_argument("--no-browser", action="store_true", help="don't open the QR page on start")
    args = ap.parse_args()

    if not (PUBLIC / "index.html").is_file():
        sys.exit(f"Can't find {PUBLIC / 'index.html'} - keep server.py next to the 'public' folder.")

    httpd, port = bind(args.port)
    STATE = State(port)
    if not STATE.ips:
        print(RED("  No network connection found. Connect this laptop to Wi-Fi, the QR will update automatically."))

    note = f"Port {args.port} was busy, using {port} instead." if port != args.port else ""
    print_banner(STATE, note)

    stop = threading.Event()
    threading.Thread(target=watch_ip, args=(STATE, stop), daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{port}/qr.html")).start()

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n  Stopping menu server... bye!")
    finally:
        stop.set()
        httpd.server_close()


if __name__ == "__main__":
    main()
