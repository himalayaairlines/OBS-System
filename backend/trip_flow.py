"""Sector-by-sector crew workflow (7.5).

A trip is worked in fixed steps on the iPad:

  round trip   sell sector 1 -> finish it -> each iPad confirms the remaining stock
               -> see the commission -> save its forms (first segment)
               -> START SECOND FLIGHT (first-segment forms lock)
               -> sell sector 2 -> finish it -> each iPad submits the final stock return
               -> see the commission -> save its forms -> FINISH WHOLE FLIGHT
  one way      sell -> finish -> final stock return -> commission -> forms -> FINISH WHOLE FLIGHT

7.11: two iPads work the trip. A form counts as handled when it is submitted,
or when one iPad marks it "submitted by the other iPad" (a deferral): the
other device's own submission then arrives when it syncs.

Saved forms go to Management straight away. Until they lock, the crew may
still change them, but every change needs remarks and becomes a new revision.

State lives on the flights table:
  started_at          return leg only: when "Start second flight" was pressed
  reports_locked_at   the sector's forms are closed to the iPad
  trip_completed_at   set on every leg when "Finish whole flight" is pressed
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


# --------------------------------------------------------------------------- #
# Helpers shared by flights, sales, reports and the stock return
# --------------------------------------------------------------------------- #
def _get(row, key):
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def leg_started(flight) -> bool:
    """Outbound and one-way legs start as soon as they exist; a return leg
    starts when the crew press "Start second flight"."""
    return _get(flight, "direction") != "RETURN" or bool(_get(flight, "started_at"))


def assert_started(flight, action="sell"):
    if not leg_started(flight):
        raise HTTPException(
            status_code=400,
            detail=("This is the second flight of the trip. Save the first-segment forms, then press "
                    f"\"Start second flight\" on the Sales page before you {action}."),
        )


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


def reports_handled(connection, flight_id: int) -> tuple[int, list[str]]:
    """Forms submitted or deferred to the other iPad; and the ones deferred but not yet in."""
    submitted = {r["report_type"] for r in connection.execute(
        "SELECT report_type FROM flight_reports WHERE flight_id=? AND status='SUBMITTED'", (flight_id,))}
    deferred = {r["report_type"] for r in connection.execute(
        "SELECT DISTINCT report_type FROM flight_report_deferrals WHERE flight_id=?", (flight_id,))}
    handled = [t for t in REPORT_TYPES if t in submitted or t in deferred]
    awaiting = [t for t in REPORT_TYPES if t in deferred and t not in submitted]
    return len(handled), awaiting


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

    With `device` (an iPad account) the per-device flags say what THIS iPad
    still has to do; the trip-level flags need every iPad.
    """
    legs = trip_legs(connection, flight_id)
    root = legs[0]
    round_trip = len(legs) > 1
    total = len(REPORT_TYPES)
    out = []
    for index, leg in enumerate(legs):
        filed = reports_filed(connection, leg["id"])
        handled, awaiting = reports_handled(connection, leg["id"])
        devices = [r["crew_account"] for r in connection.execute(
            "SELECT crew_account FROM flight_assignments WHERE flight_id=? ORDER BY id", (leg["id"],))]
        counted = [r["device"] for r in connection.execute(
            "SELECT device FROM sector_stock_counts WHERE flight_id=?", (leg["id"],))]
        out.append({
            "id": leg["id"], "flight_number": leg["flight_number"], "flight_date": leg["flight_date"],
            "origin": leg["origin"], "destination": leg["destination"], "direction": leg["direction"],
            "status": leg["status"], "segment": index + 1, "started": leg_started(leg),
            "finished": leg["status"] in ("FLOWN", "CLOSED"),
            "reports_filed": filed, "reports_total": total,
            # done = every form submitted or marked "submitted by the other iPad"
            "reports_done": handled >= total, "reports_handled": handled, "reports_awaiting_other": awaiting,
            "reports_locked": bool(leg["reports_locked_at"]),
            "devices": devices,
            # Every sector except the last confirms its remaining stock before
            # the next one starts; the last sector ends with the stock return.
            "stock_count_needed": index < len(legs) - 1,
            "stock_count_done": sector_count_submitted(connection, leg["id"]),
            "stock_count_devices": counted,
            "stock_count_waiting_for": [d for d in devices if d not in counted],
            "my_stock_count_done": (device in counted) if device else None,
        })
    completed = bool(root.get("trip_completed_at"))
    stock = stock_return_status(connection, root["id"])
    stock_done = stock in ("PENDING", "APPROVED")
    stock_devices = stock_return_devices(connection, root["id"])
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

    return {
        "root_id": root["id"], "round_trip": round_trip, "legs": out, "step": step,
        "stock_return": stock, "stock_return_devices": stock_devices,
        "stock_return_waiting_for": [] if stock_done else [d for d in last["devices"] if d not in stock_devices],
        "my_stock_return_done": (device in stock_devices) if device else None,
        "completed": completed,
        "completed_at": root.get("trip_completed_at"), "completed_by": root.get("trip_completed_by"),
        "can_start_second": can_start_second, "can_complete": can_complete,
    }


def assert_reports_open(connection, flight_id: int, may_review: bool):
    """The iPad may change a sector's forms only until they are locked."""
    if may_review:
        return
    row = connection.execute(
        "SELECT reports_locked_at, trip_completed_at FROM flights WHERE id=?", (flight_id,)).fetchone()
    if row and (row["reports_locked_at"] or row["trip_completed_at"]):
        raise HTTPException(
            status_code=400,
            detail="These forms are locked: the next flight was started or the whole flight was finished. "
                   "Ask Management if something still needs changing.")
