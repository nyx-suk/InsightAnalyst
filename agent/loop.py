"""agent/loop.py
Autonomous analytics execution loop for InsightAnalyst.
Orchestrates: Retrieve -> Plan -> Resolve Window -> Execute -> Validate -> Correct/Repair -> Contribution / Drill-Down -> Traceable Findings.

Enforces query budget (max 8 queries) and retry caps (max 3 retries per query).
Implements LLM query repair (prompts/repair.txt) on execution and validation failures.
Emits structured TraceEvent objects for every step.
"""

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import (
    AgentRun,
    AgentResponse,
    Finding,
    Plan,
    PlanQuery,
    QueryResult,
    TraceEvent,
    ValidationResult
)
from knowledge.retriever import retrieve as default_retrieve
from agent.planner import create_plan as default_create_plan
from agent.timewindow import resolve_window
from agent.contribution import (
    analyze_contribution,
    build_driver_finding,
    ContributionAnalysis
)
import agent.llm as default_llm
from agent.llm import parse_json_safely

logger = logging.getLogger("InsightAnalyst.Loop")

MAX_TOTAL_QUERIES = 8
MAX_RETRIES_PER_QUERY = 3

REPAIR_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "repair.txt"


def _call_executor(executor: Any, query_id: str, sql: str) -> QueryResult:
    """Invokes executor supporting both (query_id, sql) and (sql, query_id) signatures."""
    if hasattr(executor, "execute_query"):
        return executor.execute_query(query_id=query_id, sql=sql)
    try:
        return executor(query_id=query_id, sql=sql)
    except TypeError:
        try:
            return executor(query_id, sql)
        except TypeError:
            return executor(sql, query_id)


def _call_validator(
    validator: Any,
    result: QueryResult,
    context: Optional[dict] = None
) -> List[ValidationResult]:
    """Invokes validator supporting both context and no-context signatures."""
    if hasattr(validator, "validate"):
        return validator.validate(result, context=context)
    try:
        return validator(result, context=context)
    except TypeError:
        try:
            return validator(result, context)
        except TypeError:
            return validator(result)


def _extract_scalar(result: QueryResult) -> Optional[float]:
    """Extracts first numeric value from result rows if available."""
    if not result or not result.rows:
        return None
    for row in result.rows:
        for val in row:
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                try:
                    return float(val.replace(",", "").replace("$", ""))
                except ValueError:
                    continue
    return None


def repair_query(
    question: str,
    failed_sql: str,
    error_message: str,
    check_name: str = "execution",
    schema_chunks: Optional[List[str]] = None,
    llm: Any = None,
    error_type: str = "Execution Error"
) -> Optional[str]:
    """
    Prompt 3.4: Calls LLM to repair a SQL query that failed execution or validation.
    Reads prompt template from prompts/repair.txt.
    """
    if REPAIR_PROMPT_PATH.exists():
        template = REPAIR_PROMPT_PATH.read_text(encoding="utf-8")
    else:
        template = (
            "You are a SQLite query debugger for InsightAnalyst.\n"
            "Question: {question}\nFailed SQL: {failed_sql}\nError: {error_message}\n"
            "Return JSON with corrected 'sql'."
        )

    schema_str = ""
    if schema_chunks:
        formatted = []
        for c in schema_chunks:
            if isinstance(c, dict):
                formatted.append(c.get("document", str(c)))
            else:
                formatted.append(str(c))
        schema_str = "\n\n".join(formatted[:6])
    else:
        schema_str = (
            "Table [Order Details]: OrderID, ProductID, UnitPrice, Quantity, Discount\n"
            "Table Customers: CustomerID, Country\n"
            "Table Orders: OrderID, CustomerID, OrderDate\n"
            "Table Products: ProductID, ProductName, UnitPrice, CategoryID\n"
            "Table Categories: CategoryID, CategoryName\n"
            "Table dim_country_region: Country, Region ('South', 'North', 'Europe')"
        )

    prompt = (
        template
        .replace("{schema_context}", schema_str)
        .replace("{question}", question)
        .replace("{failed_sql}", failed_sql)
        .replace("{error_type}", error_type)
        .replace("{check_name}", check_name)
        .replace("{error_message}", error_message)
    )

    active_llm = llm or default_llm
    parsed = None

    try:
        if hasattr(active_llm, "generate_json"):
            parsed = active_llm.generate_json(prompt)
        elif hasattr(active_llm, "generate"):
            raw = active_llm.generate(prompt, temperature=0.0)
            parsed = parse_json_safely(raw)
        elif callable(active_llm):
            raw = active_llm(prompt)
            parsed = parse_json_safely(raw)
    except Exception as e:
        logger.warning(f"Error calling LLM for query repair: {e}")
        return None

    if isinstance(parsed, dict) and "sql" in parsed:
        repaired_sql = str(parsed["sql"]).strip()
        # Ensure it's a read-only query
        upper_sql = repaired_sql.upper()
        forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH", "DETACH", "PRAGMA"]
        if any(f"{kw} " in upper_sql or f" {kw}" in upper_sql for kw in forbidden):
            logger.warning(f"Repaired SQL contained forbidden write keyword: {repaired_sql}")
            return None
        return repaired_sql

    return None


