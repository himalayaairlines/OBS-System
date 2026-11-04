"""Sector-by-sector crew workflow (7.5).

A trip is worked in fixed steps on the iPad:

  round trip   sell sector 1 -> finish it -> save its forms (first segment)
               -> confirm the remaining stock after sector 1 (7.9)
               -> START SECOND FLIGHT (first-segment forms lock)
               -> sell sector 2 -> finish it -> save its forms (second segment)
               -> submit the final stock return -> FINISH WHOLE FLIGHT (everything locks)
  one way      sell -> finish -> save the forms -> stock return -> FINISH WHOLE FLIGHT

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


def stock_return_status(connection, root_id: int) -> str:
    row = connection.execute("SELECT status FROM reconciliations WHERE flight_id=?", (root_id,)).fetchone()
    return row["status"] if row else "NOT_SUBMITTED"


def progress(connection, flight_id: int) -> dict:
    """Where the trip stands and what the crew should do next."""
    legs = trip_legs(connection, flight_id)
    root = legs[0]
    round_trip = len(legs) > 1
    total = len(REPORT_TYPES)
    out = []
    for index, leg in enumerate(legs):
        filed = reports_filed(connection, leg["id"])
        out.append({
            "id": leg["id"], "flight_number": leg["flight_number"], "flight_date": leg["flight_date"],
            "origin": leg["origin"], "destination": leg["destination"], "direction": leg["direction"],
            "status": leg["status"], "segment": index + 1, "started": leg_started(leg),
            "finished": leg["status"] in ("FLOWN", "CLOSED"),
            "reports_filed": filed, "reports_total": total, "reports_done": filed >= total,
            "reports_locked": bool(leg["reports_locked_at"]),
            # Every sector except the last confirms its remaining stock before
            # the next one starts; the last sector ends with the stock return.
            "stock_count_needed": index < len(legs) - 1,
            "stock_count_done": sector_count_submitted(connection, leg["id"]),
        })
    completed = bool(root.get("trip_completed_at"))
    stock = stock_return_status(connection, root["id"])
    stock_done = stock in ("PENDING", "APPROVED")
    first, last = out[0], out[-1]

    can_start_second = (round_trip and first["finished"] and first["reports_done"]
                        and first["stock_count_done"] and not last["started"] and not completed)
    all_finished = all(l["finished"] for l in out)
    all_reports = all(l["reports_done"] for l in out)
    all_counts = all(l["stock_count_done"] for l in out if l["stock_count_needed"])
    can_complete = all_finished and all_reports and all_counts and stock_done and not completed

    if completed:
        step = "DONE"
    elif not first["finished"]:
        step = "SELL_FIRST"
    elif round_trip and not last["started"]:
        step = ("START_SECOND" if first["stock_count_done"] else "STOCK_FIRST") if first["reports_done"] else "FORMS_FIRST"
    elif round_trip and not last["finished"]:
        step = "SELL_SECOND"
    elif not last["reports_done"]:
        step = "FORMS_LAST"
    elif not stock_done:
        step = "STOCK_RETURN"
    else:
        step = "COMPLETE"

    return {
        "root_id": root["id"], "round_trip": round_trip, "legs": out, "step": step,
        "stock_return": stock, "completed": completed,
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
