"""Himalaya Airlines OBS Revenue Management System - API.

Run with:  uvicorn main:app --reload --host 0.0.0.0 --port 8000
Interactive docs:  http://localhost:8000/docs
"""

import logging
import os
import sys
import traceback
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from database import DB_PATH, ROLE_PERMISSIONS, initialize_database
from routers import (
    admin, analytics, auth, cash, catalog, communication, flight_reports, flights, history, notifications,
    obs, qr_menu, rates, register, reports, sales, security_admin, settings_smtp, stock_alert_settings, warehouse,
)
import security
import comms_worker

# Bumped whenever endpoints are added. The app compares it on login and warns
# if it is talking to an older server, which otherwise shows up as bare 404s.
API_VERSION = "7.18"

# The interactive API documentation describes every route to whoever asks, so
# it is off unless OBS_ENABLE_DOCS=1 (development).
_docs = os.getenv("OBS_ENABLE_DOCS", "0") == "1"
app = FastAPI(
    title="Himalaya Airlines OBS Revenue Management System",
    version=API_VERSION,
    description="Onboard sales, warehouse inventory, crew commission and reporting.",
    docs_url="/docs" if _docs else None, redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)

# Expo dev servers move ports frequently, so the origin list is configurable.
# Set OBS_ALLOWED_ORIGINS="https://ops.example.com,https://admin.example.com" in
# production; the wildcard fallback is for local development only.
_origins = os.getenv("OBS_ALLOWED_ORIGINS", "").strip()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins.split(",") if o.strip()] or ["*"],
    allow_credentials=False,  # bearer tokens, not cookies
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

class ClientContext:
    """Remember the caller's address, browser and path for sessions and the audit log.

    Behind nginx, uvicorn's --proxy-headers already turns X-Forwarded-For into
    request.client, so this sees the real client address.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        client = scope.get("client") or (None, None)
        token = security.set_client(client[0], headers.get("user-agent"), headers.get("x-obs-client"),
                                    scope.get("path"))
        try:
            return await self.app(scope, receive, send)
        finally:
            security.reset_client(token)


app.add_middleware(ClientContext)


class SecurityHeaders:
    """Headers that stop the API being framed, sniffed or cached by a browser.
    API answers carry people's data, so they are never stored by a cache."""

    HEADERS = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        (b"cross-origin-opener-policy", b"same-origin"),
    ]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        public = scope.get("path", "").startswith("/public/")

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                for key, value in self.HEADERS:
                    if key not in present:
                        headers.append((key, value))
                if not public and b"cache-control" not in present:
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, wrapped)


app.add_middleware(SecurityHeaders)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("obs")


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """Return a readable JSON error instead of dropping the connection.

    Without this, an unexpected exception closes the response before the CORS
    middleware adds its headers, so a browser reports the whole thing as
    "Failed to fetch" and the real cause never reaches anyone. Logging the
    traceback here means the terminal always shows what actually happened.
    """
    logger.error("Unhandled error on %s %s\n%s", request.method, request.url.path,
                 "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    return JSONResponse(
        status_code=500,
        # The details stay in the server log; the caller gets a reference only.
        content={
            "detail": f"Unexpected server error ({type(exc).__name__}). The details are in the server log.",
            "path": request.url.path,
        },
        headers={"Access-Control-Allow-Origin": "*"},
    )


initialize_database()

for module in (auth, admin, flights, catalog, warehouse, obs, register, sales, rates,
               analytics, reports, flight_reports, settings_smtp, communication, stock_alert_settings,
               notifications, security_admin, qr_menu, history, cash):
    app.include_router(module.router)


@app.on_event("startup")
def _start_worker():
    # Stock alerts and email delivery. Set OBS_EMBEDDED_WORKER=0 when running
    # `python worker.py` as its own process instead.
    comms_worker.start_embedded()


@app.on_event("shutdown")
def _stop_worker():
    comms_worker.stop_embedded()


def _announce():
    """Print which build and which folder is actually serving.

    If this banner does not appear when you start uvicorn, you are running an
    older copy of the backend from a different directory.
    """
    banner = [
        "",
        "=" * 64,
        f"  Himalaya OBS API  v{API_VERSION}",
        f"  Serving from : {Path(__file__).resolve().parent}",
        f"  Database     : {DB_PATH}",
        f"  Routes       : {len(app.routes)}",
        f"  Python       : {sys.version.split()[0]}",
        f"  Comms worker : {'embedded' if os.getenv('OBS_EMBEDDED_WORKER', '1') != '0' else 'external (python worker.py)'}",
        f"  Verify with  : http://localhost:8000/health",
        "=" * 64,
        "",
    ]
    print("\n".join(banner), flush=True)


_announce()


@app.get("/", tags=["meta"])
def root():
    return {
        "system": "Himalaya Airlines OBS Revenue Management System",
        "version": API_VERSION,
        "status": "running",
        "docs": "/docs" if _docs else None,
    }


@app.get("/health", tags=["meta"])
def health():
    return {
        "status": "online",
        "version": API_VERSION,
        "routes": len(app.routes),
    }


@app.get("/permissions", tags=["meta"])
def permissions():
    """The role/permission matrix, so the app can build its navigation from it."""
    return {role: sorted(perms) for role, perms in ROLE_PERMISSIONS.items()}
