"""tests/test_person_a.py
Comprehensive unit and integration test suite for Person A deliverables.
Verifies contracts, retrieval, LLM caching & JSON parsing, planner safeguards, and the agent loop.
"""

import os
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import Plan, PlanQuery, QueryResult, ValidationResult, Finding, AgentResponse, TraceEvent, AgentRun
from knowledge.retriever import retrieve
from agent.llm import parse_json_safely, clean_json_string, _get_cache_key
from agent.planner import _check_quick_refusal, create_plan
from agent.loop import run_agent_loop
from tests.mock_executor import mock_executor, mock_validator


class TestContracts(unittest.TestCase):
    """Verifies that shared contracts serialize and deserialize cleanly."""

    def test_plan_serialization(self):
        pq = PlanQuery(query_id="Q1", sql="SELECT 1;", purpose="Test baseline")
        plan = Plan(
            metric="revenue",
            time_window="1998-04",
            comparison_period="1998-03",
            filters={"region": "South"},
            drill_down_dimensions=["product"],
            query_list=[pq]
        )
        data = plan.to_dict()
        restored = Plan.from_dict(data)
        self.assertEqual(restored.metric, "revenue")
        self.assertEqual(restored.time_window, "1998-04")
        self.assertEqual(len(restored.query_list), 1)
        self.assertEqual(restored.query_list[0].query_id, "Q1")

    def test_query_result_serialization(self):
        qr = QueryResult(
            query_id="Q1",
            sql="SELECT 100 AS val;",
            columns=["val"],
            rows=[(100,)],
            row_count=1
        )
        data = qr.to_dict()
        restored = QueryResult.from_dict(data)
        self.assertEqual(restored.query_id, "Q1")
        self.assertEqual(restored.rows, [(100,)])
        self.assertIsNone(restored.error)

    def test_validation_result_serialization(self):
        vr = ValidationResult(status="pass", reason=None, check_name="empty_check")
        data = vr.to_dict()
        restored = ValidationResult.from_dict(data)
        self.assertEqual(restored.status, "pass")
        self.assertEqual(restored.check_name, "empty_check")

    def test_finding_and_template_rendering(self):
        """Prompt 3.1: Numbers appear only in Finding.values and are filled into template by code."""
        finding = Finding(
            id="F1",
            kind="driver",
            template="South revenue fell {pct_change}% [{query_id}]",
            values={"pct_change": -18.2},
            query_id="Q1"
        )
        self.assertEqual(finding.id, "F1")
        self.assertEqual(finding.kind, "driver")
        self.assertEqual(finding.values["pct_change"], -18.2)
        # Verify code renders the template
        rendered = finding.render()
        self.assertEqual(rendered, "South revenue fell -18.2% [Q1]")
        self.assertEqual(finding.statement, "South revenue fell -18.2% [Q1]")

        # Test serialization round-trip
        data = finding.to_dict()
        restored = Finding.from_dict(data)
        self.assertEqual(restored.id, "F1")
        self.assertEqual(restored.kind, "driver")
        self.assertEqual(restored.values, {"pct_change": -18.2})
        self.assertEqual(restored.query_id, "Q1")
        self.assertEqual(restored.render(), "South revenue fell -18.2% [Q1]")

    def test_trace_event_serialization(self):
        """Prompt 3.1: TraceEvent step, event, message, query_id."""
        event = TraceEvent(
            step=1,
            event="query",
            message="Executing baseline revenue query",
            query_id="Q1"
        )
        data = event.to_dict()
        restored = TraceEvent.from_dict(data)
        self.assertEqual(restored.step, 1)
        self.assertEqual(restored.event, "query")
        self.assertEqual(restored.message, "Executing baseline revenue query")
        self.assertEqual(restored.query_id, "Q1")

    def test_agent_run_serialization(self):
        """Prompt 3.1: AgentRun complete execution record."""
        pq = PlanQuery(query_id="Q1", sql="SELECT 1;", purpose="Test")
        plan = Plan(metric="revenue", time_window="2023-09", query_list=[pq])
        qr = QueryResult(query_id="Q1", sql="SELECT 1;", columns=["col"], rows=[(1,)], row_count=1)
        vr = ValidationResult(status="pass", check_name="execution")
        finding = Finding(id="F1", kind="headline", template="Revenue is {total} [{query_id}]", values={"total": 634607.27}, query_id="Q1")
        trace = TraceEvent(step=1, event="plan", message="Plan created")

        run = AgentRun(
            question="What was revenue in September 2023?",
            plan=plan,
            queries=[qr],
            validations=[vr],
            findings=[finding],
            trace=[trace],
            assumptions=["September 2023 is last complete month"],
            status="complete"
        )

        data = run.to_dict()
        restored = AgentRun.from_dict(data)
        self.assertEqual(restored.question, "What was revenue in September 2023?")
        self.assertEqual(restored.status, "complete")
        self.assertEqual(len(restored.queries), 1)
        self.assertEqual(len(restored.validations), 1)
        self.assertEqual(len(restored.findings), 1)
        self.assertEqual(restored.findings[0].render(), "Revenue is 634607.27 [Q1]")
        self.assertEqual(len(restored.trace), 1)
        self.assertEqual(restored.trace[0].event, "plan")
        self.assertEqual(restored.assumptions, ["September 2023 is last complete month"])


class TestKnowledgeLayer(unittest.TestCase):
    """Verifies knowledge retrieval returns grounded chunks."""

    def test_retrieval_returns_relevant_context(self):
        docs = retrieve("Why did sales drop in South region last month?", k=3)
        self.assertGreater(len(docs), 0)
        combined_text = " ".join([d["document"] for d in docs]).lower()
        # Must contain grounded concepts: revenue formula or region or Order Details
        self.assertTrue(
            "revenue" in combined_text or "order details" in combined_text or "region" in combined_text
        )


