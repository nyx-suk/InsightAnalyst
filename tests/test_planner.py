"""tests/test_planner.py
Unit tests for agent/planner.py verifying contract validation, prompt template loading,
and plan construction using fake LLM responses.
"""

from pathlib import Path
import sys
import unittest

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import Plan
from agent.planner import plan, validate_plan_dict, _load_prompt_template


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


if __name__ == "__main__":
    unittest.main()
