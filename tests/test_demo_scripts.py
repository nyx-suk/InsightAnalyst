"""tests/test_demo_scripts.py
Unit tests for scripts/warm_cache.py and scripts/check_demo.py (Prompt 4.2).
"""

import os
from pathlib import Path
import tempfile
import unittest

from contracts import AgentRun, Finding, Plan, QueryResult
from scripts.check_demo import (
    load_demo_questions,
    verify_numbers_have_query_ids,
    verify_metric_relevance,
    check_demo
)
from scripts.warm_cache import warm_cache_for_questions


class TestDemoScripts(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.q_file = Path(self.test_dir.name) / "test_questions.txt"
        with open(self.q_file, "w", encoding="utf-8") as f:
            f.write("# Sample comment\n")
            f.write("Why did sales drop in the South region last month?\n\n")
            f.write("What was our total units sold in September 2023?\n")

    def tearDown(self):
        self.test_dir.cleanup()

    def test_load_demo_questions(self):
        """Verify comments and blank lines are ignored when loading questions."""
        questions = load_demo_questions(self.q_file)
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0], "Why did sales drop in the South region last month?")
        self.assertEqual(questions[1], "What was our total units sold in September 2023?")

    def test_verify_numbers_have_query_ids_valid(self):
        """Valid findings with [Q1] and [Q2] references must pass audit."""
        run = AgentRun(
            question="Why did sales drop?",
            queries=[
                QueryResult(query_id="Q1", sql="SELECT 1", columns=["val"], rows=[(100,)], row_count=1),
                QueryResult(query_id="Q2", sql="SELECT 2", columns=["val"], rows=[(50,)], row_count=1)
            ],
            findings=[
                Finding(
                    id="F1",
                    kind="headline",
                    template="Revenue fell by {val} [{query_id}]",
                    values={"val": 500, "query_id": "Q1"},
                    query_id="Q1",
                    statement="Revenue fell by 500 [Q1]"
                ),
                Finding(
                    id="F2",
                    kind="driver",
                    template="South was 250 [{query_id}]",
                    values={"val": 250, "query_id": "Q2"},
                    query_id="Q2",
                    statement="South was 250 [Q2]"
                )
            ],
            assumptions=["Narrative Summary: Overall revenue dropped by 500 [Q1] driven by South [Q2]."],
            status="complete"
        )

        ok, msg = verify_numbers_have_query_ids(run)
        self.assertTrue(ok)
        self.assertEqual(msg, "YES")

    def test_verify_numbers_have_query_ids_missing_query_id(self):
        """Findings with empty query_id must fail audit."""
        run = AgentRun(
            question="Why did sales drop?",
            queries=[QueryResult(query_id="Q1", sql="SELECT 1", columns=["val"], rows=[(100,)], row_count=1)],
            findings=[
                Finding(
                    id="F1",
                    kind="headline",
                    template="Revenue fell by 500",
                    values={"val": 500},
                    query_id="",  # Missing query ID
                    statement="Revenue fell by 500"
                )
            ],
            status="complete"
        )

        ok, msg = verify_numbers_have_query_ids(run)
        self.assertFalse(ok)
        self.assertIn("missing query_id", msg)

    def test_verify_numbers_have_query_ids_untraced_number(self):
        """Finding statements containing numbers but missing [Q...] must fail audit."""
        run = AgentRun(
            question="Why did sales drop?",
            queries=[QueryResult(query_id="Q1", sql="SELECT 1", columns=["val"], rows=[(100,)], row_count=1)],
            findings=[
                Finding(
                    id="F1",
                    kind="headline",
                    template="Revenue fell by 500",
                    values={"val": 500, "query_id": "Q1"},
                    query_id="Q1",
                    statement="Revenue fell by 500 without citation"
                )
            ],
            status="complete"
        )

        ok, msg = verify_numbers_have_query_ids(run)
        self.assertFalse(ok)
        self.assertIn("lacks [Q1] citation", msg)

    def test_verify_numbers_empty_findings(self):
        """Empty findings in completed run must fail audit."""
        run = AgentRun(
            question="Why did sales drop?",
            queries=[],
            findings=[],
            status="complete"
        )

        ok, msg = verify_numbers_have_query_ids(run)
        self.assertFalse(ok)
        self.assertIn("No findings generated", msg)

    def test_verify_metric_relevance_valid(self):
        """Questions matching plan metric and finding text must pass semantic audit."""
        run = AgentRun(
            question="Why did sales drop in the South region last month?",
            plan=Plan(metric="revenue", time_window="last_complete_month"),
            findings=[
                Finding(
                    id="F1",
                    kind="headline",
                    template="Overall revenue fell by 52850.03 (-7.69%) [Q1]",
                    values={"val": 52850.03},
                    query_id="Q1",
                    statement="Overall revenue fell by 52850.03 (-7.69%) [Q1]"
                )
            ],
            status="complete"
        )
        ok, msg = verify_metric_relevance(run.question, run)
        self.assertTrue(ok)
        self.assertEqual(msg, "YES")

    def test_verify_metric_relevance_mismatch(self):
        """Questions with mismatched plan metric (e.g. sales vs country_count) must fail semantic audit."""
        run = AgentRun(
            question="Why did sales drop in the South region last month?",
            plan=Plan(metric="country_count", time_window="current"),
            findings=[
                Finding(
                    id="F1",
                    kind="headline",
                    template="Overall country_count fell by 21.0 [Q1]",
                    values={"val": 21.0},
                    query_id="Q1",
                    statement="Overall country_count fell by 21.0 [Q1]"
                )
            ],
            status="complete"
        )
        ok, msg = verify_metric_relevance(run.question, run)
        self.assertFalse(ok)
        self.assertIn("Semantic metric mismatch", msg)
        self.assertIn("country_count", msg)

    def test_warm_cache_and_check_demo_flow(self):
        """End-to-end test of warm_cache -> check_demo in readonly cache mode."""
        questions = [
            "Why did sales drop in the South region last month?",
            "What was our total units sold in September 2023?"
        ]

        # 1. Warm cache
        warm_ok = warm_cache_for_questions(questions, provider="fake")
        self.assertTrue(warm_ok)

        # 2. Check demo offline in readonly mode
        check_ok = check_demo(self.q_file)
        self.assertTrue(check_ok)


if __name__ == "__main__":
    unittest.main()
