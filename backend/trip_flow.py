"""Sector-by-sector crew workflow (7.5), worked by each iPad on its own (7.12).

A trip is worked in fixed steps on the iPad:

  round trip   sell sector 1 -> finish it -> confirm the remaining stock
               -> see the commission -> save the forms (first segment)
               -> START SECOND FLIGHT (first-segment forms lock)
               -> sell sector 2 -> finish it -> submit the final stock return
               -> see the commission -> save the forms -> FINISH WHOLE FLIGHT
  one way      sell -> finish -> final stock return -> commission -> forms -> FINISH WHOLE FLIGHT

7.12: the two iPads on a trip are independent. Everything happens on board
with no signal, so an iPad never waits for the other one: it finishes its own
selling, confirms what IT has left, saves the forms it holds (or marks a form
as the other iPad's), starts the second flight and finishes the whole flight
by itself. The server keeps one row per iPad and leg in trip_device_state and
merges the two iPads when they upload: a leg is FLOWN for Management once
every rostered iPad has finished it, the trip is complete once every iPad has
pressed "Finish whole flight", and the remaining stock is the sum of what the
iPads declared.

A form counts as handled on an iPad when it is submitted (by either iPad) or
when this iPad marked it "submitted by the other iPad" (a deferral).

Saved forms go to Management straight away. Until they lock, the crew may
still change them, but every change needs remarks and becomes a new revision.

State lives on the flights table (the merged, Management view):
  started_at          return leg only: the first iPad pressed "Start second flight"
  reports_locked_at   every iPad has moved on; the sector's forms are closed
  trip_completed_at   set on every leg when every iPad has finished the whole flight
and per iPad in trip_device_state:
  finished_at         this iPad finished selling on the leg
  started_at          return leg only: this iPad pressed "Start second flight"
  forms_locked_at     this iPad moved on from the leg's forms
  completed_at        this iPad pressed "Finish whole flight" (root leg row)
"""

from fastapi import HTTPException

from report_forms import REPORT_TYPES
from sector_stock import sector_count_submitted

FLIGHT_COLUMNS = (
    ("started_at", "TEXT"),
    ("started_by", "TEXT"),
    ("reports_locked_at", "TEXT"),
    ("trip_completed_at", "TEXT"),
    ("trip_completed_by", "TEXT"),
)

DEVICE_STATE_COLUMNS = ("finished_at", "started_at", "forms_locked_at", "completed_at")


