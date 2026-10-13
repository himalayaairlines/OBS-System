> **Note:** This handbook was written for v5 and is kept for its workflow, register and troubleshooting detail. Some parts are out of date as of API v7.2. For example, duty groups are now formed by the crew on the OBS iPad, and `CREW001` is an OBS iPad account with Crew Supervisor rights. When the handbook and the code disagree, the code is correct. Start with the [README](../README.md).

# Himalaya Airlines — OBS Revenue Management System (v5)

Onboard sales, warehouse inventory, crew groups and commission, and reporting.

```
himalaya-obs/
├── backend/          FastAPI + SQLite
│   ├── main.py       app entry point
│   ├── database.py   schema, migrations, seed data
│   ├── security.py   password hashing, session tokens, permission guards
│   ├── crew_seed.py  cabin crew roster loaded from the master name list
│   └── routers/      auth, admin, flights, catalog, warehouse, obs, register,
│                     sales, rates, analytics, reports
└── mobile/           Expo / React Native (runs on iPad, Android and web)
    ├── App.js        shell, login, navigation
    └── src/          api client, theme, components, charts, screens
```

## 1. Run the backend

```bash
cd backend
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000/docs> for the interactive API.

The database (`backend/obs.db`) is created and seeded on first start. **If you
are upgrading from v4, delete the old `obs.db` first** — the migration path is
best-effort and a clean file is far less trouble.

`--host 0.0.0.0` matters: without it the server only listens on localhost and a
phone on the same Wi-Fi cannot reach it.

## 2. Run the mobile app

```bash
cd mobile
npm install
npx expo start
```

Press `w` for the browser, or scan the QR code with Expo Go.

Point the app at your computer, not at localhost — a phone's localhost is the
phone itself. Copy `.env.example` to `.env` and set your LAN IP:

```
EXPO_PUBLIC_API_BASE=http://192.168.1.50:8000
```

Find the IP with `ipconfig` (Windows) or `ifconfig` / `ip addr` (macOS, Linux).
Restart `expo start` after changing it.

## 3. Demo accounts

All passwords are `1234`. Because that breaks the password policy, each account must choose a new password at its first sign-in (10+ characters, 3 of lower/upper/digit/symbol, not containing the name).

| Employee ID | Role | Sees |
|---|---|---|
| `CREW001` | Crew | Sales, Sync |
| `CSUP001` | Crew Supervisor | Sales, Sync, plus PAX/aircraft corrections |
| `INV001` | Inventory | Flights, trolley loading, crew groups, warehouse |
| `ISUP001` | Inventory Supervisor | The above plus bundles and all approvals |
| `MGT001` | Management | Oversight, corrections, dashboard, logs (no selling screen) |
| `FIN001` | Finance | Exchange rates and the dashboard only |
| `ADMIN001` | Administrator | Dashboard (user analytics), Communication Manager, SMTP Settings, Users & Access (login accounts, crew roster, sign-in settings), Audit Log |

## 4. The workflow end to end

1. **Admin** maintains the crew name roster. It ships preloaded with all 86
   cabin crew from the master name list — 25 Pursers, 21 Senior Cabin Crew and
   40 Cabin Crew — each with their staff code. Resigned crew are not included.
2. **Inventory** creates a flight: number, date, origin, destination, and one-way
   or round trip. A round trip is stored as two linked legs with the route
   reversed, so a different group can work the way back.
3. **Inventory** builds groups of up to four crew from the roster, then assigns
   the trip: the group going out, the group coming back, and the two iPad
   accounts that stay on board for both legs.
4. **Inventory** loads the trolley. Quantities are drawn out of main inventory
   automatically; reducing a load puts them back.
5. **Crew** enter the aircraft registration and PAX load. Both lock after the
   first entry. A Crew Supervisor can correct them on the same day, but only
   with mandatory remarks.
6. **Crew** sell products and bundles. Without a connection, sales queue on the
   device and upload by themselves once it reconnects — there is nothing to press.
7. **Crew** press **Finish this flight** when the leg is over. Selling stops on
   that leg. On a round trip the return leg then opens for the next group, which
   is what keeps each group's commission tied to its own sales even though both
   legs use the same iPad login.
8. **Crew** submit the leftover stock count after the flight — **from both
   iPads**. Each device counts its own trolley; the server adds them together.
9. **Inventory Supervisor** approves it. Approved leftovers return to main
   inventory automatically and the flight closes.
10. **Inventory** raises order requests when stock runs low; the **Inventory
    Supervisor** approves them and receives the goods, which posts to stock.
11. **Finance** sets exchange rates as dated bands, including future ones, and
    reads the dashboard. They see no sales, commission or sync.
12. **Management** watches the dashboard, corrects mis-keyed sales with remarks,
    reviews the commission ranking, and exports any view to Excel.

### Who can do what

Inventory build crew groups, load trolleys and raise orders. The Inventory
Supervisor additionally **deletes** groups, **creates, edits and deletes**
bundles, **adds stock** directly to the warehouse, **approves and receives**
orders, and **edits** the daily register. That split is enforced in the API, not
just hidden in the app.

## 5. Commission

Each flight pays 10% of its normal sales into a pool belonging to the crew group
that worked the leg. The pool is split equally among the group's members, so an
individual's earnings are the sum of their shares across flights. The ranking
screen filters by date and destination and draws a bar chart.

Group membership is read as it stands today. If you reshuffle a group, past
commission is recalculated against the new membership — tell me if you would
rather freeze membership at the time of the flight, which needs a snapshot on
the assignment record.

## 6. The two register sheets

**OBS stock register** (Inventory → Daily register) reproduces your monthly
sheet as a scrolling grid: the product column stays fixed while the days scroll
sideways, one row per product, an opening balance, then Received / Sales /
Closing for every day, where `closing = opening + received − sales` and each
closing carries into the next day. Tap any figure to correct it. Set the month's
opening balances once and the rest follows.

Received is dated to the **flight**, not to the moment the clerk saved the load,
so a trolley loaded on the 18th for a flight on the 16th appears against the
16th and the running balance never dips below zero. Received and Sales are derived from real trolley
loads and real sales, so the sheet cannot drift away from the rest of the
system. An Inventory Supervisor can override a day's figures, but only with
mandatory remarks; the computed value is kept alongside the override and both
appear in the export, on a "Manual Edits" sheet. Download it from the same tab.

**PO register** (Warehouse → PO register) reproduces your PO workbook column for
column in the same scrolling table, with the PO number frozen on the left: PO code, PO Number, FY, PO No., Purchase Items, QTY, order placement and
delivery dates, Order Received QTY / Date / Location, Inflight-or-OBS and
Remarks. Two differences from the spreadsheet, both deliberate:

- **Quantities are in pieces, not "03 cases".** That is what lets a receipt post
  straight into warehouse stock. Set the case size in the product record and
  order in pieces.
- **Receiving is a separate step from approving.** Stock moves when the goods
  actually arrive, not when the order is signed off, so a partly delivered order
  shows as PARTIAL with the outstanding balance visible. Receiving more than was
  ordered is rejected.

Only an Inventory Supervisor can edit a PO or record a receipt, and an edit
needs remarks. The FY label (`8384`) is derived from the placement date on the
Nepali fiscal year, and the PO number increments per code per year, so
`ANC/001`, `ANC/002` and so on.

## 7. What changed from v4

**Fixed**

- Sales were split into one row per product with no link between them, so a
  single passenger payment could land half on the server and half in the queue.
  A transaction now has one `order_ref` covering all its lines and is recorded
  atomically.
- The offline queue stopped at the first failure and retried it forever, so one
  permanently rejectable sale blocked every sale behind it. `POST /sales/sync`
  now answers per transaction and flags permanent rejections, which the app
  moves to a review list with Retry and Discard.
- Nothing but pending sales was cached, so the app was unusable offline from a
  cold start. Flights, products, bundles and rates are cached and the crew
  screen falls back to them with a warning banner.
- Local stock never decremented offline, so crew could oversell and every
  oversold row was rejected later. The cart now reserves stock as it is added.
- The stock check and the decrement were separate statements, so two devices
  could both pass the check. They are now one conditional `UPDATE`.
- Cancellations consumed stock. They no longer do; complementary items still do.
- The dashboard dropped zero-sale products when a filter was applied, and
  double-counted passengers on routes with several sale lines.
- Excel would not download on web. An `<a href>` cannot carry an auth header, so
  the app now fetches to a blob; the endpoint also accepts `?token=`.
- Passwords were plaintext and `X-Employee-Id` was trusted as-is, so editing the
  stored session on a device granted any role. Passwords are hashed with PBKDF2
  and `/login` issues a bearer token.
- `allow_origins=["*"]` with `allow_credentials=True`. Origins are now set with
  `OBS_ALLOWED_ORIGINS`, and auth is bearer tokens rather than cookies.
- The seat pattern was hardcoded to rows 1–24, letters A–F. It now accepts rows
  1–99 and letters A–K.

## Trips, not legs

A round trip is one **trip** everywhere it is shown: the route reads
`KTM-DXB-KTM`, not two separate flights. Inventory assigns crew to the trip in
one action, choosing the **going group** and the **return group**. Both legs use
the same two iPad accounts, because the same devices stay on board; only the
crew group changes at the turnaround. A round trip cannot be assigned without a
group for the return leg.

## One trip, one trolley, one stock return

A round trip is loaded **once**, before the outbound leg. Both legs sell from
the same rows, so the return leg opens with whatever the outbound left, and the
leftover count at the end covers the whole trip rather than each leg separately.
Loading, selling and the stock return all resolve to the outbound leg, which
owns the trip's stock.

### Finishing a leg

**Finish this flight** asks for confirmation first. Confirming is final: the leg
disappears from the crew's flight picker and no further sale can be recorded
against it. Its commission is locked to the group that worked it.

On a round trip the app then moves to the return leg, where the returning group
enters its own aircraft type and PAX load. When the last leg is finished the
crew are told to go to the Sync page.

### The stock return

Both iPads are offline in the air and reach Wi-Fi at different moments. That is
the whole difficulty, and it is why the return works the way it does:

- **The figures are calculated, not counted.** Loaded for the trip, minus
  everything sold on either leg, minus wastage. There is no flight to pick and
  nothing to type.
- **Each iPad confirms.** The point of requiring both is not to add two counts
  together — it is to be sure every sale has arrived before the remainder is
  trusted. Until both have confirmed, the trip sits at **WAITING FOR 2ND IPAD**
  and the API refuses to approve it.
- **The confirmation queues offline** and uploads *after* the sales queue, never
  before, so the remainder is never computed against partial sales.
- **Only the Inventory Supervisor can change a figure**, and only with a written
  reason. The system's own number is kept beside the correction rather than
  overwritten, and both appear in the export.
- **Approved leftovers post to the warehouse** through the movement ledger, so
  every returned unit is traceable with the trip reference against it.

## Commission on a round trip

Each leg's 10% pool is calculated on that leg's own sales and divided among that
group's four members only. The two groups are never pooled, even though both
legs are worked from the same two iPad logins.

**Added in the latest pass**

- Queued sales upload themselves: on reconnect, and on a 12-second timer while
  online. The manual sync button is gone; the page reports state instead.
- A round trip is loaded once and reconciled once, across both legs.
- The leftover figures are calculated by the system; the crew confirm rather
  than count, and there is no flight to select.
- Only the Inventory Supervisor can correct a figure, with mandatory remarks.
- Finishing a leg asks for confirmation and is final; the leg cannot be selected
  again.
- Returned stock is recorded in the warehouse movement ledger with the trip
  reference.
- The profile dialog is a fixed box that does not scroll.
- **Finish this flight** closes selling on a leg. On a round trip the outbound
  group hands over to the return group, and each is paid on its own sales even
  though both use the same iPad login. A finished leg rejects further sales.
- Deleting a crew group and deleting a bundle are Inventory Supervisor actions,
  as is adding stock directly to the warehouse. A group with recorded sales is
  deactivated rather than deleted, so commission history survives.
- Every management log view exports to Excel: crew ranking (with a by-group
  sheet), top products, the audit log, and sales detail (with a separate
  corrections sheet). Each export carries the filters shown on screen.
- The profile dialog is a modest centred box rather than a near-full-height
  sheet.

- The daily register imports your existing workbook: pick the file and it fills
  opening balances and every day's received and sales figures. Products are
  matched by name and anything unmatched is reported rather than guessed at.
  Imported figures are stored as manual entries, so live totals underneath are
  never lost.
- Purchase orders filter by delivery date, and receiving now records the actual
  delivery date alongside the promised one, so the register shows both.
- Order requests take either an existing product or a new one. A new product is
  only created in the catalogue once the Inventory Supervisor approves the
  order, so unapproved requests never pollute the product list. Receiving is
  blocked until approval.
- Products are no longer created from the Inventory screen; that tab is a
  read-only view of the warehouse catalogue.
- Crew see products as tiles with a search box, and anything sold out is hidden
  rather than shown greyed out.
- A crew stock return can be submitted once by the crew. Changing one that has
  already been submitted is a Crew Supervisor action and needs written remarks,
  which are recorded in the audit log. Approved returns are closed.
- Management no longer have a selling screen; Finance see only rates and the
  dashboard.
- The dashboard adds target analysis: actual and target RPP, the per-passenger
  difference, revenue at target, total shortfall or surplus, and how many legs
  cleared the target.
- Exchange rates carry a quotation unit, so INR per 100 and JPY per 10 convert
  correctly.

- OBS daily stock register and PO register, both matching the source workbooks
  and both exportable to Excel.
- Products can be created and edited in the app, and stock can be added to the
  warehouse directly or through a purchase order receipt.
- Aircraft is now a choice of A320 or A319 rather than free text, and the seat
  map is drawn from it: 30 rows for the A320, 24 for the A319, three seats either
  side of the aisle. A seat that does not exist on the aircraft is rejected, and
  the type cannot be changed once sales exist against its seat numbers.
- Crew and supervisor iPad accounts are picked from existing logins instead of
  being typed in.
- Search boxes on the trolley load list, the crew group list, the product
  catalogue, the warehouse stock list, the daily register and the crew roster;
  any picker with more than a dozen entries filters as you type.
- The cabin crew roster is preloaded from the master name list with staff codes
  and positions. Position shows on the group builder, the ranking table, the
  sync page and the Commission sheet of the export, and the roster can be
  filtered by it. The group builder warns when a group has no Purser.
- The inventory Sync page shows crew stock returns instead of commission, which
  is not their concern.
- Revenue by product on the dashboard toggles between the chart and a ranked
  list.

**Added earlier**

Finance role; crew roster and groups; flight assignment per leg; round trips;
bundles with revenue allocated across components so product reporting stays
accurate; main inventory with a full movement ledger; order requests and
approvals; post-flight reconciliation; management sale corrections with remarks
that flow into the Excel; commission ranking with charts; an inventory dashboard
with low-stock alerts; and a calendar date picker built without adding a
dependency.

## 8. Troubleshooting

**A red banner saying the API is an older build, or 404s on `/aircraft-types`,
`/obs-register`, `/crew-accounts`, `/main-inventory/receive`,
`/orders/{id}/receive`, `/reports/po-register.xlsx`**

All of these are the same thing: port 8000 is being served by an older copy of
the backend. `uvicorn --reload` only watches the folder it was started in, so
extracting a new copy somewhere else leaves the old one running.

**The quickest check of all:** when the current build starts, it prints a banner
naming the folder it is serving from:

```
================================================================
  Himalaya OBS API  v5.2
  Serving from : C:\Users\you\himalaya-obs\backend
  Database     : C:\Users\you\himalaya-obs\backend\obs.db
  Routes       : 83
