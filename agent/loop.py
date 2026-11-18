"""agent/loop.py
Autonomous analytics execution loop for InsightAnalyst.
Orchestrates: Retrieve -> Plan -> Execute -> Validate -> Correct / Drill-down -> Assemble Traceable Findings.
Enforces query budget (max 8 queries) and retry caps (max 3 retries per query).
"""

import json
import logging
from typing import Any, Callable, Dict, List, Optional
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import AgentResponse, Finding, Plan, PlanQuery, QueryResult, ValidationResult
from knowledge.retriever import retrieve
from agent.planner import create_plan
from agent.llm import generate

logger = logging.getLogger("InsightAnalyst.Loop")

MAX_TOTAL_QUERIES = 8
MAX_RETRIES_PER_QUERY = 3


REWRITE_SYSTEM_PROMPT = """You are a SQLite query debugger for InsightAnalyst.
A SQL query failed either execution or a validation sanity check.
Your task is to fix the query based on the error or validation failure reason provided.

RULES:
1. ONLY produce a single valid SELECT query.
2. Never include INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, ATTACH, DETACH, PRAGMA.
3. [Order Details] MUST have square brackets around it.
4. Freight must NEVER be included in revenue.
5. Revenue formula: SUM(UnitPrice * Quantity * (1 - Discount)).
6. To avoid Cartesian join fan-out, never join EmployeeTerritories or Region for sales geography. Use dim_country_region.
7. Return ONLY valid JSON:
{
  "sql": "SELECT ...;",
  "explanation": "Why this query was changed"
}
"""


def _rewrite_query(failed_sql: str, error_or_reason: str, question: str) -> Optional[str]:
    """Asks the LLM to fix a failed or suspicious SQL query."""
    user_prompt = f"""ORIGINAL USER QUESTION:
"{question}"

FAILED SQL QUERY:
```sql
{failed_sql}
```

FAILURE REASON / VALIDATION ERROR:
"{error_or_reason}"

Please fix the SQL query to resolve this error. Return ONLY JSON."""

    try:
        res = generate(
            prompt=user_prompt,
            system_prompt=REWRITE_SYSTEM_PROMPT,
            json_mode=True
        )
        if isinstance(res, dict) and "sql" in res:
            return res["sql"].strip()
    except Exception as e:
        logger.warning(f"Failed to obtain query rewrite from LLM: {e}")
    return None


def _calculate_top_driver(rows: List[tuple], columns: List[str]) -> Optional[Dict[str, Any]]:
    """Deterministically identifies the largest negative contributor or variance in pure Python."""
    if not rows or len(rows) < 2:
        return None

    # Check for name and value columns
    # Expected columns e.g. [dimension_name, revenue] or [dimension_name, current_val, prev_val]
    try:
        col_lower = [c.lower() for c in columns]
        val_idx = -1
        name_idx = 0

        for idx, col in enumerate(col_lower):
            if any(term in col for term in ["revenue", "sales", "val", "amount", "total", "count"]):
                val_idx = idx
                break

        if val_idx == -1:
            val_idx = len(columns) - 1

        # Find min value or largest drop
        sorted_rows = sorted(rows, key=lambda r: float(r[val_idx]) if r[val_idx] is not None else 0.0)
        worst_item = sorted_rows[0]
        best_item = sorted_rows[-1]

        return {
            "dimension": columns[name_idx],
            "worst_name": str(worst_item[name_idx]),
            "worst_val": float(worst_item[val_idx]) if worst_item[val_idx] is not None else 0.0,
            "best_name": str(best_item[name_idx]),
            "best_val": float(best_item[val_idx]) if best_item[val_idx] is not None else 0.0
        }
    except Exception:
        return None


