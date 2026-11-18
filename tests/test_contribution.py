"""tests/test_contribution.py
Unit tests for agent/contribution.py (Prompt 3.2).
Tests pure Python contribution analysis, ranking, and reconciliation verification.
"""

import unittest
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import QueryResult
from agent.contribution import (
    analyze_contribution,
    compare_two_results,
    build_driver_finding,
    ContributionAnalysis,
    ContributionItem
)


class TestContributionAnalysis(unittest.TestCase):
    """Test suite for contribution and variance analysis."""

    def test_standard_reconciliation_pass(self):
        """Case 1: Standard breakdown comparing two periods where sum matches perfectly."""
        # Baseline (Aug): 1,000 -> Current (Sep): 920. Total drop: -80 (-8.0%)
        # South: 200 -> 150 (-50, -25.0%, drives 62.5% of total drop)
        # Europe: 500 -> 480 (-20, -4.0%, drives 25.0% of total drop)
        # North: 300 -> 290 (-10, -3.33%, drives 12.5% of total drop)
        qr = QueryResult(
            query_id="Q1",
            sql="SELECT Region, aug_rev, sep_rev FROM sales_summary;",
            columns=["Region", "aug_rev", "sep_rev"],
            rows=[
                ("Europe", 500.0, 480.0),
                ("South", 200.0, 150.0),
                ("North", 300.0, 290.0),
            ],
            row_count=3
        )

        analysis = analyze_contribution(qr)

        # Totals
        self.assertEqual(analysis.total_prior, 1000.0)
        self.assertEqual(analysis.total_current, 920.0)
        self.assertEqual(analysis.total_change, -80.0)
        self.assertEqual(analysis.total_pct_change, -8.0)

        # Reconciliation check
        self.assertTrue(analysis.is_reconciled)
        self.assertAlmostEqual(analysis.reconciliation_error, 0.0, places=4)

        # Ranking: South must be #1 because it drove the biggest share of the drop (-50 out of -80)
        self.assertEqual(len(analysis.items), 3)
        top = analysis.top_driver
        self.assertIsNotNone(top)
        self.assertEqual(top.dimension_value, "South")
        self.assertEqual(top.rank, 1)
        self.assertEqual(top.absolute_change, -50.0)
        self.assertEqual(top.pct_change, -25.0)
        self.assertEqual(top.contribution_pct, 62.5)

        # Second driver is Europe
        self.assertEqual(analysis.items[1].dimension_value, "Europe")
        self.assertEqual(analysis.items[1].contribution_pct, 25.0)

        # Third driver is North
        self.assertEqual(analysis.items[2].dimension_value, "North")
        self.assertEqual(analysis.items[2].contribution_pct, 12.5)

    def test_reconciliation_fail(self):
        """Case 2: Hand-made table where the sum does NOT match the expected total change."""
        # Table rows sum to: (-30) + (-20) = -50
        # But expected total change is -90 (e.g. due to missing records or fan-out mismatch)
        qr = QueryResult(
            query_id="Q2",
            sql="SELECT Category, prior_val, current_val FROM broken_table;",
            columns=["Category", "prior_val", "current_val"],
            rows=[
                ("Beverages", 100.0, 70.0),   # -30
                ("Condiments", 80.0, 60.0),    # -20
            ],
            row_count=2
        )

        # Pass expected_total_change=-90.0
        analysis = analyze_contribution(qr, expected_total_change=-90.0, tolerance=0.01)

        self.assertFalse(analysis.is_reconciled)
        self.assertEqual(analysis.total_change, -90.0)
        # Sum of items is -50. Error should be |-50 - (-90)| = 40.0
        self.assertAlmostEqual(analysis.reconciliation_error, 40.0, places=2)

    def test_synthetic_total_row_reconciliation_fail(self):
        """Case 2b: A query returns an explicit 'Total' row that doesn't match individual rows."""
        qr = QueryResult(
            query_id="Q2b",
            sql="SELECT Region, prior, curr FROM rollups;",
            columns=["Region", "prior", "curr"],
            rows=[
                ("North", 100.0, 90.0),        # -10
                ("South", 100.0, 80.0),        # -20
                ("Total", 200.0, 150.0),       # Claims total dropped by -50, but rows only drop by -30!
            ],
            row_count=3
        )

        analysis = analyze_contribution(qr)
        # Total change extracted from Total row: 150 - 200 = -50
        self.assertEqual(analysis.total_change, -50.0)
        # Sum of rows is -30. Error: |-30 - (-50)| = 20.0
        self.assertFalse(analysis.is_reconciled)
        self.assertAlmostEqual(analysis.reconciliation_error, 20.0, places=2)

    def test_mixed_direction_changes(self):
        """Case 3: Some dimensions grow while others decline."""
        # Total change: -40
        # Category A: -70 (drives 175% of net decline)
        # Category B: +30 (partially offsets decline)
        qr = QueryResult(
            query_id="Q3",
            sql="SELECT Category, prior, curr FROM data;",
            columns=["Category", "prior", "curr"],
            rows=[
                ("Category B", 50.0, 80.0),   # +30
                ("Category A", 100.0, 30.0),  # -70
            ],
            row_count=2
        )

        analysis = analyze_contribution(qr)
        self.assertTrue(analysis.is_reconciled)
        self.assertEqual(analysis.total_change, -40.0)

        # Category A should be ranked #1 because it drove the drop
        top = analysis.top_driver
        self.assertEqual(top.dimension_value, "Category A")
        self.assertEqual(top.absolute_change, -70.0)
        self.assertEqual(top.contribution_pct, 175.0)

    def test_long_format_table(self):
        """Case 4: Long format table with [Region, Month, Revenue]."""
        qr = QueryResult(
            query_id="Q4",
            sql="SELECT Region, Month, Revenue FROM monthly_sales;",
            columns=["Region", "Month", "Revenue"],
            rows=[
                ("North", "2023-08", 300.0),
                ("North", "2023-09", 250.0),  # -50
                ("South", "2023-08", 200.0),
                ("South", "2023-09", 100.0),  # -100
            ],
            row_count=4
        )

        analysis = analyze_contribution(qr)
        self.assertTrue(analysis.is_reconciled)
        self.assertEqual(analysis.total_prior, 500.0)
        self.assertEqual(analysis.total_current, 350.0)
        self.assertEqual(analysis.total_change, -150.0)

        # South dropped by -100 / -150 = 66.67%
        self.assertEqual(analysis.top_driver.dimension_value, "South")
        self.assertEqual(analysis.top_driver.absolute_change, -100.0)

    def test_compare_two_separate_results(self):
        """Case 5: Compare two distinct QueryResults."""
        q1 = QueryResult(
            query_id="Q1",
            sql="SELECT Region, Revenue FROM aug;",
            columns=["Region", "Revenue"],
            rows=[("Europe", 100.0), ("South", 50.0)],
            row_count=2
        )
        q2 = QueryResult(
            query_id="Q2",
            sql="SELECT Region, Revenue FROM sep;",
            columns=["Region", "Revenue"],
            rows=[("Europe", 80.0), ("South", 40.0)],
            row_count=2
        )

        analysis = compare_two_results(q1, q2, dimension_col="Region", metric_col="Revenue")
        self.assertTrue(analysis.is_reconciled)
        self.assertEqual(analysis.total_change, -30.0)
        self.assertEqual(analysis.top_driver.dimension_value, "Europe")
        self.assertEqual(analysis.top_driver.absolute_change, -20.0)

    def test_edge_cases_zero_division(self):
        """Case 6: Prior is 0, total change is 0, or empty results."""
        # Empty result
        empty = QueryResult(query_id="Q0", sql="", columns=[], rows=[])
        an_empty = analyze_contribution(empty)
        self.assertTrue(an_empty.is_reconciled)
        self.assertEqual(len(an_empty.items), 0)

        # Prior is 0 (new product introduced)
        new_prod = QueryResult(
            query_id="Q_new",
            sql="",
            columns=["Product", "prior", "curr"],
            rows=[("New Item", 0.0, 50.0)],
            row_count=1
        )
        an_new = analyze_contribution(new_prod)
        self.assertIsNone(an_new.items[0].pct_change) # Safe None, no ZeroDivisionError
        self.assertEqual(an_new.items[0].contribution_pct, 100.0)

        # Total change is 0
        flat = QueryResult(
            query_id="Q_flat",
            sql="",
            columns=["Region", "prior", "curr"],
            rows=[("North", 100.0, 100.0)],
            row_count=1
        )
        an_flat = analyze_contribution(flat)
        self.assertEqual(an_flat.total_change, 0.0)
        self.assertEqual(an_flat.items[0].contribution_pct, 0.0)

    def test_driver_finding_builder(self):
        """Case 7: Build traceable Finding from contribution analysis."""
        qr = QueryResult(
            query_id="Q1",
            sql="",
            columns=["Region", "aug", "sep"],
            rows=[("South", 200.0, 150.0)],
            row_count=1
        )
        analysis = analyze_contribution(qr)
        finding = build_driver_finding(analysis, query_id="Q1", metric_name="revenue")

        self.assertIsNotNone(finding)
        self.assertEqual(finding.kind, "driver")
        self.assertEqual(finding.query_id, "Q1")
        self.assertEqual(finding.values["abs_change"], 50.0)
        self.assertEqual(finding.values["direction"], "fell")
        rendered = finding.render()
        self.assertIn("South revenue fell by 50.0 (-25.0%)", rendered)
        self.assertIn("[Q1]", rendered)


if __name__ == "__main__":
    unittest.main()
