import unittest
from agent import loop
from contracts import Plan, PlanQuery, AgentResponse
from executor import executor_fn
from validator import validator_fn


test_plan = Plan(
    metric="country_count",
    time_window="current",
    filters={},
    drill_down_dimensions=[],
    query_list=[
        PlanQuery(
            query_id="Q1",
            sql="""
                SELECT Region, COUNT(*) AS total_countries
                FROM dim_country_region
                GROUP BY Region
                ORDER BY Region;
            """,
            purpose="Count countries in each region"
        )
    ]
)


class TestAgentIntegration(unittest.TestCase):
    """Person B: Integration test for full agent loop with real executor and validator."""

    def test_agent_integration(self):
        # 1. Save original loop attributes to preserve clean test isolation
        orig_create_plan = getattr(loop, "create_plan", None)
        orig_retrieve = getattr(loop, "retrieve", None)

        try:
            # 2. Temporarily replace the planner and retriever.
            loop.create_plan = lambda question, docs: test_plan
            loop.retrieve = lambda question, k=4: []

            # 3. Capture agent events.
            events = []

            def trace_callback(event, data=None):
                events.append(event)

            # 4. Run the actual agent loop with Person B's functions.
            response = loop.run_agent_loop(
                question="How many countries are assigned to each region?",
                executor_fn=executor_fn,
                validator_fn=validator_fn,
                trace_callback=trace_callback
            )

            # 5. Verify the response.
            self.assertIsInstance(response, AgentResponse)
            self.assertEqual(response.status, "success")
            self.assertIn("Q1", response.query_results)
            self.assertIn("Q1", response.validation_results)
            self.assertIsNone(response.query_results["Q1"].error)
            self.assertGreaterEqual(len(response.findings), 1)
            self.assertTrue(all(
                item.status == "pass"
                for item in response.validation_results["Q1"]
            ))

        finally:
            # 6. Clean up monkey patch so other test modules in the suite are never contaminated
            if orig_create_plan is not None:
                loop.create_plan = orig_create_plan
            elif hasattr(loop, "create_plan"):
                delattr(loop, "create_plan")

            if orig_retrieve is not None:
                loop.retrieve = orig_retrieve
            elif hasattr(loop, "retrieve"):
                delattr(loop, "retrieve")


if __name__ == "__main__":
    unittest.main()
