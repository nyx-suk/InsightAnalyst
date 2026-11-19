"""tests/test_golden_set.py
Unit tests verifying the integrity of the Golden Set (tests/golden_set.json)
and the evaluation runner (scripts/evaluate_golden_set.py).

Checks:
1. Exactly 15 to 20 benchmark questions exist.
2. Hand-written reference SQL executes on SQLite Northwind without errors.
3. Reference SQL produces the expected numbers without discrepancies.
4. All 4 mandatory trap questions (relative date, fan-out join, ambiguous query, write refusal) exist.
"""

import json
from pathlib import Path
import sqlite3
import unittest

from scripts.evaluate_golden_set import (
    execute_reference_sql,
    is_number_within_tolerance,
    evaluate_question
)

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "golden_set.json"
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "northwind.db"


class TestGoldenSet(unittest.TestCase):
    """Test suite verifying ground truth and validity of golden questions."""

    def setUp(self):
        self.assertTrue(GOLDEN_SET_PATH.exists(), f"Golden set missing at {GOLDEN_SET_PATH}")
        with open(GOLDEN_SET_PATH, "r", encoding="utf-8") as f:
            self.golden_set = json.load(f)

    def test_golden_set_size_and_schema(self):
        """PRD Section 10: Golden set must contain between 15 and 20 questions."""
        count = len(self.golden_set)
        self.assertGreaterEqual(count, 15, "Golden set must contain at least 15 questions.")
        self.assertLessEqual(count, 20, "Golden set should not exceed 20 questions.")

        required_keys = {"id", "category", "question", "metric", "description", "expected_output"}
        for item in self.golden_set:
            self.assertTrue(required_keys.issubset(item.keys()), f"Missing keys in {item.get('id')}")

    def test_all_trap_categories_represented(self):
        """PRD Section 10: Must include relative dates, join fan-out, ambiguous metric, and write refusal traps."""
        categories = set(item["category"] for item in self.golden_set)
        self.assertIn("trap_relative_date", categories, "Missing relative date window trap")
        self.assertIn("trap_fan_out", categories, "Missing join fan-out trap")
        self.assertIn("trap_clarification", categories, "Missing ambiguous clarification trap")
        self.assertIn("trap_write_refusal", categories, "Missing write attempt refusal trap")

    def test_reference_sql_executions_and_ground_truth(self):
        """Verifies all reference SQL queries execute on SQLite and match expected ground-truth numbers."""
        for item in self.golden_set:
            sql = item.get("reference_sql")
            qid = item["id"]
            if not sql:
                continue  # Clarification and refusal traps have no SQL

            rows = execute_reference_sql(sql)
            self.assertGreater(len(rows), 0, f"Reference SQL for {qid} returned 0 rows.")

            expected = item.get("expected_output", {})
            if "value" in expected:
                exp_val = expected["value"]
                # First cell of first row
                act_val = rows[0][0]
                if isinstance(act_val, str) and len(rows[0]) > 1:
                    act_val = rows[0][1]
                self.assertTrue(
                    is_number_within_tolerance(float(act_val), float(exp_val), tolerance=0.01),
                    f"{qid}: Ground truth mismatch. Got {act_val}, expected {exp_val}"
                )
            elif "current_value" in expected:
                # Variance query, check second row (current month)
                self.assertGreaterEqual(len(rows), 2, f"{qid}: Expected 2 comparison periods")
                act_current = rows[1][1]
                exp_current = expected["current_value"]
                self.assertTrue(
                    is_number_within_tolerance(float(act_current), float(exp_current), tolerance=0.01),
                    f"{qid}: Current month mismatch. Got {act_current}, expected {exp_current}"
                )

    def test_tolerance_helper(self):
        """Verifies numeric tolerance checker behaves correctly for exact, tiny, and percentage variances."""
        self.assertTrue(is_number_within_tolerance(100.0, 100.0, 0.05))
        self.assertTrue(is_number_within_tolerance(102.0, 100.0, 0.05))
        self.assertTrue(is_number_within_tolerance(100.4, 100.0, 0.01))
        self.assertFalse(is_number_within_tolerance(120.0, 100.0, 0.05))

    def test_evaluate_question_guardrail_refusal(self):
        """Verifies evaluator correctly checks and passes a write refusal trap."""
        trap_item = next(item for item in self.golden_set if item["category"] == "trap_write_refusal")
        eval_result = evaluate_question(trap_item, mode="offline")
        self.assertTrue(eval_result["status_passed"])
        self.assertTrue(eval_result["numeric_passed"])
        self.assertTrue(eval_result["overall_pass"])


if __name__ == "__main__":
    unittest.main()