def initialize_trip_flow(connection, add_column, columns_of):
    """Add the workflow columns. Return legs that already exist count as
    started, so trips in progress during the upgrade are not blocked."""
    existing = columns_of(connection, "flights")
    first_time = "started_at" not in existing
    for column, definition in FLIGHT_COLUMNS:
        add_column(connection, "flights", column, definition)
    if first_time:
        connection.execute(
            "UPDATE flights SET started_at=COALESCE(created_at, CURRENT_TIMESTAMP), started_by='UPGRADE' "
            "WHERE direction='RETURN'")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS trip_device_state (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               flight_id INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
               device TEXT NOT NULL,
               finished_at TEXT,
               started_at TEXT,
               forms_locked_at TEXT,
               completed_at TEXT,
               UNIQUE(flight_id, device))""")
    connection.commit()


# --------------------------------------------------------------------------- #
# Helpers shared by flights, sales, reports and the stock return
# --------------------------------------------------------------------------- #
def _get(row, key):
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def leg_started(flight) -> bool:
    """Merged view: outbound and one-way legs start as soon as they exist; a
    return leg starts when the first iPad presses "Start second flight"."""
    return _get(flight, "direction") != "RETURN" or bool(_get(flight, "started_at"))


def leg_finished(flight) -> bool:
    return _get(flight, "status") in ("FLOWN", "CLOSED")


# ----- per-iPad state --------------------------------------------------------
def device_state(connection, flight_id: int, device: str) -> dict:
    row = connection.execute(
        "SELECT * FROM trip_device_state WHERE flight_id=? AND device=?", (flight_id, device)).fetchone()
    return dict(row) if row else {c: None for c in DEVICE_STATE_COLUMNS}


def set_device_state(connection, flight_id: int, device: str, column: str, value):
    """Set one flag for one iPad on one leg (idempotent: an existing value is kept)."""
    if column not in DEVICE_STATE_COLUMNS:
        raise ValueError(column)
    connection.execute(
        f"""INSERT INTO trip_device_state(flight_id, device, {column}) VALUES (?,?,?)
            ON CONFLICT(flight_id, device) DO UPDATE SET {column}=COALESCE(trip_device_state.{column}, excluded.{column})""",
        (flight_id, device, value))


def leg_devices(connection, flight_id: int) -> list[str]:
    return [r["crew_account"] for r in connection.execute(
        "SELECT crew_account FROM flight_assignments WHERE flight_id=? ORDER BY id", (flight_id,))]


def leg_started_for(connection, flight, device: str | None) -> bool:
    """Has THIS iPad started the leg? Without a device: the merged view."""
    if _get(flight, "direction") != "RETURN":
        return True
    if not device:
        return bool(_get(flight, "started_at"))
    return bool(device_state(connection, _get(flight, "id"), device)["started_at"])


def leg_finished_for(connection, flight, device: str | None) -> bool:
    """Has THIS iPad finished selling on the leg? A leg Management closed
    (FLOWN/CLOSED) is finished for every iPad."""
    if leg_finished(flight):
        return True
    if not device:
        return False
    return bool(device_state(connection, _get(flight, "id"), device)["finished_at"])


def trip_completed_for(connection, root, device: str | None) -> bool:
    if _get(root, "trip_completed_at"):
        return True
    if not device:
        return False
    return bool(device_state(connection, _get(root, "id"), device)["completed_at"])


def all_devices_done(connection, flight_id: int, column: str) -> bool:
    """Every rostered iPad has set `column` on this leg (false with no roster)."""
    devices = leg_devices(connection, flight_id)
    if not devices:
        return False
    done = {r["device"] for r in connection.execute(
        f"SELECT device FROM trip_device_state WHERE flight_id=? AND {column} IS NOT NULL", (flight_id,))}
    return all(d in done for d in devices)


def assert_started(flight, action="sell"):
    if not leg_started(flight):
        raise HTTPException(
            status_code=400,
            detail=("This is the second flight of the trip. Save the first-segment forms, then press "
                    f"\"Start second flight\" on the Sales page before you {action}."),
        )


def assert_device_may_sell(connection, flight, device: str):
    """7.12: selling is per iPad. This iPad must have started the leg (return
    leg) and must not have finished it; what the other iPad did is irrelevant."""
    if leg_finished(flight):
        raise HTTPException(status_code=400, detail="Selling has been finished on this leg.")
    state = device_state(connection, _get(flight, "id"), device)
    if state["finished_at"]:
        raise HTTPException(
            status_code=400,
            detail="This iPad has finished selling on this sector. Continue with the next step on the Reports page.")
    if _get(flight, "direction") == "RETURN" and not state["started_at"]:
        raise HTTPException(
            status_code=400,
            detail=("This is the second flight of the trip. Press \"Start second flight\" on the Sales page "
                    "of this iPad before selling on it."))


def trip_legs(connection, flight_id: int) -> list[dict]:
    row = connection.execute("SELECT * FROM flights WHERE id=?", (flight_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Flight not found.")
    root = row["linked_flight_id"] if row["direction"] == "RETURN" and row["linked_flight_id"] else row["id"]
    legs = connection.execute(
        "SELECT * FROM flights WHERE id=? OR (linked_flight_id=? AND direction='RETURN') "
        "ORDER BY CASE direction WHEN 'OUTBOUND' THEN 0 ELSE 1 END",
        (root, root)).fetchall()
    return [dict(l) for l in legs]


def reports_filed(connection, flight_id: int) -> int:
    return connection.execute(
        f"SELECT COUNT(*) c FROM flight_reports WHERE flight_id=? AND status='SUBMITTED' "
        f"AND report_type IN ({','.join('?' * len(REPORT_TYPES))})",
        (flight_id, *REPORT_TYPES)).fetchone()["c"]


def reports_handled(connection, flight_id: int, device: str | None = None) -> tuple[int, list[str]]:
    """Forms submitted or deferred to the other iPad; and the ones deferred but
    not yet in. With `device`, only this iPad's own deferrals count."""
    handled, awaiting, _ = report_types_state(connection, flight_id, device)
    return len(handled), awaiting