class TestLLMUtilities(unittest.TestCase):
    """Verifies JSON parsing, markdown stripping, and cache key generation."""

    def test_clean_json_markdown_blocks(self):
        raw = "```json\n{\"metric\": \"revenue\", \"value\": 100}\n```"
        parsed = parse_json_safely(raw)
        self.assertEqual(parsed.get("metric"), "revenue")
        self.assertEqual(parsed.get("value"), 100)

    def test_cache_key_determinism(self):
        k1 = _get_cache_key("test prompt", "system", "gemini-2.0-flash", "gemini")
        k2 = _get_cache_key("test prompt", "system", "gemini-2.0-flash", "gemini")
        self.assertEqual(k1, k2)


class TestPlannerSafeguards(unittest.TestCase):
    """Verifies that destructive queries are refused and contracts are upheld."""

    def test_destructive_write_refusal(self):
        q = "Delete all orders from 1997"
        refusal = _check_quick_refusal(q)
        self.assertIsNotNone(refusal)
        self.assertTrue(refusal.is_refusal)
        self.assertIn("violates read-only safety guard", refusal.refusal_reason)

    def test_drop_table_refusal(self):
        q = "DROP TABLE Orders"
        refusal = _check_quick_refusal(q)
        self.assertIsNotNone(refusal)
        self.assertTrue(refusal.is_refusal)


class TestAgentLoop(unittest.TestCase):
    """Tests the agent loop end-to-end against mock executor and validator."""

    def test_loop_write_attempt_blocked(self):
        # A destructive question should stop immediately without querying the database
        res = run_agent_loop(
            question="Drop table Orders",
            executor_fn=mock_executor,
            validator_fn=mock_validator
        )
        self.assertEqual(res.status, "refused")
        self.assertTrue(res.plan.is_refusal)

    def test_loop_with_mock_execution(self):
        # Test query flow against the mock database
        sample_q = "Why did sales drop in the South region last month?"
        trace_events = []

        def trace_collector(event, data):
            trace_events.append(event)

        # Pre-seed cache / mock plan to test loop mechanics without live API call
        from agent.llm import _write_to_cache, _get_cache_key
        dummy_plan_json = """{
          "metric": "revenue",
          "time_window": "1998-04",
          "comparison_period": "1998-03",
          "filters": {"region": "South"},
          "drill_down_dimensions": ["product"],
          "query_list": [
            {
              "query_id": "Q1",
              "sql": "SELECT dcr.Region, strftime('%Y-%m', o.OrderDate) AS Month, SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) AS Revenue FROM [Order Details] od JOIN Orders o ON od.OrderID = o.OrderID JOIN Customers c ON o.CustomerID = c.CustomerID JOIN dim_country_region dcr ON c.Country = dcr.Country WHERE dcr.Region = 'South' GROUP BY dcr.Region, Month ORDER BY Month DESC;",
              "purpose": "Revenue comparison between April and March in South region"
            }
          ],
          "needs_clarification": false,
          "clarification_message": null,
          "is_refusal": false,
          "refusal_reason": null
        }"""

    def test_loop_with_cached_plan(self):
        # Test full query flow with pre-cached plan to verify Q1 execution and Q2 drill-down
        sample_q = "Why did sales drop in the South region last month?"
        trace_events = []

        def trace_collector(event, data):
            trace_events.append(event)

        # Pre-seed cache with a valid Plan JSON for this question
        from agent.llm import _write_to_cache, _get_cache_key
        dummy_plan_json = """{
  "metric": "revenue",
  "time_window": "1998-04",
  "comparison_period": "1998-03",
  "filters": {"region": "South"},
  "drill_down_dimensions": ["product"],
  "query_list": [
    {
      "query_id": "Q1",
      "sql": "SELECT dcr.Region, strftime('%Y-%m', o.OrderDate) AS Month, SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) AS Revenue FROM [Order Details] od JOIN Orders o ON od.OrderID = o.OrderID JOIN Customers c ON o.CustomerID = c.CustomerID JOIN dim_country_region dcr ON c.Country = dcr.Country WHERE dcr.Region = 'South' GROUP BY dcr.Region, Month ORDER BY Month DESC;",
      "purpose": "Revenue comparison between April and March in South region"
    }
  ],
  "needs_clarification": false,
  "clarification_message": null,
  "is_refusal": false,
  "refusal_reason": null
}"""
        from agent.llm import register_fake_response, clear_fake_responses, _write_to_cache, _get_cache_key
        os.environ["LLM_PROVIDER"] = "fake"
        old_cache = os.environ.get("CACHE_MODE", "readwrite")
        os.environ["CACHE_MODE"] = "off"
        register_fake_response(sample_q, dummy_plan_json)

        response = run_agent_loop(
            question=sample_q,
            executor_fn=mock_executor,
            validator_fn=mock_validator,
            trace_callback=trace_collector
        )
        clear_fake_responses()
        os.environ["CACHE_MODE"] = old_cache

        self.assertEqual(response.status, "success")
        self.assertGreater(len(response.findings), 0)
        # Verify traceability: every single finding has a valid query_id
        for finding in response.findings:
            self.assertTrue(finding.query_id in ("Q1", "Q2"))
            self.assertIn(finding.query_id, response.query_results)

        # Verify Q1 and Q2 executed
        self.assertIn("Q1", response.query_results)
        self.assertEqual(response.query_results["Q1"].error, None)
        self.assertIn("Q2", response.query_results)
        self.assertEqual(response.query_results["Q2"].error, None)


if __name__ == "__main__":
    unittest.main()
