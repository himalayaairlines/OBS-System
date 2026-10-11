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
    admin, analytics, auth, catalog, communication, flight_reports, flights, notifications,
    obs, rates, register, reports, sales, settings_smtp, warehouse,
)
import comms_worker

# Bumped whenever endpoints are added. The app compares it on login and warns
# if it is talking to an older server, which otherwise shows up as bare 404s.
API_VERSION = "7.3"

app = FastAPI(
    title="Himalaya Airlines OBS Revenue Management System",
    version=API_VERSION,
    description="Onboard sales, warehouse inventory, crew commission and reporting.",
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
        content={
            "detail": f"{type(exc).__name__}: {exc}",
            "path": request.url.path,
            "hint": "The full traceback is in the uvicorn terminal.",
        },
        headers={"Access-Control-Allow-Origin": "*"},
    )


initialize_database()

for module in (auth, admin, flights, catalog, warehouse, obs, register, sales, rates,
               analytics, reports, flight_reports, settings_smtp, communication, notifications):
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
        "docs": "/docs",
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