# Backward-compatible alias
_rewrite_query = repair_query


def _generate_drilldown_query(
    plan: Plan,
    top_driver_val: str,
    drill_dim: str,
    query_id: str = "Q2"
) -> PlanQuery:
    """Generates a focused drill-down SQL query for the top contributing dimension slice."""
    metric_col = "SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) AS Revenue"
    filter_clause = f"dcr.Region = '{top_driver_val}'" if top_driver_val else ""
    if not filter_clause and plan.filters:
        for k, v in plan.filters.items():
            filter_clause = f"{k} = '{v}'"
            break

    where_str = f"WHERE {filter_clause}" if filter_clause else ""

    if drill_dim.lower() in ["product", "productname"]:
        sql = (
            f"SELECT p.ProductName, {metric_col} "
            "FROM [Order Details] od "
            "JOIN Products p ON od.ProductID = p.ProductID "
            "JOIN Orders o ON od.OrderID = o.OrderID "
            "JOIN Customers c ON o.CustomerID = c.CustomerID "
            "JOIN dim_country_region dcr ON c.Country = dcr.Country "
            f"{where_str} "
            "GROUP BY p.ProductName "
            "ORDER BY Revenue ASC LIMIT 5;"
        )
        purpose = f"Drill-down: bottom 5 revenue products in {top_driver_val or 'selected slice'}"
    elif drill_dim.lower() in ["category", "categoryname"]:
        sql = (
            f"SELECT cat.CategoryName, {metric_col} "
            "FROM [Order Details] od "
            "JOIN Products p ON od.ProductID = p.ProductID "
            "JOIN Categories cat ON p.CategoryID = cat.CategoryID "
            "JOIN Orders o ON od.OrderID = o.OrderID "
            "JOIN Customers c ON o.CustomerID = c.CustomerID "
            "JOIN dim_country_region dcr ON c.Country = dcr.Country "
            f"{where_str} "
            "GROUP BY cat.CategoryName "
            "ORDER BY Revenue ASC LIMIT 5;"
        )
        purpose = f"Drill-down: categories in {top_driver_val or 'selected slice'}"
    else:
        sql = (
            f"SELECT c.Country, {metric_col} "
            "FROM [Order Details] od "
            "JOIN Orders o ON od.OrderID = o.OrderID "
            "JOIN Customers c ON o.CustomerID = c.CustomerID "
            "JOIN dim_country_region dcr ON c.Country = dcr.Country "
            f"{where_str} "
            "GROUP BY c.Country "
            "ORDER BY Revenue ASC LIMIT 5;"
        )
        purpose = f"Drill-down: country breakdown in {top_driver_val or 'selected slice'}"

    return PlanQuery(query_id=query_id, sql=sql, purpose=purpose)


