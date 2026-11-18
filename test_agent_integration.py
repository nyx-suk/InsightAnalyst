
from agent import loop
from contracts import Plan, PlanQuery, AgentResponse
from executor import executor_fn
from validator import validator_fn


# 1. Create a controlled plan.
# This avoids requiring an external LLM API key.

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


# 2. Temporarily replace the planner and retriever.
loop.create_plan = lambda question, docs: test_plan
loop.retrieve = lambda question, k=4: []


# 3. Capture agent events.
events = []

def trace_callback(event, data):
    events.append(event)


# 4. Run the actual agent loop with Person B's functions.
response = loop.run_agent_loop(
    question="How many countries are assigned to each region?",
    executor_fn=executor_fn,
    validator_fn=validator_fn,
    trace_callback=trace_callback
)


# 5. Verify the response.
print("\n===== INTEGRATION TEST RESULTS =====")

print("Response type:", isinstance(response, AgentResponse))
print("Status:", response.status)
print("Headline:", response.headline)

print("\n===== QUERY RESULTS =====")

for query_id, result in response.query_results.items():
    print("Query ID:", query_id)
    print("Columns:", result.columns)
    print("Rows:", result.rows)
    print("Error:", result.error)

print("\n===== VALIDATION RESULTS =====")

for query_id, validations in response.validation_results.items():
    print("Query ID:", query_id)

    for validation in validations:
        print(validation.to_dict())

print("\n===== FINDINGS =====")

for finding in response.findings:
    print(finding.statement)
    print("Value:", finding.value)

print("\n===== AGENT TRACE =====")
print(events)


# 6. Final assertions.
assert isinstance(response, AgentResponse)
assert response.status == "success"
assert "Q1" in response.query_results
assert "Q1" in response.validation_results
assert response.query_results["Q1"].error is None
assert len(response.findings) >= 1
assert all(
    item.status == "pass"
    for item in response.validation_results["Q1"]
)

print("\nSUCCESS: Full agent-loop integration test passed!")
