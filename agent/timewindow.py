"""agent/timewindow.py
Time window resolver adhering to Prompt 2.2.
Resolves relative temporal specifications (e.g. 'last_complete_month') against dataset max date.
The LLM never computes dates; it specifies a relative window, and this module deterministically resolves it.
"""

import calendar
from datetime import date, datetime
from typing import Any, Dict, Tuple, Union


def _parse_date(val: Union[str, date, datetime]) -> date:
    """Parses a date string or datetime into a date object."""
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    text = str(val).strip()
    # Try ISO date format first (YYYY-MM-DD)
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Cannot parse date from value: '{val}'")


def _is_month_complete(dt: date) -> bool:
    """Returns True if dt is on or after the last calendar day of its month."""
    _, last_day = calendar.monthrange(dt.year, dt.month)
    return dt.day >= last_day


def _get_last_complete_month(data_max_date: date) -> Tuple[int, int]:
    """Returns (year, month) of the latest complete month."""
    if _is_month_complete(data_max_date):
        return data_max_date.year, data_max_date.month

    # If final month is partial, last complete month is the one before it
    if data_max_date.month == 1:
        return data_max_date.year - 1, 12
    return data_max_date.year, data_max_date.month - 1


def _shift_month(year: int, month: int, delta: int) -> Tuple[int, int]:
    """Shifts year and month by delta months (positive or negative)."""
    total_months = (year * 12 + (month - 1)) + delta
    new_year = total_months // 12
    new_month = (total_months % 12) + 1
    return new_year, new_month


def _month_bounds(year: int, month: int) -> Tuple[date, date]:
    """Returns (start_date, end_date) for a given year and month."""
    _, last_day = calendar.monthrange(year, month)
    return date(year, month, 1), date(year, month, last_day)


def _get_quarter_bounds(year: int, quarter: int) -> Tuple[date, date]:
    """Returns (start_date, end_date) for a specific quarter (1 to 4)."""
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    _, end_day = calendar.monthrange(year, end_month)
    return date(year, start_month, 1), date(year, end_month, end_day)


def resolve_window(spec: Union[Dict[str, Any], str], data_max_date: Union[date, str]) -> Tuple[date, date]:
    """Resolves relative temporal specifications against the maximum date in the database.

    Args:
        spec: Dictionary or string describing the relative or explicit time window.
              Supported spec values:
                - 'last_complete_month'
                - 'previous_month_before_last'
                - 'last_complete_quarter'
                - 'previous_quarter'
                - 'last_n_months' (with 'n')
                - explicit {'start': ..., 'end': ...}
        data_max_date: The latest OrderDate in the dataset.

    Returns:
        Tuple of (start_date, end_date) as datetime.date objects.
    """
    max_d = _parse_date(data_max_date)

    # Normalize spec dictionary
    if isinstance(spec, str):
        spec_dict = {"value": spec, "type": "relative"}
    else:
        spec_dict = dict(spec)

    # 1. Check for explicit start/end
    if "start" in spec_dict and "end" in spec_dict:
        return _parse_date(spec_dict["start"]), _parse_date(spec_dict["end"])

    kind = str(spec_dict.get("value") or spec_dict.get("type") or "").lower()

    # Identify last complete month
    lcm_year, lcm_month = _get_last_complete_month(max_d)

    if kind == "last_complete_month":
        return _month_bounds(lcm_year, lcm_month)

    if kind in ("previous_month_before_last", "previous_month", "prior_month"):
        prev_year, prev_month = _shift_month(lcm_year, lcm_month, -1)
        return _month_bounds(prev_year, prev_month)

    if kind in ("last_complete_quarter", "quarter"):
        # Quarter is complete if max_d is on/after end of quarter
        curr_q = (max_d.month - 1) // 3 + 1
        q_start, q_end = _get_quarter_bounds(max_d.year, curr_q)
        if max_d >= q_end:
            target_q = curr_q
            target_year = max_d.year
        else:
            # Previous quarter
            if curr_q == 1:
                target_q = 4
                target_year = max_d.year - 1
            else:
                target_q = curr_q - 1
                target_year = max_d.year
        return _get_quarter_bounds(target_year, target_q)

    if kind in ("previous_quarter", "prior_quarter"):
        # First resolve last complete quarter, then step back 1 quarter
        q_start, _ = resolve_window({"value": "last_complete_quarter"}, max_d)
        curr_q = (q_start.month - 1) // 3 + 1
        if curr_q == 1:
            prev_q = 4
            prev_year = q_start.year - 1
        else:
            prev_q = curr_q - 1
            prev_year = q_start.year
        return _get_quarter_bounds(prev_year, prev_q)

    if kind in ("last_n_months", "n_months"):
        n = int(spec_dict.get("n", 3))
        if n < 1:
            n = 1
        start_year, start_month = _shift_month(lcm_year, lcm_month, -(n - 1))
        start_date = date(start_year, start_month, 1)
        _, end_day = calendar.monthrange(lcm_year, lcm_month)
        end_date = date(lcm_year, lcm_month, end_day)
        return start_date, end_date

    # Default fallback: last complete month
    return _month_bounds(lcm_year, lcm_month)
