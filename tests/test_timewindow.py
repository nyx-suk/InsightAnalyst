"""tests/test_timewindow.py
Unit tests for agent/timewindow.py as specified in Prompt 2.2.
Verifies partial final month, complete final month, relative quarters, n-months, and explicit ranges.
"""

from datetime import date
from pathlib import Path
import sys
import unittest

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.timewindow import resolve_window


class TestTimeWindowResolver(unittest.TestCase):
    """Test suite for deterministic relative date window resolution."""

    def test_partial_final_month(self):
        """Verifies that if the final month is partial (e.g. 2023-10-28 in Northwind),

        the last complete month is the one immediately before it (September 2023).
        """
        max_date = date(2023, 10, 28)
        start, end = resolve_window({"value": "last_complete_month"}, max_date)
        self.assertEqual(start, date(2023, 9, 1))
        self.assertEqual(end, date(2023, 9, 30))

    def test_complete_final_month(self):
        """Verifies that if data_max_date is on the last day of the month (e.g. 2023-10-31),

        that month itself is treated as the last complete month.
        """
        max_date = date(2023, 10, 31)
        start, end = resolve_window({"value": "last_complete_month"}, max_date)
        self.assertEqual(start, date(2023, 10, 1))
        self.assertEqual(end, date(2023, 10, 31))

    def test_previous_month_before_last(self):
        """Verifies previous_month_before_last steps back one month before last complete month."""
        max_date = date(2023, 10, 28)
        start, end = resolve_window({"value": "previous_month_before_last"}, max_date)
        self.assertEqual(start, date(2023, 8, 1))
        self.assertEqual(end, date(2023, 8, 31))

    def test_last_complete_quarter(self):
        """Verifies that for 2023-10-28, the last complete quarter is Q3 (July to September 2023)."""
        max_date = date(2023, 10, 28)
        start, end = resolve_window({"value": "last_complete_quarter"}, max_date)
        self.assertEqual(start, date(2023, 7, 1))
        self.assertEqual(end, date(2023, 9, 30))

    def test_previous_quarter(self):
        """Verifies that previous_quarter resolves to Q2 (April to June 2023)."""
        max_date = date(2023, 10, 28)
        start, end = resolve_window({"value": "previous_quarter"}, max_date)
        self.assertEqual(start, date(2023, 4, 1))
        self.assertEqual(end, date(2023, 6, 30))

    def test_last_n_months(self):
        """Verifies that last_n_months with n=3 returns a 3-month window ending on the last complete month."""
        max_date = date(2023, 10, 28)
        start, end = resolve_window({"value": "last_n_months", "n": 3}, max_date)
        self.assertEqual(start, date(2023, 7, 1))
        self.assertEqual(end, date(2023, 9, 30))

    def test_explicit_window(self):
        """Verifies that explicit start and end date strings are parsed into date objects."""
        spec = {"start": "2023-01-01", "end": "2023-03-31"}
        start, end = resolve_window(spec, date(2023, 10, 28))
        self.assertEqual(start, date(2023, 1, 1))
        self.assertEqual(end, date(2023, 3, 31))

    def test_year_boundary_rollover(self):
        """Verifies that a partial January date rolls back cleanly into December of the previous year."""
        max_date = date(2024, 1, 15)
        start, end = resolve_window({"value": "last_complete_month"}, max_date)
        self.assertEqual(start, date(2023, 12, 1))
        self.assertEqual(end, date(2023, 12, 31))

    def test_datetime_string_max_date_parsing(self):
        """Verifies that max_date passed as an ISO datetime string is accepted."""
        start, end = resolve_window("last_complete_month", "2023-10-28 14:30:00")
        self.assertEqual(start, date(2023, 9, 1))
        self.assertEqual(end, date(2023, 9, 30))


if __name__ == "__main__":
    unittest.main()