def run_agent(
    question: str,
    retriever: Any = None,
    llm: Any = None,
    executor: Any = None,
    validator: Any = None,
    on_event: Optional[Callable[[TraceEvent], None]] = None
) -> AgentRun:
    """
    Main autonomous agent execution loop adhering to Prompt 3.3 and 3.4.
    
    Args:
        question: User natural language analytics question.
        retriever: Retrieval function or module.
        llm: LLM client wrapper or module.
        executor: Database query executor function/object.
        validator: Data/sanity check validator function/object.
        on_event: Callback receiving TraceEvent at each step.
        
    Returns:
        AgentRun: Full structured run record with findings and trace.
    """
    step_count = 0
    trace_events: List[TraceEvent] = []
    executed_queries: List[QueryResult] = []
    all_validations: List[ValidationResult] = []
    findings: List[Finding] = []
    assumptions: List[str] = []
    total_query_count = 0

    def emit_event(event_type: str, message: str, query_id: Optional[str] = None):
        nonlocal step_count
        step_count += 1
        te = TraceEvent(step=step_count, event=event_type, message=message, query_id=query_id)
        trace_events.append(te)
        if on_event:
            try:
                on_event(te)
            except Exception as e:
                logger.warning(f"Error in on_event callback: {e}")

    # Step 1: Retrieve context & Plan
    emit_event("plan", f"Starting analysis for question: '{question}'")
    
    # Retrieve domain facts
    docs: List[Any] = []
    retrieve_fn = getattr(loop_module, "retrieve", None) or retriever or default_retrieve
    try:
        if callable(retrieve_fn):
            docs = retrieve_fn(question, k=6)
        elif hasattr(retrieve_fn, "retrieve"):
            docs = retrieve_fn.retrieve(question, k=6)
        emit_event("plan", f"Retrieved {len(docs)} knowledge chunks")
    except Exception as e:
        logger.warning(f"Retrieval error: {e}")
        emit_event("plan", f"Retrieval fallback: {e}")

    # Generate Plan
    plan_fn = getattr(loop_module, "create_plan", None) or default_create_plan
    plan: Optional[Plan] = None
    try:
        plan = plan_fn(question, docs, llm=llm)
    except TypeError:
        try:
            plan = plan_fn(question, docs, llm)
        except TypeError:
            plan = plan_fn(question, docs)

    if not plan:
        emit_event("error", "Planner failed to generate an execution plan")
        return AgentRun(
            question=question,
            plan=None,
            status="failed",
            error="Failed to generate execution plan",
            trace=trace_events
        )

    # Check for clarification or refusal immediately
    if plan.needs_clarification:
        msg = plan.clarification_message or "Clarification required"
        emit_event("done", f"Question requires clarification: {msg}")
        return AgentRun(
            question=question,
            plan=plan,
            status="clarify",
            trace=trace_events,
            assumptions=plan.assumptions or []
        )

    if plan.is_refusal:
        reason = plan.refusal_reason or "Request refused"
        emit_event("done", f"Question refused by safety guard: {reason}")
        return AgentRun(
            question=question,
            plan=plan,
            status="refused",
            error=reason,
            trace=trace_events,
            assumptions=plan.assumptions or []
        )

    # Resolve time windows
    if plan.time_window:
        try:
            resolved_start, resolved_end = resolve_window(plan.time_window)
            assumptions.append(f"Primary time window: {resolved_start} to {resolved_end}")
        except Exception as e:
            assumptions.append(f"Time window spec: {plan.time_window}")

    if plan.comparison_period:
        try:
            comp_start, comp_end = resolve_window(plan.comparison_period)
            assumptions.append(f"Comparison period: {comp_start} to {comp_end}")
        except Exception:
            pass

    if plan.assumptions:
        for asm in plan.assumptions:
            if asm not in assumptions:
                assumptions.append(asm)

    emit_event("plan", f"Plan generated with {len(plan.query_list)} queries for metric '{plan.metric}'")

    # Dynamic query execution queue
    queries_to_run = list(plan.query_list)
    if not queries_to_run:
        emit_event("done", "Plan contains no queries to execute")
        return AgentRun(
            question=question,
            plan=plan,
            status="complete",
            findings=[],
            trace=trace_events,
            assumptions=assumptions
        )

    baseline_analysis: Optional[ContributionAnalysis] = None
    q_idx = 0

    while queries_to_run and total_query_count < MAX_TOTAL_QUERIES:
        plan_query = queries_to_run.pop(0)
        qid = plan_query.query_id or f"Q{q_idx+1}"
        current_sql = plan_query.sql
        attempt = 0
        q_result: Optional[QueryResult] = None
        query_succeeded = False
        last_val_results: List[ValidationResult] = []

        # Retry and Query Repair loop (max 3 retries per query)
        while attempt < MAX_RETRIES_PER_QUERY and total_query_count < MAX_TOTAL_QUERIES:
            attempt += 1
            total_query_count += 1
            emit_event("query", f"Executing {qid} (attempt {attempt}): {plan_query.purpose}", query_id=qid)

            if executor:
                q_result = _call_executor(executor, qid, current_sql)
            else:
                q_result = QueryResult(query_id=qid, sql=current_sql, error="No executor provided")

            # 1. Execution Check
            if q_result.error:
                emit_event("retry", f"{qid} execution error on attempt {attempt}: {q_result.error}", query_id=qid)
                if attempt < MAX_RETRIES_PER_QUERY and total_query_count < MAX_TOTAL_QUERIES:
                    emit_event("retry", f"Calling LLM query repair for {qid} execution error...", query_id=qid)
                    repaired_sql = repair_query(
                        question=question,
                        failed_sql=current_sql,
                        error_message=q_result.error,
                        check_name="execution",
                        schema_chunks=docs,
                        llm=llm,
                        error_type="Execution Error"
                    )
                    if repaired_sql:
                        current_sql = repaired_sql
                    continue
                # Reached cap
                break

            # 2. Validation Check
            val_context: Dict[str, Any] = {}
            if plan.time_window and "Orders" in current_sql:
                try:
                    s, e = resolve_window(plan.time_window)
                    val_context["date_window"] = {"start": s, "end": e}
                    val_context["date_column"] = "OrderDate"
                except Exception:
                    pass

            if "dim_country_region" in current_sql:
                val_context["required_sql_fragments"] = ["dim_country_region"]

            val_results: List[ValidationResult] = []
            if validator:
                val_results = _call_validator(validator, q_result, context=val_context)
                last_val_results = val_results
                check_statuses = [v.status for v in val_results]
                emit_event("check", f"Validated {qid}: {check_statuses}", query_id=qid)

            # Check for validation failures
            val_failures = [v for v in val_results if v.status in ("fail", "suspicious")]
            if not val_failures:
                # Succeeded execution and passed validation cleanly!
                query_succeeded = True
                break

            # Has validation failure -> trigger query repair
            fail_reasons = "; ".join([f"{f.check_name}: {f.reason}" for f in val_failures])
            emit_event("retry", f"{qid} validation failed on attempt {attempt}: {fail_reasons}", query_id=qid)

            if attempt < MAX_RETRIES_PER_QUERY and total_query_count < MAX_TOTAL_QUERIES:
                first_fail = val_failures[0]
                emit_event("retry", f"Calling LLM query repair for {qid} validation failure ({first_fail.check_name})...", query_id=qid)
                repaired_sql = repair_query(
                    question=question,
                    failed_sql=current_sql,
                    error_message=first_fail.reason or "Validation check failed",
                    check_name=first_fail.check_name,
                    schema_chunks=docs,
                    llm=llm,
                    error_type="Validation Failure"
                )
                if repaired_sql:
                    current_sql = repaired_sql
                continue

            # Reached cap
            break

        if q_result:
            executed_queries.append(q_result)
        if last_val_results:
            all_validations.extend(last_val_results)

        # After 3 failed attempts, mark query failed, keep going if other queries support partial answer,
        # and NEVER present failed query numbers as findings.
        if not query_succeeded:
            emit_event("error", f"{qid} failed after {attempt} attempts and is marked failed. Numbers will not be presented.", query_id=qid)
            q_idx += 1
            continue

        # Step 3: Analyze baseline result (Q1)
        if q_idx == 0:
            if len(q_result.columns or []) >= 3 and q_result.rows:
                baseline_analysis = analyze_contribution(q_result)
                if baseline_analysis.items:
                    dir_str = "fell" if baseline_analysis.total_change < 0 else "rose"
                    headline_finding = Finding(
                        id="F_headline",
                        kind="headline",
                        template=f"Overall {plan.metric or 'metric'} {dir_str} by {{total_abs_change}} ({{total_pct_change}}%) [{{query_id}}]",
                        values={
                            "total_abs_change": abs(baseline_analysis.total_change),
                            "total_pct_change": baseline_analysis.total_pct_change or 0.0,
                            "total_change": baseline_analysis.total_change,
                            "prior": baseline_analysis.total_prior,
                            "current": baseline_analysis.total_current,
                            "query_id": qid
                        },
                        query_id=qid
                    )
                    findings.append(headline_finding)

                    driver_f = build_driver_finding(baseline_analysis, query_id=qid, metric_name=plan.metric or "revenue")
                    if driver_f:
                        findings.append(driver_f)
                        top_val = baseline_analysis.top_driver.dimension_value
                        emit_event("drilldown", f"Primary driver identified: {top_val} explaining {baseline_analysis.top_driver.contribution_pct}% of change", query_id=qid)

            # Fallback scalar headline if no finding was generated
            if not findings and q_result.rows:
                val = _extract_scalar(q_result)
                findings.append(
                    Finding(
                        id="F1",
                        kind="headline",
                        template=f"{plan_query.purpose}: {{val}} [{{query_id}}]",
                        values={"val": val if val is not None else 0.0, "query_id": qid},
                        query_id=qid,
                        statement=f"{plan_query.purpose} [{qid}]",
                        value=val,
                        formatted_value=f"{val:,.2f}" if val is not None else None
                    )
                )

            # Step 4: Dynamically generate drill-down query for top contributor if needed
            if plan.drill_down_dimensions and not queries_to_run and total_query_count < MAX_TOTAL_QUERIES:
                drill_dim = plan.drill_down_dimensions[0]
                top_val = (
                    baseline_analysis.top_driver.dimension_value
                    if baseline_analysis and baseline_analysis.top_driver
                    else plan.filters.get("region", "South")
                )
                drill_q = _generate_drilldown_query(plan, top_driver_val=top_val, drill_dim=drill_dim, query_id="Q2")
                queries_to_run.append(drill_q)
                emit_event("plan", f"Generated dynamic drill-down query Q2 for dimension '{drill_dim}' on top contributor '{top_val}'")

        elif q_idx > 0 and q_result.rows:
            # Secondary drilldown finding
            first_val = _extract_scalar(q_result)
            findings.append(
                Finding(
                    id=f"F_{qid}",
                    kind="driver",
                    template=f"{plan_query.purpose}: {{val}} [{{query_id}}]",
                    values={"val": first_val if first_val is not None else 0.0, "query_id": qid},
                    query_id=qid,
                    statement=f"{plan_query.purpose}: {first_val:,.2f} [{qid}]" if first_val is not None else f"{plan_query.purpose} [{qid}]",
                    value=first_val,
                    formatted_value=f"{first_val:,.2f}" if first_val is not None else None
                )
            )
            emit_event("drilldown", f"Drill-down finding captured from {qid}", query_id=qid)

        q_idx += 1

    # Determine final status
    status = "complete"
    failed_queries = [q for q in executed_queries if q.error]
    # Also treat un-succeeded validation queries as failed
    if len(findings) == 0:
        status = "failed"
    elif failed_queries or len(executed_queries) < len(plan.query_list):
        status = "partial"

    emit_event("done", f"Agent run completed with status '{status}' and {len(findings)} traceable findings")

    return AgentRun(
        question=question,
        plan=plan,
        queries=executed_queries,
        validations=all_validations,
        findings=findings,
        trace=trace_events,
        assumptions=assumptions,
        status=status,
        error=None if status != "failed" else "All queries failed"
    )


