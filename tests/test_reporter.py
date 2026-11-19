"""tests/test_reporter.py
Unit tests for reporter.py (Person B - Business Reporting & Visualization Engine).
Tests report generation against sample_agent_run.json and edge cases.
"""

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from contracts import (
    AgentRun,
    Finding,
    Plan,
    PlanQuery,
    QueryResult,
    ValidationResult,
    TraceEvent
)
import reporter

SAMPLE_RUN_PATH = Path(__file__).resolve().parent.parent / "sample_agent_run.json"


class TestReporter(unittest.TestCase):
    """Test suite for reporter.py functionality and zero-hallucination report integrity."""

    def setUp(self):
        self.assertTrue(SAMPLE_RUN_PATH.exists(), f"Sample run missing at {SAMPLE_RUN_PATH}")
        with open(SAMPLE_RUN_PATH, "r", encoding="utf-8") as f:
            self.sample_data = json.load(f)
        self.sample_run = AgentRun.from_dict(self.sample_data)

    def test_report_generation_from_sample_agent_run(self):
        """Prompt Phase 3: Build report from sample_agent_run.json and verify all 5 PRD sections."""
        result = reporter.build_report(self.sample_run)
        md = result.markdown

        # Check main header and question
        self.assertIn("# InsightAnalyst Business Analysis Report", md)
        self.assertIn("Why did sales drop in the South region last month?", md)
        self.assertIn("`revenue`", md)
        self.assertIn("`Complete`", md)

        # Check 5 PRD Section Headers
        self.assertIn("## 1. Headline Answer", md)
        self.assertIn("## 2. Key Drivers", md)
        self.assertIn("## 3. Interactive Visualizations", md)
        self.assertIn("## 4. Assumptions & Business Rules", md)
        self.assertIn("## 5. Evidence Appendix", md)

        # Check Headline content with [Q1] citation
        self.assertIn("Overall revenue rose by 980.56 (0.14%) [Q1]", md)

        # Check Key Drivers with citations
        self.assertIn("South revenue rose by 980.56 (0.14%), driving 100.0% of total change [Q1]", md)
        self.assertIn("[Q2]", md)

        # Check Evidence Appendix contains executed SQL and validation checks
        self.assertIn("### Query [Q1]", md)
        self.assertIn("### Query [Q2]", md)
        self.assertIn("SELECT dcr.Region", md)
        self.assertIn("Geitost", md)
        self.assertIn("✅ **execution**", md)

    def test_zero_hallucination_traceability_audit(self):
        """PRD Section 8: Every stated number in Headline and Drivers must have a query citation."""
        result = reporter.build_report(self.sample_run)
        is_valid, violations = reporter.verify_report_traceability(result.markdown)
        self.assertTrue(is_valid, f"Traceability audit failed with violations: {violations}")
        self.assertEqual(len(violations), 0)

    def test_traceability_checker_catches_untraced_number(self):
        """Traceability audit must fail when a number is stated without [Q#] reference."""
        bad_markdown = (
            "# InsightAnalyst Business Analysis Report\n\n"
            "## 1. Headline Answer\n\n"
            "Total revenue reached 452000 across all markets.\n\n"
            "## 2. Key Drivers\n\n"
            "- Sales fell by 14.5% due to supply disruptions.\n\n"
            "## 3. Interactive Visualizations\n"
        )
        is_valid, violations = reporter.verify_report_traceability(bad_markdown)
        self.assertFalse(is_valid)
        self.assertGreaterEqual(len(violations), 1)

    def test_trend_chart_generation(self):
        """Verifies trend chart is built with time periods and values from Q1."""
        trend_chart = reporter.generate_trend_chart(self.sample_run)
        self.assertIsNotNone(trend_chart)

        if isinstance(trend_chart, reporter.ChartSpec):
            self.assertEqual(trend_chart.chart_type, "trend")
            self.assertIn("Trend", trend_chart.title)
            self.assertEqual(len(trend_chart.data), 2)
            periods = [d["period"] for d in trend_chart.data]
            self.assertIn("2023-08", periods)
            self.assertIn("2023-09", periods)
        else:
            # Plotly Figure
            self.assertTrue(hasattr(trend_chart, "data"))
            self.assertGreaterEqual(len(trend_chart.data), 1)

    def test_driver_chart_generation(self):
        """Verifies driver breakdown chart is built with product entities and values from Q2."""
        driver_chart = reporter.generate_driver_chart(self.sample_run)
        self.assertIsNotNone(driver_chart)

        if isinstance(driver_chart, reporter.ChartSpec):
            self.assertEqual(driver_chart.chart_type, "driver")
            self.assertIn("Driver Breakdown", driver_chart.title)
            self.assertEqual(len(driver_chart.data), 5)
            entities = [d["entity"] for d in driver_chart.data]
            self.assertIn("Geitost", entities)
        else:
            # Plotly Figure
            self.assertTrue(hasattr(driver_chart, "data"))
            self.assertGreaterEqual(len(driver_chart.data), 1)

    def test_report_with_refusal_status(self):
        """Verifies clear guardrail refusal message when destructive query is blocked."""
        refusal_plan = Plan(
            metric="revenue",
            time_window="all",
            question="Drop table Orders",
            is_refusal=True,
            refusal_reason="Destructive DDL and DML operations are strictly blocked by read-only guardrails."
        )
        run = AgentRun(
            question="Drop table Orders",
            plan=refusal_plan,
            status="refused",
            error=None
        )
        md = reporter.generate_markdown_report(run)
        self.assertIn("`Request Refused (Guardrail)`", md)
        self.assertIn("Destructive DDL and DML operations are strictly blocked", md)
        self.assertIn("*(No SQL queries were executed for this request)*", md)

    def test_report_with_clarification_status(self):
        """Verifies clarification question is clearly presented when query is vague."""
        clarify_plan = Plan(
            metric="revenue",
            time_window="unspecified",
            question="How are we doing?",
            needs_clarification=True,
            clarification_message="Could you specify whether you mean revenue, order volume, or average order value, and for which time period?"
        )
        run = AgentRun(
            question="How are we doing?",
            plan=clarify_plan,
            status="clarify"
        )
        md = reporter.generate_markdown_report(run)
        self.assertIn("`Clarification Required`", md)
        self.assertIn("Could you specify whether you mean revenue", md)

    def test_report_with_failed_status(self):
        """Verifies honest failure messaging when query execution fails."""
        failed_query = QueryResult(
            query_id="Q1",
            sql="SELECT * FROM UnknownTable;",
            error="no such table: UnknownTable",
            row_count=0
        )
        failed_val = ValidationResult(
            status="fail",
            reason="SQL execution failed: no such table: UnknownTable",
            check_name="execution"
        )
        run = AgentRun(
            question="What is the revenue?",
            queries=[failed_query],
            validations=[failed_val],
            status="failed",
            error="All queries failed after maximum retries"
        )
        md = reporter.generate_markdown_report(run)
        self.assertIn("`Execution Failed`", md)
        self.assertIn("All queries failed after maximum retries", md)
        self.assertIn("no such table: UnknownTable", md)

    def test_export_markdown(self):
        """Verifies report can be written directly to a markdown file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "test_report.md"
            saved_path = reporter.export_markdown(self.sample_run, filepath=out_file)
            self.assertTrue(saved_path.exists())
            content = saved_path.read_text(encoding="utf-8")
            self.assertIn("# InsightAnalyst Business Analysis Report", content)
            self.assertIn("## 1. Headline Answer", content)


if __name__ == "__main__":
    unittest.main()
