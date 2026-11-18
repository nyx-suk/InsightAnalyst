"""tests/test_loop.py
Unit and integration tests for agent/loop.py (Prompt 3.3).
Tests run_agent() with fake LLM and fake executor, verifying:
- Clarification and refusal immediate stops
- Baseline query execution and validation
- Contribution driver identification in code
- Drill-down query generation and execution
- Retry and budget caps (max 3 retries, max 8 queries)
- TraceEvent streaming through on_event callback
"""

import unittest
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import Plan, PlanQuery, QueryResult, ValidationResult, TraceEvent, AgentRun
from agent.loop import run_agent, MAX_TOTAL_QUERIES, MAX_RETRIES_PER_QUERY


class FakeRetriever:
    """Mock retriever returning static knowledge chunks."""
    def retrieve(self, question: str, k: int = 6):
        return [
            {"id": "metrics_revenue", "document": "Revenue formula is SUM(UnitPrice * Quantity * (1 - Discount))", "type": "metric"},
            {"id": "dim_country_region", "document": "dim_country_region maps 21 countries to South, North, Europe", "type": "rule"}
        ]


class FakeLLM:
    """Mock LLM returning controlled responses."""
    def __init__(self, plan_to_return: Plan, repair_sql: str = None):
        self.plan = plan_to_return
        self.repair_sql = repair_sql
        self.repair_calls = []

    def generate_json(self, prompt: str, system: str = None):
        if "query debugger" in prompt.lower() or "failure type" in prompt.lower():
            self.repair_calls.append(prompt)
            return {
                "sql": self.repair_sql or "SELECT 1;",
                "explanation": "Corrected query"
            }
        return self.plan.to_dict()


class FakeExecutor:
    """Mock database executor with call tracking and configurable responses."""
    def __init__(self, canned_results=None, fail_count=0):
        self.canned = canned_results or {}
        self.fail_count = fail_count
        self.calls = []

    def execute_query(self, query_id: str, sql: str) -> QueryResult:
        self.calls.append((query_id, sql))
        if self.fail_count > 0:
            self.fail_count -= 1
            return QueryResult(query_id=query_id, sql=sql, error="SIMULATED_TRANSIENT_DB_ERROR")
        if query_id in self.canned:
            return self.canned[query_id]
        return QueryResult(
            query_id=query_id,
            sql=sql,
            columns=["metric_val"],
            rows=[(12345.67,)],
            row_count=1
        )


class FakeValidator:
    """Mock validator returning pass results."""
    def __init__(self, status="pass"):
        self.status = status
        self.validated = []

    def validate(self, result: QueryResult, context=None):
        self.validated.append((result.query_id, context))
        return [ValidationResult(status=self.status, check_name="execution_check")]