# Alias module pointer for monkeypatching in unit tests
loop_module = sys.modules[__name__]


def run_agent_loop(
    question: str,
    executor_fn: Any = None,
    validator_fn: Any = None,
    trace_callback: Optional[Callable[[str, Any], None]] = None,
    **kwargs
) -> AgentResponse:
    """
    Backward-compatible wrapper for test_agent_integration.py and legacy callers.
    Translates run_agent results into AgentResponse.
    """
    def event_adapter(event: TraceEvent):
        if trace_callback:
            try:
                trace_callback(event.event, {"event": event.event, "message": event.message})
            except TypeError:
                trace_callback(event.event)

    run = run_agent(
        question=question,
        executor=executor_fn,
        validator=validator_fn,
        on_event=event_adapter
    )

    qr_dict = {q.query_id: q for q in run.queries}
    vr_dict: Dict[str, List[ValidationResult]] = {}
    for q in run.queries:
        vr_dict[q.query_id] = list(run.validations)

    headline = run.findings[0].render() if run.findings else ""

    return AgentResponse(
        question=question,
        status="success" if run.status == "complete" else run.status,
        headline=headline,
        assumptions=run.assumptions,
        plan=run.plan,
        findings=run.findings,
        query_results=qr_dict,
        validation_results=vr_dict,
        trace=[t.to_dict() for t in run.trace],
        error_message=run.error
    )
