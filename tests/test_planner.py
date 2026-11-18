"""tests/test_planner.py
Unit tests for agent/planner.py verifying contract validation, prompt template loading,
and plan construction using fake LLM responses.
"""

import os
from pathlib import Path
import sys
import unittest

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import Plan
from agent.planner import plan, validate_plan_dict, _load_prompt_template
import agent.llm as default_llm
from agent.llm import register_fake_response, clear_fake_responses


class MockLLM:
    """Mock LLM callable for simulating structured planner responses."""

    def __init__(self, response_dict):
        self.response_dict = response_dict
        self.last_system = None
        self.last_prompt = None

    def generate_json(self, prompt: str, system: str = None) -> dict:
        self.last_prompt = prompt
        self.last_system = system
        return self.response_dict


class TestPlanner(unittest.TestCase):
    """Test suite for agent/planner.py."""

    def test_prompt_template_loads(self):
        """Verifies that prompts/planner.txt loads and contains expected rules."""
        template = _load_prompt_template()
        self.assertIn("RULES:", template)
        self.assertIn("SQLite", template)
        self.assertIn("dim_country_region", template)

    def test_plan_valid_response(self):
        """Verifies successful plan construction with all contract fields populated."""
        mock_response = {
            "question": "Why did sales drop in the South region last month?",
            "metric": "revenue",
            "time_window": {"type": "relative", "value": "last_complete_month"},
            "comparison_window": {"type": "relative", "value": "previous_month_before_last"},
            "filters": {"region": "South"},
            "drill_down_dimensions": ["product", "time"],
            "queries": [
                {
                    "query_id": "Q1",
                    "purpose": "Compare South region revenue between target and baseline month",
                    "sql": "SELECT dcr.Region, SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) AS Revenue FROM [Order Details] od JOIN Orders o ON od.OrderID = o.OrderID JOIN Customers c ON o.CustomerID = c.CustomerID JOIN dim_country_region dcr ON c.Country = dcr.Country WHERE dcr.Region = 'South' GROUP BY dcr.Region;"
                }
            ],
            "assumptions": ["Anchored to September 2023"],
            "needs_clarification": False,
            "clarification_question": None,
            "refused": False,
            "refusal_reason": None
        }

        mock_llm = MockLLM(mock_response)
        fake_chunks = [
            {"id": "metric_revenue", "type": "metric", "text": "Formula: SUM(UnitPrice * Quantity * (1 - Discount))"}
        ]

        p = plan("Why did sales drop in the South region last month?", fake_chunks, mock_llm)
        self.assertIsInstance(p, Plan)
        self.assertEqual(p.metric, "revenue")
        self.assertEqual(len(p.query_list), 1)
        self.assertEqual(p.query_list[0].query_id, "Q1")
        self.assertEqual(p.assumptions, ["Anchored to September 2023"])

    def test_validate_plan_missing_metric_raises_error(self):
        """Verifies that missing 'metric' raises a clear validation error."""
        invalid_data = {
            "time_window": "last_complete_month",
            "queries": [{"query_id": "Q1", "sql": "SELECT 1;", "purpose": "test"}]
        }
        with self.assertRaises(ValueError) as ctx:
            validate_plan_dict(invalid_data)
        self.assertIn("missing required field 'metric'", str(ctx.exception))

    def test_validate_plan_missing_time_window_raises_error(self):
        """Verifies that missing 'time_window' raises a clear validation error."""
        invalid_data = {
            "metric": "revenue",
            "queries": [{"query_id": "Q1", "sql": "SELECT 1;", "purpose": "test"}]
        }
        with self.assertRaises(ValueError) as ctx:
            validate_plan_dict(invalid_data)
        self.assertIn("missing required field 'time_window'", str(ctx.exception))

    def test_validate_plan_missing_sql_raises_error(self):
        """Verifies that a query missing 'sql' raises a validation error."""
        invalid_data = {
            "metric": "revenue",
            "time_window": "last_complete_month",
            "queries": [{"query_id": "Q1", "purpose": "test without sql"}]
        }
        with self.assertRaises(ValueError) as ctx:
            validate_plan_dict(invalid_data)
        self.assertIn("missing 'sql' statement", str(ctx.exception))

    def test_prompt_2_4_vague_question_clarification_with_fake_llm(self):
        """Prompt 2.4: Verifies vague questions set needs_clarification=True and clarification_question."""
        os.environ["LLM_PROVIDER"] = "fake"
        clear_fake_responses()
        canned_clarification = (
            "{\n"
            '  "question": "How are we doing?",\n'
            '  "metric": "revenue",\n'
            '  "time_window": {"type": "relative", "value": "last_complete_month"},\n'
            '  "queries": [],\n'
            '  "needs_clarification": true,\n'
            '  "clarification_question": "Which metric and timeframe would you like to evaluate?",\n'
            '  "refused": false\n'
            "}"
        )
        register_fake_response("How are we doing?", canned_clarification)

        p = plan("How are we doing?", [], default_llm)
        self.assertTrue(p.needs_clarification)
        self.assertEqual(p.clarification_message, "Which metric and timeframe would you like to evaluate?")
        self.assertFalse(p.is_refusal)
        clear_fake_responses()

    def test_prompt_2_4_write_request_refusal_with_fake_llm(self):
        """Prompt 2.4: Verifies destructive write requests set refused=True, refusal_reason, and produce no SQL."""
        os.environ["LLM_PROVIDER"] = "fake"
        clear_fake_responses()
        canned_refusal = (
            "{\n"
            '  "question": "Delete all orders from 1997",\n'
            '  "metric": "none",\n'
            '  "time_window": "none",\n'
            '  "queries": [],\n'
            '  "needs_clarification": false,\n'
            '  "refused": true,\n'
            '  "refusal_reason": "InsightAnalyst is strictly a read-only agent. Write and delete operations are forbidden."\n'
            "}"
        )
        register_fake_response("Delete all orders from 1997", canned_refusal)

        p = plan("Delete all orders from 1997", [], default_llm)
        self.assertTrue(p.is_refusal)
        self.assertEqual(len(p.query_list), 0)
        self.assertIn("read-only agent", p.refusal_reason)
        clear_fake_responses()

    def test_prompt_2_4_unrelated_question_refusal_with_fake_llm(self):
        """Prompt 2.4: Verifies unrelated questions set refused=True with polite reason and produce no SQL."""
        os.environ["LLM_PROVIDER"] = "fake"
        clear_fake_responses()
        canned_unrelated = (
            "{\n"
            '  "question": "What is the capital of France?",\n'
            '  "metric": "none",\n'
            '  "time_window": "none",\n'
            '  "queries": [],\n'
            '  "needs_clarification": false,\n'
            '  "refused": true,\n'
            '  "refusal_reason": "InsightAnalyst is specialized in the Northwind business sales database and cannot answer general trivia questions."\n'
            "}"
        )
        register_fake_response("What is the capital of France?", canned_unrelated)

        p = plan("What is the capital of France?", [], default_llm)
        self.assertTrue(p.is_refusal)
        self.assertEqual(len(p.query_list), 0)
        self.assertIn("cannot answer general trivia", p.refusal_reason)
        clear_fake_responses()

    def test_prompt_2_4_refusal_with_queries_violates_contract(self):
        """Prompt 2.4: Verifies that a plan marked as refused must not produce any SQL queries."""
        invalid_refusal = {
            "refused": True,
            "refusal_reason": "Refused but generated queries",
            "queries": [{"query_id": "Q1", "sql": "DELETE FROM Orders;"}]
        }
        with self.assertRaises(ValueError) as ctx:
            validate_plan_dict(invalid_refusal)
        self.assertIn("must not produce any SQL queries", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