================================================================
```

No banner means an older build. The "Serving from" line tells you exactly which
copy is running, which is the usual cause: two folders with the same name.

1. Go to the terminal window running uvicorn and press **Ctrl-C**.
2. `cd` into the `backend` folder you just extracted.
3. Check you are in the right one — this build has files the old one does not:

   ```
   dir crew_seed.py routers\register.py        (Windows)
   ls  crew_seed.py routers/register.py         (macOS, Linux)
   ```

   If either is "not found", you are in the old folder.
4. Start it again: `uvicorn main:app --reload --host 0.0.0.0 --port 8000`
5. Reload the app page.

If step 4 reports that the port is already in use, an old server is still alive.
Find and stop it:

```
netstat -ano | findstr :8000        (Windows, then: taskkill /PID <pid> /F)
lsof -ti:8000 | xargs kill          (macOS, Linux)
```

To confirm which build is answering, open `http://<your-ip>:8000/health`. The
current build reports `"version": "5.2"`; an older one returns only
`{"status": "online"}`, which is what produces the banner. `/docs` lists every
route the running server actually has.

**Blank screens or stale data after upgrading** — the app caches reference data
for offline use. Sign out and back in to refresh it.

**`sqlite3.OperationalError: no such column`** — the database predates a schema
change. Delete `backend/obs.db` and restart; it reseeds.