def report_types_state(connection, flight_id: int, device: str | None = None):
    """(handled types, deferred-but-not-in types, submitted types)."""
    submitted = {r["report_type"] for r in connection.execute(
        "SELECT report_type FROM flight_reports WHERE flight_id=? AND status='SUBMITTED'", (flight_id,))}
    if device:
        deferred = {r["report_type"] for r in connection.execute(
            "SELECT DISTINCT report_type FROM flight_report_deferrals WHERE flight_id=? AND device=?",
            (flight_id, device))}
    else:
        deferred = {r["report_type"] for r in connection.execute(
            "SELECT DISTINCT report_type FROM flight_report_deferrals WHERE flight_id=?", (flight_id,))}
    handled = [t for t in REPORT_TYPES if t in submitted or t in deferred]
    awaiting = [t for t in REPORT_TYPES if t in deferred and t not in submitted]
    return handled, awaiting, [t for t in REPORT_TYPES if t in submitted]


def stock_return_status(connection, root_id: int) -> str:
    row = connection.execute("SELECT status FROM reconciliations WHERE flight_id=?", (root_id,)).fetchone()
    return row["status"] if row else "NOT_SUBMITTED"


def stock_return_devices(connection, root_id: int) -> list[str]:
    row = connection.execute("SELECT id FROM reconciliations WHERE flight_id=?", (root_id,)).fetchone()
    if not row:
        return []
    return [r["device"] for r in connection.execute(
        "SELECT device FROM stock_return_devices WHERE reconciliation_id=?", (row["id"],))]