class TestAgentLoopPrompt33(unittest.TestCase):
    """Test suite for Prompt 3.3 agent loop."""

    def test_run_agent_clarification_stops_immediately(self):
        """Flow 1: If plan needs clarification, stop immediately without querying."""
        plan = Plan(
            metric="revenue",
            time_window="last_complete_month",
            needs_clarification=True,
            clarification_message="Did you mean gross or net sales?",
            query_list=[PlanQuery(query_id="Q1", sql="SELECT 1;", purpose="Should not run")]
        )
        executor = FakeExecutor()
        events = []

        run = run_agent(
            question="How are sales?",
            retriever=FakeRetriever(),
            llm=FakeLLM(plan),
            executor=executor,
            validator=FakeValidator(),
            on_event=lambda e: events.append(e)
        )

        self.assertEqual(run.status, "clarify")
        self.assertEqual(len(run.queries), 0)
        self.assertEqual(len(executor.calls), 0)
        self.assertTrue(any(e.event == "done" and "clarification" in e.message.lower() for e in events))

    def test_run_agent_refusal_stops_immediately(self):
        """Flow 1: If plan is refusal, stop immediately without querying."""
        plan = Plan(
            metric="none",
            time_window="none",
            is_refusal=True,
            refusal_reason="DROP TABLE violates read-only safety guard",
            query_list=[]
        )
        executor = FakeExecutor()
        events = []

        run = run_agent(
            question="Drop table orders",
            retriever=FakeRetriever(),
            llm=FakeLLM(plan),
            executor=executor,
            validator=FakeValidator(),
            on_event=lambda e: events.append(e)
        )

        self.assertEqual(run.status, "refused")
        self.assertEqual(len(run.queries), 0)
        self.assertEqual(len(executor.calls), 0)
        self.assertTrue(any(e.event == "done" and "refused" in e.message.lower() for e in events))

    def test_run_agent_drilldown_and_contribution(self):
        """Flow 2, 3, 4: Baseline execution -> contribution driver pick in code -> drill-down Q2."""
        plan = Plan(
            metric="revenue",
            time_window="2023-09",
            comparison_period="2023-08",
            filters={"region": "South"},
            drill_down_dimensions=["product"],
            query_list=[
                PlanQuery(
                    query_id="Q1",
                    sql="SELECT Region, aug_rev, sep_rev FROM sales_comp;",
                    purpose="Compare regional sales"
                )
            ]
        )

        # Q1 returns 3 regions where South is the biggest drop:
        # Europe: 500 -> 480 (-20)
        # South:  200 -> 100 (-100, top driver)
        # North:  300 -> 290 (-10)
        q1_res = QueryResult(
            query_id="Q1",
            sql="SELECT Region, aug_rev, sep_rev FROM sales_comp;",
            columns=["Region", "aug_rev", "sep_rev"],
            rows=[
                ("Europe", 500.0, 480.0),
                ("South", 200.0, 100.0),
                ("North", 300.0, 290.0)
            ],
            row_count=3
        )

        # Q2 drilldown returns products in South
        q2_res = QueryResult(
            query_id="Q2",
            sql="SELECT p.ProductName, ...",
            columns=["ProductName", "Revenue"],
            rows=[("Chai", 50.0), ("Syrup", 30.0)],
            row_count=2
        )

        executor = FakeExecutor(canned_results={"Q1": q1_res, "Q2": q2_res})
        validator = FakeValidator()
        events = []

        run = run_agent(
            question="Why did sales drop in South region last month?",
            retriever=FakeRetriever(),
            llm=FakeLLM(plan),
            executor=executor,
            validator=validator,
            on_event=lambda e: events.append(e)
        )

        self.assertEqual(run.status, "complete")
        self.assertEqual(len(run.queries), 2)
        self.assertEqual(executor.calls[0][0], "Q1")
        self.assertEqual(executor.calls[1][0], "Q2")

        # Verify findings
        self.assertGreaterEqual(len(run.findings), 2)
        # Finding 1: Headline
        headline = run.findings[0]
        self.assertEqual(headline.kind, "headline")
        self.assertEqual(headline.query_id, "Q1")
        self.assertIn("fell by 130.0", headline.render())

        # Finding 2: Top driver identified by contribution.py in code
        driver = run.findings[1]
        self.assertEqual(driver.kind, "driver")
        self.assertEqual(driver.values["dimension_value"], "South")
        self.assertEqual(driver.values["abs_change"], 100.0)

        # Verify TraceEvents
        event_types = [e.event for e in events]
        self.assertIn("plan", event_types)
        self.assertIn("query", event_types)
        self.assertIn("check", event_types)
        self.assertIn("drilldown", event_types)
        self.assertIn("done", event_types)

    def test_run_agent_retry_cap_enforced(self):
        """Limits: At most 3 retries per query."""
        plan = Plan(
            metric="revenue",
            time_window="2023-09",
            query_list=[PlanQuery(query_id="Q1", sql="SELECT 1;", purpose="Test retry cap")]
        )
        # Fails 5 times -> should stop after 3 attempts
        executor = FakeExecutor(fail_count=5)
        run = run_agent(
            question="Test retries",
            retriever=FakeRetriever(),
            llm=FakeLLM(plan),
            executor=executor,
            validator=FakeValidator()
        )

        self.assertEqual(run.status, "failed")
        # Attempt count must be capped at 3
        self.assertEqual(len(executor.calls), MAX_RETRIES_PER_QUERY)

    def test_run_agent_total_query_budget_cap(self):
        """Limits: At most 8 queries total per question."""
        # Provide 12 planned queries
        many_queries = [
            PlanQuery(query_id=f"Q{i}", sql=f"SELECT {i};", purpose=f"Query {i}")
            for i in range(1, 13)
        ]
        plan = Plan(metric="revenue", time_window="2023-09", query_list=many_queries)
        executor = FakeExecutor()

        run = run_agent(
            question="Test budget cap",
            retriever=FakeRetriever(),
            llm=FakeLLM(plan),
            executor=executor,
            validator=FakeValidator()
        )

        # Total executed queries must not exceed MAX_TOTAL_QUERIES (8)
        self.assertEqual(len(run.queries), MAX_TOTAL_QUERIES)
        self.assertEqual(len(executor.calls), MAX_TOTAL_QUERIES)

    def test_repair_on_wrong_column_name(self):
        """Prompt 3.4: Inject wrong column name; verify LLM repair is called and loop recovers."""
        bad_sql = "SELECT WrongCol FROM Orders;"
        good_sql = "SELECT OrderID FROM Orders;"
        plan = Plan(
            metric="orders",
            time_window="2023-09",
            query_list=[PlanQuery(query_id="Q1", sql=bad_sql, purpose="Get order count")]
        )

        class ConditionalExecutor:
            def __init__(self):
                self.calls = []
            def execute_query(self, query_id: str, sql: str) -> QueryResult:
                self.calls.append((query_id, sql))
                if "WrongCol" in sql:
                    return QueryResult(query_id=query_id, sql=sql, error="no such column: WrongCol")
                return QueryResult(query_id=query_id, sql=sql, columns=["OrderID"], rows=[(10248,)], row_count=1)

        executor = ConditionalExecutor()
        llm = FakeLLM(plan, repair_sql=good_sql)

        run = run_agent(
            question="How many orders?",
            retriever=FakeRetriever(),
            llm=llm,
            executor=executor,
            validator=FakeValidator(status="pass")
        )

        # Loop must recover and finish with complete status
        self.assertEqual(run.status, "complete")
        self.assertEqual(len(llm.repair_calls), 1)
        self.assertIn("no such column: WrongCol", llm.repair_calls[0])
        # Executed twice: attempt 1 with bad_sql, attempt 2 with good_sql
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(executor.calls[0][1], bad_sql)
        self.assertEqual(executor.calls[1][1], good_sql)
        # Findings should be generated from good_sql
        self.assertGreater(len(run.findings), 0)
        self.assertEqual(run.findings[0].query_id, "Q1")

    def test_repair_on_reconciliation_failure(self):
        """Prompt 3.4: Inject reconciliation validation failure; verify LLM repair recovers."""
        initial_sql = "SELECT Region, aug, sep FROM broken_summary;"
        fixed_sql = "SELECT Region, aug, sep FROM reconciled_summary;"
        plan = Plan(
            metric="revenue",
            time_window="2023-09",
            comparison_period="2023-08",
            query_list=[PlanQuery(query_id="Q1", sql=initial_sql, purpose="Regional comparison")]
        )

        class ReconciliationValidator:
            def __init__(self):
                self.attempt = 0
            def validate(self, result: QueryResult, context=None):
                self.attempt += 1
                if "broken" in result.sql:
                    return [
                        ValidationResult(
                            status="fail",
                            reason="Reconciliation failed: breakdown total 100 != expected 150",
                            check_name="reconciliation"
                        )
                    ]
                return [ValidationResult(status="pass", check_name="reconciliation")]

        class ReconcileExecutor:
            def __init__(self):
                self.calls = []
            def execute_query(self, query_id: str, sql: str) -> QueryResult:
                self.calls.append(sql)
                return QueryResult(
                    query_id=query_id,
                    sql=sql,
                    columns=["Region", "aug", "sep"],
                    rows=[("South", 100.0, 50.0), ("Europe", 50.0, 40.0)],
                    row_count=2
                )

        validator = ReconciliationValidator()
        executor = ReconcileExecutor()
        llm = FakeLLM(plan, repair_sql=fixed_sql)

        run = run_agent(
            question="Why did revenue drop?",
            retriever=FakeRetriever(),
            llm=llm,
            executor=executor,
            validator=validator
        )

        self.assertEqual(run.status, "complete")
        self.assertEqual(len(llm.repair_calls), 1)
        self.assertIn("Reconciliation failed", llm.repair_calls[0])
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(executor.calls[1], fixed_sql)
        self.assertGreater(len(run.findings), 0)

    def test_failed_query_after_three_attempts_partial_answer(self):
        """Prompt 3.4: Q1 fails all 3 attempts; Q2 succeeds. Status is partial, Q1 has no findings."""
        plan = Plan(
            metric="revenue",
            time_window="2023-09",
            query_list=[
                PlanQuery(query_id="Q1", sql="SELECT BadCol FROM Orders;", purpose="Failing query"),
                PlanQuery(query_id="Q2", sql="SELECT 100 AS GoodVal;", purpose="Succeeding query")
            ]
        )

        class PartialExecutor:
            def __init__(self):
                self.calls = []
            def execute_query(self, query_id: str, sql: str) -> QueryResult:
                self.calls.append((query_id, sql))
                if query_id == "Q1":
                    return QueryResult(query_id=query_id, sql=sql, error="Permanent syntax error")
                return QueryResult(query_id=query_id, sql=sql, columns=["GoodVal"], rows=[(100,)], row_count=1)

        executor = PartialExecutor()
        # LLM repair returns unrepairable SQL so Q1 fails 3 attempts
        llm = FakeLLM(plan, repair_sql="SELECT BadCol FROM Orders;")

        run = run_agent(
            question="Multi-query test",
            retriever=FakeRetriever(),
            llm=llm,
            executor=executor,
            validator=FakeValidator()
        )

        # Status must be partial because Q1 failed but Q2 succeeded
        self.assertEqual(run.status, "partial")
        # Q1 was attempted 3 times
        q1_calls = [c for c in executor.calls if c[0] == "Q1"]
        self.assertEqual(len(q1_calls), 3)
        # Q2 was executed
        q2_calls = [c for c in executor.calls if c[0] == "Q2"]
        self.assertEqual(len(q2_calls), 1)

        # CRITICAL: Numbers from failed Q1 must NEVER appear in findings
        for finding in run.findings:
            self.assertNotEqual(finding.query_id, "Q1")
        self.assertTrue(all(f.query_id == "Q2" for f in run.findings))


if __name__ == "__main__":
    unittest.main()
