"""Exchange rates.

Owned by the finance role. A rate is a dated band: "from this date to that
date, one USD is worth this many NPR". Future bands can be pre-set. Saving a
band automatically closes the previous open-ended band for that currency so
they never overlap.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import get_connection
from security import current_user, log_action, requires, today_iso, validate_date

router = APIRouter(tags=["rates"])

# Name and the unit each currency is normally quoted in.
CURRENCIES = {
    "NPR": ("Nepalese Rupee", 1),
    "INR": ("Indian Rupee", 100),
    "USD": ("U.S. Dollar", 1),
    "EUR": ("European Euro", 1),
    "GBP": ("UK Pound Sterling", 1),
    "AUD": ("Australian Dollar", 1),
    "SGD": ("Singapore Dollar", 1),
    "JPY": ("Japanese Yen", 10),
    "CNY": ("Chinese Yuan", 1),
    "SAR": ("Saudi Arabian Riyal", 1),
    "QAR": ("Qatari Riyal", 1),
    "AED": ("UAE Dirham", 1),
    "MYR": ("Malaysian Ringgit", 1),
    "KWD": ("Kuwait Dinar", 1),
}
CURRENCY_NAMES = {code: name for code, (name, _) in CURRENCIES.items()}
CURRENCY_UNITS = {code: unit for code, (_, unit) in CURRENCIES.items()}


class RateIn(BaseModel):
    currency_code: str
    unit: float = Field(gt=0, default=1)
    rate_to_npr: float = Field(gt=0)
    effective_from: str
    effective_to: str | None = None


def active_rates(on: str | None = None):
    """The rate in force for each currency on a given date."""
    target = on or today_iso()
    connection = get_connection()
    try:
        codes = [r["currency_code"] for r in connection.execute(
            "SELECT DISTINCT currency_code FROM exchange_rates ORDER BY currency_code"
        ).fetchall()]
        result = []
        for code in codes:
            row = connection.execute(
                """SELECT * FROM exchange_rates
                   WHERE currency_code=? AND active=1 AND effective_from <= ?
                     AND (effective_to IS NULL OR effective_to >= ?)
                   ORDER BY effective_from DESC, id DESC LIMIT 1""",
                (code, target, target),
            ).fetchone()
            if row:
                unit = float(row["unit"] or 1) or 1
                result.append({
                    "currency_code": code,
                    "currency_name": CURRENCY_NAMES.get(code, code),
                    "unit": unit,
                    "rate_to_npr": float(row["rate_to_npr"]),
                    # What one unit of the currency is worth, for the sales screen.
                    "per_unit_npr": round(float(row["rate_to_npr"]) / unit, 6),
                    "effective_from": row["effective_from"],
                    "effective_to": row["effective_to"],
                })
        return result
    finally:
        connection.close()


@router.get("/exchange-rates/active")
def get_active_rates(on: str | None = None, user: dict = Depends(current_user)):
    return active_rates(validate_date(on, "date") if on else None)


@router.get("/exchange-rates")
def rate_history(currency: str | None = None, user: dict = Depends(requires("rates"))):
    connection = get_connection()
    try:
        sql = "SELECT * FROM exchange_rates"
        params = []
        if currency:
            sql += " WHERE currency_code=?"
            params.append(currency.strip().upper())
        sql += " ORDER BY currency_code, effective_from DESC, id DESC"
        rows = connection.execute(sql, params).fetchall()
        today = today_iso()
        result = []
        for row in rows:
            item = dict(row)
            item["currency_name"] = CURRENCY_NAMES.get(item["currency_code"], item["currency_code"])
            item["unit"] = float(item.get("unit") or 1) or 1
            item["per_unit_npr"] = round(float(item["rate_to_npr"]) / item["unit"], 6)
            if item["effective_from"] > today:
                item["state"] = "SCHEDULED"
            elif item["effective_to"] and item["effective_to"] < today:
                item["state"] = "EXPIRED"
            elif not item["active"]:
                item["state"] = "INACTIVE"
            else:
                item["state"] = "CURRENT"
            result.append(item)
        return result
    finally:
        connection.close()


@router.get("/exchange-rates/currencies")
def known_currencies(user: dict = Depends(current_user)):
    return [
        {"code": code, "name": name, "unit": unit}
        for code, (name, unit) in CURRENCIES.items()
    ]


@router.post("/exchange-rates")
def save_rate(data: RateIn, user: dict = Depends(requires("rates_edit"))):
    code = data.currency_code.strip().upper()
    if code == "NPR":
        raise HTTPException(status_code=400, detail="The NPR base rate is fixed at 1.")
    effective_from = validate_date(data.effective_from, "effective from date")
    effective_to = validate_date(data.effective_to, "effective to date") if data.effective_to else None
    if effective_to and effective_to < effective_from:
        raise HTTPException(status_code=400, detail="The end date cannot be before the start date.")

    connection = get_connection()
    try:
        overlap = connection.execute(
            """SELECT * FROM exchange_rates
               WHERE currency_code=? AND active=1
                 AND effective_from <= ?
                 AND (effective_to IS NULL OR effective_to >= ?)""",
            (code, effective_to or "9999-12-31", effective_from),
        ).fetchall()
        for row in overlap:
            if row["effective_from"] >= effective_from and (
                effective_to is None or (row["effective_to"] and row["effective_to"] <= effective_to)
            ):
                # Fully covered by the new band, so retire it.
                connection.execute("UPDATE exchange_rates SET active=0 WHERE id=?", (row["id"],))
            else:
                # Close the earlier band the day before the new one starts.
                from datetime import date, timedelta

                previous_day = (date.fromisoformat(effective_from) - timedelta(days=1)).isoformat()
                if row["effective_from"] <= previous_day:
                    connection.execute(
                        "UPDATE exchange_rates SET effective_to=? WHERE id=?",
                        (previous_day, row["id"]),
                    )
                else:
                    connection.execute("UPDATE exchange_rates SET active=0 WHERE id=?", (row["id"],))

        cur = connection.execute(
            """INSERT INTO exchange_rates
               (currency_code,unit,rate_to_npr,effective_from,effective_to,updated_by,active)
               VALUES (?,?,?,?,?,?,1)""",
            (code, data.unit, data.rate_to_npr, effective_from, effective_to,
             user["employee_id"]),
        )
        rate_id = cur.lastrowid
        connection.commit()
    finally:
        connection.close()

    log_action(user, "EXCHANGE RATE", "SAVE_RATE",
               f"{data.unit:g} {code} set to NPR {data.rate_to_npr:,.4f} from {effective_from} "
               f"to {effective_to or 'open-ended'}.",
               "exchange_rate", rate_id, metadata=data.model_dump())
    return {"status": "success", "id": rate_id, "currency_code": code,
            "unit": data.unit, "rate_to_npr": data.rate_to_npr,
            "per_unit_npr": round(data.rate_to_npr / data.unit, 6),
            "effective_from": effective_from, "effective_to": effective_to}


@router.delete("/exchange-rates/{rate_id}")
def retire_rate(rate_id: int, user: dict = Depends(requires("rates_edit"))):
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM exchange_rates WHERE id=?", (rate_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Rate not found.")
        if row["updated_by"] == "SYSTEM" and row["effective_from"] == "2000-01-01":
            raise HTTPException(status_code=400, detail="The system baseline rate cannot be removed.")
        connection.execute("UPDATE exchange_rates SET active=0 WHERE id=?", (rate_id,))
        connection.commit()
    finally:
        connection.close()
    log_action(user, "EXCHANGE RATE", "RETIRE_RATE",
               f"Retired the {row['currency_code']} band starting {row['effective_from']}.",
               "exchange_rate", rate_id)
    return {"status": "success"}


# --------------------------------------------------------------------------- #
# Monthly rates (7.18): the rates are fixed for a calendar month.
# --------------------------------------------------------------------------- #
def _month_bounds(month: str) -> tuple[str, str]:
    import calendar
    import re as _re
    if not month or not _re.fullmatch(r"\d{4}-\d{2}", month.strip()):
        raise HTTPException(status_code=400, detail="Choose the month as YYYY-MM, e.g. 2026-11.")
    year, mon = (int(x) for x in month.strip().split("-"))
    if not 1 <= mon <= 12 or not 2000 <= year <= 2100:
        raise HTTPException(status_code=400, detail="That month is not valid.")
    last = calendar.monthrange(year, mon)[1]
    return f"{year:04d}-{mon:02d}-01", f"{year:04d}-{mon:02d}-{last:02d}"


def _previous_month(month: str) -> str:
    year, mon = (int(x) for x in month.split("-"))
    return f"{year - 1:04d}-12" if mon == 1 else f"{year:04d}-{mon - 1:02d}"


def _month_rates(month: str) -> list[dict]:
    first, last = _month_bounds(month)
    connection = get_connection()
    try:
        out = []
        for code, (name, default_unit) in CURRENCIES.items():
            if code == "NPR":
                continue
            row = connection.execute(
                """SELECT * FROM exchange_rates WHERE currency_code=? AND active=1
                     AND effective_from <= ? AND (effective_to IS NULL OR effective_to >= ?)
                   ORDER BY effective_from DESC, id DESC LIMIT 1""", (code, first, first)).fetchone()
            unit = float(row["unit"] or default_unit) if row else float(default_unit)
            out.append({
                "currency_code": code, "currency_name": name, "unit": unit,
                "rate_to_npr": float(row["rate_to_npr"]) if row else None,
                "per_unit_npr": round(float(row["rate_to_npr"]) / unit, 6) if row else None,
                "effective_from": row["effective_from"] if row else None,
                "effective_to": row["effective_to"] if row else None,
                "set_for_month": bool(row and row["effective_from"] == first and row["effective_to"] == last),
                "updated_by": row["updated_by"] if row else None,
            })
        return out
    finally:
        connection.close()


@router.get("/exchange-rates/month")
def month_rates(month: str, user: dict = Depends(requires("rates"))):
    first, last = _month_bounds(month)
    return {"month": month, "from": first, "to": last, "rates": _month_rates(month),
            "previous_month": _previous_month(month)}


class MonthRate(BaseModel):
    currency_code: str
    unit: float = Field(gt=0, default=1)
    rate_to_npr: float = Field(gt=0, le=1_000_000)


class MonthRatesIn(BaseModel):
    month: str
    rates: list[MonthRate]


@router.post("/exchange-rates/month")
def save_month_rates(data: MonthRatesIn, user: dict = Depends(requires("rates_edit"))):
    """Set the rates for a whole month (1st to last day) in one go."""
    first, last = _month_bounds(data.month)
    saved = []
    for rate in data.rates:
        code = rate.currency_code.strip().upper()
        if code not in CURRENCIES or code == "NPR":
            raise HTTPException(status_code=400, detail=f"{code} is not a currency the system takes.")
        save_rate(RateIn(currency_code=code, unit=rate.unit, rate_to_npr=rate.rate_to_npr,
                         effective_from=first, effective_to=last), user)
        saved.append(code)
    return {"month": data.month, "saved": saved, "rates": _month_rates(data.month)}


class CopyIn(BaseModel):
    month: str
    overwrite: bool = False


@router.post("/exchange-rates/month/copy")
def copy_previous_month(data: CopyIn, user: dict = Depends(requires("rates_edit"))):
    """Copy the previous month's rates into this month. Currencies already set
    for this month are kept unless `overwrite`."""
    _month_bounds(data.month)
    previous = {r["currency_code"]: r for r in _month_rates(_previous_month(data.month)) if r["rate_to_npr"]}
    if not previous:
        raise HTTPException(status_code=400, detail="The previous month has no rates to copy.")
    current = {r["currency_code"]: r for r in _month_rates(data.month)}
    to_copy = [MonthRate(currency_code=c, unit=r["unit"], rate_to_npr=r["rate_to_npr"])
               for c, r in previous.items() if data.overwrite or not current.get(c, {}).get("set_for_month")]
    if not to_copy:
        return {"month": data.month, "saved": [], "rates": list(current.values()),
                "detail": "Every currency is already set for this month."}
    result = save_month_rates(MonthRatesIn(month=data.month, rates=to_copy), user)
    log_action(user, "EXCHANGE RATE", "COPY_MONTH",
               f"Copied {len(to_copy)} rate(s) from {_previous_month(data.month)} into {data.month}.")
    return result