## 9. Exchange rates

Rates are quoted per **unit** of foreign currency, as your rate sheet does:

| | Currency | Unit | NPR |
|---|---|---|---|
| 1 | INR (Indian Rupee) | 100 | 160.00 |
| 2 | USD (U.S. Dollar) | 1 | 151.00 |
| 3 | EUR (European Euro) | 1 | 174.00 |
| 4 | GBP (UK Pound Sterling) | 1 | 204.00 |
| 5 | AUD (Australian Dollar) | 1 | 106.00 |
| 6 | SGD (Singapore Dollar) | 1 | 117.00 |
| 7 | JPY (Japanese Yen) | 10 | 9.40 |
| 8 | CNY (Chinese Yuan) | 1 | 22.40 |
| 9 | SAR (Saudi Arabian Riyal) | 1 | 40.00 |
| 10 | QAR (Qatari Riyal) | 1 | 41.00 |
| 11 | AED (UAE Dirham) | 1 | 40.90 |
| 12 | MYR (Malaysian Ringgit) | 1 | 36.80 |
| 13 | KWD (Kuwait Dinar) | 1 | 490.00 |

The unit matters: 5,000 INR converts at 160 per 100, giving NPR 8,000, not NPR
800,000. Finance pick the currency and the unit defaults correctly, but it can
be overridden if the quotation convention changes.

## 10. Known limits

- Sessions live in the API process, so restarting `uvicorn` logs everyone out.
  Fine for a prototype; move them to the database before production.
- SQLite is single-writer. It will hold up for a few iPads; a busy fleet wants
  PostgreSQL.
- The API speaks HTTP. Put it behind TLS before it leaves your network.
