# Himalaya Airlines OBS Revenue Management System

![API version](https://img.shields.io/badge/API-v7.3-0B4F8A)
![Backend](https://img.shields.io/badge/backend-FastAPI%20%2B%20SQLite-009688)
![Mobile](https://img.shields.io/badge/mobile-Expo%2057%20%2F%20React%20Native-000020)
![Platforms](https://img.shields.io/badge/runs%20on-iPad%20%7C%20Android%20%7C%20web-lightgrey)
![License](https://img.shields.io/badge/license-proprietary-red)

This system handles onboard sales (OBS) for Himalaya Airlines. The crew sell from an iPad in the cabin, even with no connection. The ground teams use the same app to manage the trolley stock, warehouse, purchase orders, crew commission, exchange rates and Excel reports.

The repository has two parts:

- **`backend/`**: a FastAPI service on SQLite.
- **`mobile/`**: an Expo / React Native app that runs on iPad, Android and the web.

## Table of contents

- [Features](#features)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Usage](#usage)
- [Building the app](#building-the-app)
- [Getting help](#getting-help)
- [Maintainers and contributing](#maintainers-and-contributing)
- [License](#license)

## Features

- **Offline-first selling.** Flights, products, bundles and rates are cached on the device. When the iPad is offline, sales are queued and stock is held as it goes into the cart. The queue uploads by itself when the connection comes back.
- **Atomic transactions.** All the lines of one payment share an `order_ref` and are recorded together. Stock is checked and taken off in a single conditional `UPDATE`, so two devices can't oversell the same item.
- **Round trips handled as one trip.** Each leg has its own crew group and its own commission pool. The trip has one trolley load and one stock return.
- **Seat maps by aircraft type.** The A320 has 30 rows and the A319 has 24, and a sale to a seat that doesn't exist on the aircraft is rejected.
- **Calculated stock returns.** The system works out the leftover stock, and both iPads must confirm it before the Inventory Supervisor can approve it.
- **Warehouse and purchase orders.** Every stock change goes through a movement ledger. Purchase orders are approved and received as separate steps, and partial deliveries are supported.
- **Registers that match the paper sheets.** The OBS daily stock register and the PO register follow the airline's existing workbooks. Both can be imported and exported as Excel.
- **Commission.** 10% of a leg's normal sales goes to the group that worked it and is split equally among its members. A ranking screen shows the results.
- **Cabin report forms.** After each sector the crew file the security checklist, cabin safety report, voyage report and lost & found. Each form follows the airline's paper form ([`backend/report_forms.py`](backend/report_forms.py)).
- **Stock alerts (Communication Manager).** Each rule names its own products, threshold, recipients, channels (in-app and email), frequency and escalation. Products with no rule never send email. Delivery goes through a durable SMTP queue. See [`docs/COMMUNICATION.md`](docs/COMMUNICATION.md).
- **Role-based access, enforced by the API.** There are permission sets for crew, inventory, management, finance and admin users. Passwords are hashed with PBKDF2 and the API uses bearer-token sessions.
- **Multi-currency.** Exchange rates are stored as dated bands, with quotation units (for example, INR is quoted per 100).

## Project structure

```text
.
├── backend/                 FastAPI + SQLite API
│   ├── main.py              app entry point, CORS, /health
│   ├── database.py          schema, migrations, roles, seed data
│   ├── security.py          password hashing, sessions, permission guards
│   ├── crew_seed.py         cabin crew roster seed
│   ├── report_forms.py      cabin report form definitions
│   ├── requirements.txt
│   └── routers/             auth, admin, flights, catalog, warehouse, obs,
│                            register, sales, rates, analytics, reports,
│                            flight_reports
├── mobile/                  Expo / React Native client
│   ├── App.js               app shell, login, navigation
│   ├── app.json · eas.json  Expo and EAS build config
│   └── src/
│       ├── api.js           API client, offline cache and sync queue
│       ├── ui.js · theme.js shared components and styles
│       ├── charts.js        SVG charts
│       └── screens/         Crew, Sync, Inventory, Warehouse, Dashboard,
│                            Logs, Finance, Reports, Admin
└── docs/
    ├── HANDBOOK.md          operations handbook (workflow, registers,
    │                        troubleshooting)
    └── COMMUNICATION.md     stock alerts, SMTP, worker, permissions
```

## Getting started

### Prerequisites

- Python 3.10 or newer
- Node.js 20 LTS or newer, with npm
- To run the app on a phone: the [Expo Go](https://expo.dev/go) app, or a development build

### 1. Start the backend

```bash
cd backend
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
external api -- http://43.245.85.147:8005/
```

On first start, the server creates `backend/obs.db` and fills it with demo data. It then prints a banner showing the API version, the folder it's serving from and the database path. To check that it's running:

```bash
curl http://localhost:8000/health
# {"status":"online","version":"7.3","routes":...}
```

Open <http://localhost:8000/docs> for the interactive API reference.

> `--host 0.0.0.0` is required if you want a phone or tablet on the same network to reach the API.

### 2. Start the mobile app

```bash
cd mobile
npm install
cp .env.example .env        # Windows: copy .env.example .env
```

In `.env`, set the API address to your computer's LAN IP. A phone can't use `localhost`, because on the phone that means the phone itself:

```dotenv
EXPO_PUBLIC_API_BASE=http://192.168.1.50:8000
```

Then start the app:

```bash
npx expo start
```

Press `w` to open the app in a browser, or scan the QR code with Expo Go. Restart `expo start` after any change to `.env`.

### 3. Sign in

The seed data creates one demo account for each role. Every password is `1234`; change it from the profile menu.

| Employee ID | Account | Role |
|---|---|---|
| `CREW001` | OBS iPad 01 | Crew Supervisor |
| `CSUP001` | OBS iPad 02 | Crew Supervisor |
| `INV001` | Inventory Officer | Inventory |
| `ISUP001` | Inventory Supervisor | Inventory Supervisor |
| `MGT001` | Management User | Management |
| `FIN001` | Finance Officer | Finance |
| `ADMIN001` | Administrator | Admin |

Each role sees only the screens its permissions allow. The permission sets are defined in `ROLE_PERMISSIONS` in [`backend/database.py`](backend/database.py), and the API serves them at `GET /permissions`.

## Configuration

| Variable | Where | Purpose |
|---|---|---|
| `EXPO_PUBLIC_API_BASE` | `mobile/.env` | Base URL of the API. If it isn't set, the build falls back to a default address hard-coded in `src/api.js`. |
| `OBS_ALLOWED_ORIGINS` | backend environment | Comma-separated CORS origins. If it isn't set, any origin is allowed, which is only suitable for development. |
| `OBS_ADMIN_RECOVERY_CODE` | backend environment | Recovery code for the administrator account. Set this in any real deployment. |
| `OBS_SECRET_KEY` | backend environment | Fernet key that encrypts the stored SMTP password. Set this in production. |
| `OBS_EMBEDDED_WORKER` | backend environment | `1` (default) runs the alert/email worker inside the API; set `0` when running `python worker.py` separately. |

**Version check:** at login the app compares its `REQUIRED_API_VERSION` (in `mobile/src/api.js`) with the version the API reports (`API_VERSION` in `backend/main.py`). If they don't match, the app shows a warning banner. Change both values together.

## Usage

A typical sector, end to end:

1. **Inventory** creates the flight (one way or round trip) and loads the trolley. The loaded stock is taken out of main inventory.
2. **The crew** sign in on the OBS iPad, form the duty group, and enter the aircraft type and PAX load. Both lock after the first entry, and a correction needs written remarks.
3. **The crew** sell products and bundles. Offline sales are queued and upload by themselves later.
4. **The crew** press **Finish this flight**. Selling stops on that leg. On a round trip, the return leg then opens for the next group.
5. **Each iPad confirms the stock return**. The **Inventory Supervisor** then approves it, and the leftover stock goes back to the warehouse.
6. **Management and Finance** use the dashboards, the commission ranking and the Excel exports.

For the full workflow, register formats, commission rules and troubleshooting, see [`docs/HANDBOOK.md`](docs/HANDBOOK.md).

Reports can also be pulled straight from the API with a bearer token:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/login \
  -H 'Content-Type: application/json' \
  -d '{"employee_id":"MGT001","password":"1234"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/me
curl -H "Authorization: Bearer $TOKEN" -o sales.xlsx http://localhost:8000/reports/sales.xlsx
```

To see every route and its request schema, open `/docs` on a running server.

**Pagination.** Lists that grow over time (audit log, sales, purchase orders, stock movements, stock returns, filed reports, trips, alerts, deliveries, notifications) accept `?page=1&page_size=25` (max 200) and then return `{items, total, page, page_size, pages, has_prev, has_next}`. Without `page` they return their original response, so existing clients keep working.

## Building the app

Android and iOS builds use [EAS Build](https://docs.expo.dev/build/introduction/). The profiles are in [`mobile/eas.json`](mobile/eas.json):

```bash
cd mobile
npx eas-cli build --profile preview --platform android   # installable APK
npx eas-cli build --profile production --platform all
```

Before building, point `EXPO_PUBLIC_API_BASE` at the deployed API. You can set it in `.env` or in the EAS environment for that profile.

## Getting help

- **API reference:** `/docs` (Swagger UI) or `/redoc` on a running backend.
- **Operations handbook:** [`docs/HANDBOOK.md`](docs/HANDBOOK.md) covers the workflow, registers, exchange rates and common problems.
- **Project scope:** `Himalaya Airlines - OBS Revenue Management System - Final Project Scope.docx`, in the repository root.
- **Problems with the code:** open an issue in this repository, or contact H9 IT at <it@himalaya-airlines.com>.

**Two common problems:**

- **"API is an older build" banner, or 404 errors:** an old copy of the backend is still serving the port. Stop uvicorn and restart it from this repository's `backend/` folder.
- **`sqlite3.OperationalError: no such column`:** the database is older than the current schema. Delete `backend/obs.db` and restart the backend; it creates and seeds a fresh one.

### Known limits

- Sessions are held in memory, so restarting `uvicorn` signs everyone out.
- SQLite allows only one writer at a time. That works for a few devices, but a full fleet needs PostgreSQL.
- The API serves plain HTTP. Put it behind TLS before exposing it outside the internal network.

## Maintainers and contributing

The **Himalaya Airlines IT Department (H9 IT)** maintains this project. Contact <it@himalaya-airlines.com>.

Contributions from H9 IT staff and approved partners are welcome. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a pull request.

## License

This is proprietary software, © Himalaya Airlines. It is for internal use only, and you may not redistribute it without written permission from Himalaya Airlines.