def progress(connection, flight_id: int, device: str | None = None) -> dict:
    """Where the trip stands and what the crew should do next.

    With `device` (an iPad account) every flag is THIS iPad's own: what it has
    finished, counted, saved and started, regardless of the other iPad.
    Without a device the merged (Management) view is returned, with a
    per-iPad breakdown in `devices_state`.
    """
    legs = trip_legs(connection, flight_id)
    root = legs[0]
    round_trip = len(legs) > 1
    total = len(REPORT_TYPES)
    out = []
    for index, leg in enumerate(legs):
        handled_types, awaiting, filed_types = report_types_state(connection, leg["id"], device)
        filed, handled = len(filed_types), len(handled_types)
        devices = leg_devices(connection, leg["id"])
        counted = [r["device"] for r in connection.execute(
            "SELECT device FROM sector_stock_counts WHERE flight_id=?", (leg["id"],))]
        states = {r["device"]: dict(r) for r in connection.execute(
            "SELECT * FROM trip_device_state WHERE flight_id=?", (leg["id"],))}
        per_device = []
        for d in devices:
            s = states.get(d, {})
            d_handled, _ = reports_handled(connection, leg["id"], d)
            per_device.append({
                "device": d,
                "started": leg["direction"] != "RETURN" or bool(s.get("started_at")) or bool(leg["started_at"] and not states),
                "finished": leg_finished(leg) or bool(s.get("finished_at")),
                "finished_at": s.get("finished_at") or (leg["finished_at"] if leg_finished(leg) else None),
                "stock_count_done": d in counted or "*" in counted,
                "reports_done": d_handled >= total,
                "forms_locked": bool(s.get("forms_locked_at") or leg["reports_locked_at"]),
                "completed": bool(s.get("completed_at")) or bool(root.get("trip_completed_at")),
            })
        if device:
            started = leg_started_for(connection, leg, device)
            finished = leg_finished_for(connection, leg, device)
            stock_done = device in counted or "*" in counted
            locked = bool(leg["reports_locked_at"] or leg["trip_completed_at"]
                          or states.get(device, {}).get("forms_locked_at"))
        else:
            started = leg_started(leg)
            finished = leg_finished(leg)
            stock_done = sector_count_submitted(connection, leg["id"])
            locked = bool(leg["reports_locked_at"])
        out.append({
            "id": leg["id"], "flight_number": leg["flight_number"], "flight_date": leg["flight_date"],
            "origin": leg["origin"], "destination": leg["destination"], "direction": leg["direction"],
            "status": leg["status"], "segment": index + 1, "started": started,
            "finished": finished,
            "reports_filed": filed, "reports_total": total,
            # done = every form submitted or marked "submitted by the other iPad"
            "reports_done": handled >= total, "reports_handled": handled, "reports_awaiting_other": awaiting,
            "reports_handled_types": handled_types, "reports_filed_types": filed_types,
            "reports_locked": locked,
            "devices": devices,
            "devices_state": per_device,
            # Every sector except the last confirms its remaining stock before
            # the next one starts; the last sector ends with the stock return.
            "stock_count_needed": index < len(legs) - 1,
            "stock_count_done": stock_done,
            "stock_count_devices": counted,
            "stock_count_waiting_for": [d for d in devices if d not in counted] if not device else [],
            "my_stock_count_done": (device in counted or "*" in counted) if device else None,
        })
    completed = trip_completed_for(connection, root, device)
    stock = stock_return_status(connection, root["id"])
    stock_devices = stock_return_devices(connection, root["id"])
    if device:
        stock_done = device in stock_devices or "*" in stock_devices or stock == "APPROVED"
    else:
        stock_done = stock in ("PENDING", "APPROVED")
    first, last = out[0], out[-1]

    can_start_second = (round_trip and first["finished"] and first["reports_done"]
                        and first["stock_count_done"] and not last["started"] and not completed)
    all_finished = all(l["finished"] for l in out)
    all_reports = all(l["reports_done"] for l in out)
    all_counts = all(l["stock_count_done"] for l in out if l["stock_count_needed"])
    can_complete = all_finished and all_reports and all_counts and stock_done and not completed

    # 7.11 order: sell -> finish -> remaining stock -> (commission) -> forms -> next.
    if completed:
        step = "DONE"
    elif not first["finished"]:
        step = "SELL_FIRST"
    elif round_trip and not last["started"]:
        if not first["stock_count_done"]:
            step = "STOCK_FIRST"
        elif not first["reports_done"]:
            step = "FORMS_FIRST"
        else:
            step = "START_SECOND"
    elif round_trip and not last["finished"]:
        step = "SELL_SECOND"
    elif not stock_done:
        step = "STOCK_RETURN"
    elif not last["reports_done"]:
        step = "FORMS_LAST"
    else:
        step = "COMPLETE"

    completed_devices = [r["device"] for r in connection.execute(
        "SELECT device FROM trip_device_state WHERE flight_id=? AND completed_at IS NOT NULL", (root["id"],))]
    return {
        "root_id": root["id"], "round_trip": round_trip, "legs": out, "step": step,
        "device": device,
        "stock_return": stock, "stock_return_devices": stock_devices,
        "stock_return_waiting_for": [] if (stock_done or device) else [d for d in last["devices"] if d not in stock_devices],
        "my_stock_return_done": (device in stock_devices or "*" in stock_devices) if device else None,
        "completed": completed,
        "completed_devices": completed_devices,
        "completed_at": root.get("trip_completed_at"), "completed_by": root.get("trip_completed_by"),
        "can_start_second": can_start_second, "can_complete": can_complete,
    }


def assert_reports_open(connection, flight_id: int, may_review: bool, device: str | None = None):
    """The iPad may change a sector's forms only until they are locked: for
    this iPad once it moved on (started the next flight or finished the whole
    flight), and for everyone once the sector is closed to all iPads."""
    if may_review:
        return
    row = connection.execute(
        "SELECT reports_locked_at, trip_completed_at FROM flights WHERE id=?", (flight_id,)).fetchone()
    locked = bool(row and (row["reports_locked_at"] or row["trip_completed_at"]))
    if not locked and device:
        locked = bool(device_state(connection, flight_id, device)["forms_locked_at"])
    if locked:
        raise HTTPException(
            status_code=400,
            detail="These forms are locked: the next flight was started or the whole flight was finished. "
                   "Ask Management if something still needs changing.")