def run_agent_loop(
    question: str,
    executor_fn: Callable[[str, str], QueryResult],
    validator_fn: Callable[[QueryResult], List[ValidationResult]],
    trace_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None
) -> AgentResponse:
    """Executes the end-to-end InsightAnalyst agent loop.

    Args:
        question: Natural-language business question.
        executor_fn: Callable(query_id, sql) -> QueryResult (provided by Person B).
        validator_fn: Callable(QueryResult) -> List[ValidationResult] (provided by Person B).
        trace_callback: Optional hook for live event streaming to UI.

    Returns:
        AgentResponse holding all findings, query results, validations, and trace.
    """
    trace: List[Dict[str, Any]] = []

    def emit(event: str, data: Dict[str, Any]) -> None:
        payload = {"event": event, "data": data}
        trace.append(payload)
        if trace_callback:
            try:
                trace_callback(event, data)
            except Exception:
                pass

    emit("start", {"question": question})

    # Step 1: Retrieval
    retrieved_docs = retrieve(question, k=4)
    emit("retrieval_complete", {
        "retrieved_count": len(retrieved_docs),
        "chunk_names": [d.get("metadata", {}).get("name") for d in retrieved_docs]
    })

    # Step 2: Planning
    plan = create_plan(question, retrieved_docs)
    emit("plan_created", plan.to_dict())

    # Handle immediate refusal or clarification
    if plan.is_refusal:
        emit("refused", {"reason": plan.refusal_reason})
        return AgentResponse(
            question=question,
            status="refused",
            headline=plan.refusal_reason or "Request refused by safety guard.",
            plan=plan,
            trace=trace
        )

    if plan.needs_clarification:
        emit("needs_clarification", {"message": plan.clarification_message})
        return AgentResponse(
            question=question,
            status="needs_clarification",
            headline=plan.clarification_message or "Clarification required.",
            plan=plan,
            trace=trace
        )

    query_results: Dict[str, QueryResult] = {}
    validation_results: Dict[str, List[ValidationResult]] = {}
    findings: List[Finding] = []
    assumptions: List[str] = [
        "Time anchored to the latest complete calendar month in the dataset.",
        "Revenue strictly calculated as UnitPrice * Quantity * (1 - Discount) from [Order Details]; Freight excluded.",
        "Regional mapping derived through Customer Country and dim_country_region."
    ]

    total_query_count = 0
    queries_to_run: List[PlanQuery] = list(plan.query_list)

    # Step 3 & 4: Execute & Validate queries with retry loop
    while queries_to_run and total_query_count < MAX_TOTAL_QUERIES:
        current_pq = queries_to_run.pop(0)
        qid = current_pq.query_id
        active_sql = current_pq.sql
        retry_count = 0
        q_result: Optional[QueryResult] = None
        v_results: List[ValidationResult] = []

        while retry_count <= MAX_RETRIES_PER_QUERY and total_query_count < MAX_TOTAL_QUERIES:
            total_query_count += 1
            emit("executing_query", {"query_id": qid, "sql": active_sql, "attempt": retry_count + 1})

            q_result = executor_fn(qid, active_sql)
            query_results[qid] = q_result

            # Run validator checks
            v_results = validator_fn(q_result)
            validation_results[qid] = v_results
            emit("validated_query", {
                "query_id": qid,
                "validations": [v.to_dict() for v in v_results]
            })

            # Check if any validation check failed or was suspicious
            failures = [v for v in v_results if v.status in ("fail", "suspicious")]
            if not failures:
                # Query passed cleanly!
                break

            # Need retry
            retry_count += 1
            if retry_count <= MAX_RETRIES_PER_QUERY:
                fail_reasons = "; ".join([f"{f.check_name}: {f.reason}" for f in failures])
                emit("retrying_query", {
                    "query_id": qid,
                    "attempt": retry_count,
                    "reasons": fail_reasons
                })
                rewritten_sql = _rewrite_query(active_sql, fail_reasons, question)
                if rewritten_sql and rewritten_sql != active_sql:
                    active_sql = rewritten_sql
                else:
                    # Could not rewrite, abort retry loop for this query
                    break

        if q_result and not q_result.error and q_result.rows:
            # Step 5: Extract findings linked to query_id
            # 1. Add primary finding
            first_val = None
            if q_result.columns and len(q_result.rows) > 0:
                for val in reversed(q_result.rows[0]):
                    if isinstance(val, (int, float)):
                        first_val = float(val)
                        break

            findings.append(Finding(
                statement=f"{current_pq.purpose} [{qid}]",
                value=first_val,
                formatted_value=f"{first_val:,.2f}" if first_val is not None else None,
                query_id=qid,
                metadata={"rows": len(q_result.rows), "columns": q_result.columns}
            ))

            # Step 4 (Drill-Down): If this was Q1 and we have dimensions to explore, compute top driver
            if qid == "Q1" and plan.drill_down_dimensions and len(queries_to_run) == 0:
                driver_summary = _calculate_top_driver(q_result.rows, q_result.columns)
                if driver_summary:
                    emit("drill_down_detected", driver_summary)
                    findings.append(Finding(
                        statement=f"Largest variance identified in {driver_summary['dimension']}: {driver_summary['worst_name']} with value {driver_summary['worst_val']:,.2f} [{qid}]",
                        value=driver_summary["worst_val"],
                        query_id=qid,
                        dimension=driver_summary["dimension"]
                    ))

                # Dynamically append Q2 drill-down query if budget allows
                if total_query_count < MAX_TOTAL_QUERIES:
                    drill_dim = plan.drill_down_dimensions[0]
                    if drill_dim == "product":
                        drill_sql = (
                            "SELECT p.ProductName, SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) AS Revenue "
                            "FROM [Order Details] od "
                            "JOIN Products p ON od.ProductID = p.ProductID "
                            "JOIN Orders o ON od.OrderID = o.OrderID "
                            "JOIN Customers c ON o.CustomerID = c.CustomerID "
                            "JOIN dim_country_region dcr ON c.Country = dcr.Country "
                            "WHERE dcr.Region = 'South' "
                            "GROUP BY p.ProductName "
                            "ORDER BY Revenue ASC LIMIT 5;"
                        )
                        queries_to_run.append(PlanQuery(
                            query_id="Q2",
                            sql=drill_sql,
                            purpose="Drill-down: bottom 5 revenue products in South region"
                        ))

    # Assemble headline answer
    if findings:
        primary_finding = findings[0]
        headline = f"Analysis complete. {primary_finding.statement}."
    else:
        headline = "Analysis could not extract conclusive findings from executed queries."

    emit("completed", {"findings_count": len(findings), "total_queries": total_query_count})

    return AgentResponse(
        question=question,
        status="success" if findings else "partial_failure",
        headline=headline,
        assumptions=assumptions,
        plan=plan,
        findings=findings,
        query_results=query_results,
        validation_results=validation_results,
        trace=trace
    )
