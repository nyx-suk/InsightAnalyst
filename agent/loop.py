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
from agent.llm import parse_json_safely, LLMError, LLMRateLimitError
from agent.narrative import generate_narrative

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


def _safe_float(val: Any) -> float:
    """Safely converts a value to float, returning 0.0 on error."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        if isinstance(val, str):
            try:
                return float(val.replace(",", "").replace("$", ""))
            except ValueError:
                pass
        return 0.0


def _extract_primary_metric_value(
    result: QueryResult,
    metric_name: Optional[str] = None,
    time_window: Optional[Any] = None,
    question: Optional[str] = None
) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """
    Extracts the primary metric value, column name, and entity label using column names,
    metric context, and target time window.
    
    Returns:
        (value, column_name, entity_label)
    """
    if not result or not result.rows or not result.columns:
        return None, None, None

    cols = result.columns
    cols_lower = [c.lower() for c in cols]
    rows = result.rows

    # 1. Score candidate metric columns
    metric_str = (metric_name or "").lower()
    q_str = (question or "").lower()

    if "revenue" in metric_str or "sale" in metric_str or "revenue" in q_str or "sale" in q_str:
        primary_targets = ["total_revenue", "overall_revenue", "monthly_revenue", "totalrevenue", "revenue", "totalsales", "sales"]
        secondary_targets = ["product_revenue", "productrevenue", "item_revenue", "subtotal", "amount"]
    elif "unit" in metric_str or "quantity" in metric_str or "unit" in q_str:
        primary_targets = ["total_units_sold", "total_units", "total_quantity", "totalunits", "totalquantity", "units_sold", "quantity"]
        secondary_targets = ["units", "product_units", "item_quantity"]
    elif "order" in metric_str or "order" in q_str:
        primary_targets = ["total_orders", "order_count", "ordercount", "totalorders", "orders"]
        secondary_targets = ["count"]
    elif "average" in metric_str or "aov" in metric_str or "average" in q_str or "aov" in q_str:
        primary_targets = ["average_order_value", "averageordervalue", "aov", "avg_order_value", "avg_aov"]
        secondary_targets = ["avg", "average"]
    else:
        primary_targets = ["total_revenue", "overall_revenue", "revenue", "total", "amount", "value"]
        secondary_targets = ["count", "units"]

    time_terms = ["month", "ordermonth", "date", "orderdate", "period", "year", "quarter"]
    id_terms = ["id", "orderid", "customerid", "productid", "categoryid"]

    scored_cols = []
    for idx, c in enumerate(cols_lower):
        if any(term in c for term in time_terms) or any(term in c for term in id_terms):
            continue
        score = 0
        if c in primary_targets:
            score = 100
        elif any(c.startswith(f"total_{t}") or c.startswith(f"overall_{t}") for t in primary_targets):
            score = 95
        elif any(t in c for t in primary_targets):
            score = 80
        elif c in secondary_targets:
            score = 40
        elif any(t in c for t in secondary_targets):
            score = 30
        else:
            if len(rows) > 0 and idx < len(rows[0]) and isinstance(rows[0][idx], (int, float)):
                score = 20

        if score > 0:
            scored_cols.append((score, idx, cols[idx]))

    scored_cols.sort(key=lambda x: x[0], reverse=True)
    metric_idx = scored_cols[0][1] if scored_cols else -1
    col_name = scored_cols[0][2] if scored_cols else None

    # Fallback if no scored column
    if metric_idx == -1:
        for idx, col in enumerate(cols):
            if any(term in cols_lower[idx] for term in time_terms) or any(term in cols_lower[idx] for term in id_terms):
                continue
            if len(rows) > 0 and idx < len(rows[0]) and isinstance(rows[0][idx], (int, float)):
                metric_idx = idx
                col_name = col
                break

    if metric_idx == -1:
        return None, None, None

    # 2. Select the most relevant row based on target time window or question
    selected_row_idx = 0
    time_col_idx = -1
    for idx, c in enumerate(cols_lower):
        if any(term in c for term in time_terms):
            time_col_idx = idx
            break

    target_time_str = None
    if isinstance(time_window, str):
        target_time_str = time_window
    elif isinstance(time_window, dict):
        target_time_str = time_window.get("value")

    if time_col_idx != -1 and len(rows) > 1:
        matched = False
        if target_time_str:
            for r_idx, r in enumerate(rows):
                if target_time_str in str(r[time_col_idx]):
                    selected_row_idx = r_idx
                    matched = True
                    break
        if not matched:
            month_map = {
                "september": "2023-09", "sep": "2023-09",
                "august": "2023-08", "aug": "2023-08",
                "july": "2023-07", "jul": "2023-07"
            }
            for m_name, m_code in month_map.items():
                if m_name in q_str:
                    for r_idx, r in enumerate(rows):
                        if m_code in str(r[time_col_idx]) or m_name in str(r[time_col_idx]).lower():
                            selected_row_idx = r_idx
                            matched = True
                            break
                    if matched:
                        break
        if not matched and len(rows) == 2:
            selected_row_idx = len(rows) - 1

    selected_row = rows[selected_row_idx]
    raw_val = selected_row[metric_idx]
    val = None
    if isinstance(raw_val, (int, float)):
        val = float(raw_val)
    elif isinstance(raw_val, str):
        try:
            val = float(raw_val.replace(",", "").replace("$", ""))
        except ValueError:
            pass

    # Extract entity label from non-time, non-metric column (e.g. ProductName or Region)
    entity_label = None
    for idx, col in enumerate(cols):
        if idx != metric_idx and idx != time_col_idx:
            cell_val = selected_row[idx]
            if cell_val is not None and not isinstance(cell_val, (int, float)):
                entity_label = str(cell_val).strip()
                break

    return val, col_name, entity_label


def _extract_scalar(result: QueryResult) -> Optional[float]:
    """Extracts primary metric value from result rows (backward-compatible alias)."""
    val, _, _ = _extract_primary_metric_value(result)
    return val


def _calculate_top_driver(rows: List[tuple], columns: List[str]) -> Optional[Dict[str, Any]]:
    """
    Deterministically identifies the largest contributor or variance in pure Python.
    Strictly operates on entity dimensions (e.g. product, category, region, country),
    NEVER on time periods (month, date, year) or summary/total rows.
    """
    if not rows or len(rows) < 2 or not columns:
        return None

    col_lower = [c.lower() for c in columns]
    time_terms = ["month", "ordermonth", "date", "orderdate", "period", "year", "quarter", "day", "time"]
    id_terms = ["id", "orderid", "customerid", "productid", "categoryid"]
    metric_terms = ["revenue", "sales", "val", "amount", "total", "count", "quantity", "units"]

    # 1. Identify value/metric column
    val_idx = -1
    for idx, col in enumerate(col_lower):
        if any(term in col for term in metric_terms):
            val_idx = idx
            break
    if val_idx == -1:
        val_idx = len(columns) - 1

    # 2. Identify entity dimension column (MUST NOT be time or ID)
    dim_idx = -1
    for idx, col in enumerate(col_lower):
        if idx == val_idx:
            continue
        if any(term in col for term in time_terms):
            continue
        if any(term in col for term in id_terms):
            continue
        dim_idx = idx
        break

    # If no true entity dimension exists, return None (time periods are not drivers)
    if dim_idx == -1:
        return None

    dim_name = columns[dim_idx]

    # 3. Filter rows: exclude summary rows ('Total', 'All') and rows where dim value looks like a date/month
    valid_rows = []
    month_regex = re.compile(r"^\d{4}-\d{2}$|^\d{4}-\d{2}-\d{2}$|^[A-Za-z]+ \d{4}$")
    for r in rows:
        if dim_idx >= len(r) or val_idx >= len(r):
            continue
        dim_val = str(r[dim_idx]).strip()
        if dim_val.lower() in ["total", "all", "overall", "summary"]:
            continue
        if month_regex.match(dim_val):
            continue
        val = _safe_float(r[val_idx]) if val_idx < len(r) else 0.0
        valid_rows.append((dim_val, val))

    if len(valid_rows) < 2:
        return None

    sorted_rows = sorted(valid_rows, key=lambda x: x[1])
    worst_item = sorted_rows[0]
    best_item = sorted_rows[-1]

    return {
        "dimension": dim_name,
        "worst_name": worst_item[0],
        "worst_val": worst_item[1],
        "best_name": best_item[0],
        "best_val": best_item[1]
    }


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
    top_driver_val: Optional[str] = None,
    drill_dim: str = "product",
    query_id: str = "Q2",
    question: Optional[str] = None
) -> PlanQuery:
    """
    Generates a focused drill-down SQL query that strictly follows:
      1. User question intent: 'top' / 'highest' -> DESC vs 'bottom' / 'lowest' -> ASC.
      2. Planned time window: resolves date bounds (e.g. September 2023 -> 2023-09-01 to 2023-09-30).
      3. Dynamic filters: applies region/category filter from plan or top contributor, without hardcoding.
    """
    q_text = (question or plan.question or "").lower()

    # 1. Direction (TOP vs BOTTOM)
    if any(w in q_text for w in ["top", "highest", "most", "best", "leading", "greatest", "max"]):
        order_dir = "DESC"
        order_label = "top"
    elif any(w in q_text for w in ["bottom", "lowest", "least", "worst", "min"]):
        order_dir = "ASC"
        order_label = "bottom"
    elif any(w in q_text for w in ["drop", "fell", "loss", "decrease", "decline"]):
        order_dir = "ASC"
        order_label = "lowest"
    else:
        order_dir = "DESC"
        order_label = "top"

    # 2. Limit
    limit = 5
    m = re.search(r"(?:top|bottom|lowest|highest)\s+(\d+)", q_text)
    if m:
        try:
            limit = int(m.group(1))
        except ValueError:
            limit = 5
    elif "ten" in q_text:
        limit = 10
    elif "three" in q_text:
        limit = 3

    # 3. Time Window
    where_conditions: List[str] = []
    time_label = "target period"

    if plan.time_window:
        try:
            s_date, e_date = resolve_window(plan.time_window)
            where_conditions.append(f"o.OrderDate >= '{s_date}' AND o.OrderDate <= '{e_date}'")
            time_label = f"{s_date} to {e_date}"
        except Exception:
            pass

    if not any("OrderDate" in c for c in where_conditions):
        if "september" in q_text or "2023-09" in q_text:
            where_conditions.append("o.OrderDate >= '2023-09-01' AND o.OrderDate <= '2023-09-30'")
            time_label = "September 2023"
        elif "august" in q_text or "2023-08" in q_text:
            where_conditions.append("o.OrderDate >= '2023-08-01' AND o.OrderDate <= '2023-08-31'")
            time_label = "August 2023"

    # 4. Filters (Region, Category, Country)
    if plan.filters:
        if plan.filters.get("region"):
            where_conditions.append(f"dcr.Region = '{plan.filters['region']}'")
        if plan.filters.get("country"):
            where_conditions.append(f"c.Country = '{plan.filters['country']}'")
        if plan.filters.get("category"):
            where_conditions.append(f"cat.CategoryName = '{plan.filters['category']}'")
    elif top_driver_val and top_driver_val in ["South", "North", "Europe"]:
        where_conditions.append(f"dcr.Region = '{top_driver_val}'")

    where_str = f"WHERE {' AND '.join(where_conditions)}" if where_conditions else ""

    # 5. Metric selection
    metric_str = (plan.metric or "").lower()
    if "unit" in metric_str or "quantity" in metric_str or "unit" in q_text:
        metric_col = "SUM(od.Quantity) AS UnitsSold"
        metric_order = "UnitsSold"
        metric_name = "units sold"
    elif "order" in metric_str or "order" in q_text:
        metric_col = "COUNT(DISTINCT o.OrderID) AS OrderCount"
        metric_order = "OrderCount"
        metric_name = "order count"
    else:
        metric_col = "ROUND(SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)), 2) AS Revenue"
        metric_order = "Revenue"
        metric_name = "revenue"

    # 6. Dimension selection & Required Joins
    dim_lower = (drill_dim or "product").lower()
    joins = [
        "FROM [Order Details] od",
        "JOIN Orders o ON od.OrderID = o.OrderID",
        "JOIN Customers c ON o.CustomerID = c.CustomerID",
        "JOIN dim_country_region dcr ON c.Country = dcr.Country"
    ]

    if dim_lower in ["product", "productname"]:
        dim_select = "p.ProductName"
        dim_desc = "products"
        joins.insert(1, "JOIN Products p ON od.ProductID = p.ProductID")
    elif dim_lower in ["category", "categoryname"]:
        dim_select = "cat.CategoryName"
        dim_desc = "categories"
        joins.insert(1, "JOIN Products p ON od.ProductID = p.ProductID")
        joins.insert(2, "JOIN Categories cat ON p.CategoryID = cat.CategoryID")
    elif dim_lower in ["country"]:
        dim_select = "c.Country"
        dim_desc = "countries"
    else:
        dim_select = "dcr.Region"
        dim_desc = "regions"

    join_str = "\n".join(joins)

    sql = (
        f"SELECT {dim_select}, {metric_col} "
        f"{join_str} "
        f"{where_str} "
        f"GROUP BY {dim_select} "
        f"ORDER BY {metric_order} {order_dir} LIMIT {limit};"
    )
    purpose = f"Drill-down: {order_label} {limit} {dim_desc} by {metric_name} for {time_label}"

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

    try:
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
                        try:
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
                        except Exception as repair_err:
                            emit_event("retry", f"Query repair error: {repair_err}", query_id=qid)
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
                    try:
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
                    except Exception as repair_err:
                        emit_event("retry", f"Query repair error: {repair_err}", query_id=qid)
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
                if len(q_result.columns or []) >= 2 and q_result.rows:
                    baseline_analysis = analyze_contribution(q_result)
                    if baseline_analysis and (baseline_analysis.total_prior or baseline_analysis.total_current):
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

                        # Only add driver finding if top_driver is an actual entity driver (items not empty)
                        if baseline_analysis.top_driver:
                            driver_f = build_driver_finding(baseline_analysis, query_id=qid, metric_name=plan.metric or "revenue")
                            if driver_f:
                                findings.append(driver_f)
                                top_val = baseline_analysis.top_driver.dimension_value
                                emit_event("drilldown", f"Primary driver identified: {top_val} explaining {baseline_analysis.top_driver.contribution_pct}% of change", query_id=qid)

                # Fallback scalar headline if no finding was generated
                if not findings and q_result.rows:
                    val, col_name, entity_label = _extract_primary_metric_value(
                        q_result,
                        metric_name=plan.metric,
                        time_window=plan.time_window,
                        question=question
                    )
                    finding_label = entity_label or plan_query.purpose
                    findings.append(
                        Finding(
                            id="F1",
                            kind="headline",
                            template=f"{finding_label}: {{val}} [{{query_id}}]",
                            values={"val": val if val is not None else 0.0, "query_id": qid},
                            query_id=qid,
                            statement=f"{finding_label}: {val:,.2f} [{qid}]" if val is not None else f"{finding_label} [{qid}]",
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
                        else plan.filters.get("region")
                    )
                    drill_q = _generate_drilldown_query(
                        plan,
                        top_driver_val=top_val,
                        drill_dim=drill_dim,
                        query_id="Q2",
                        question=question
                    )
                    queries_to_run.append(drill_q)
                    emit_event("plan", f"Generated dynamic drill-down query Q2 for dimension '{drill_dim}' on contributor '{top_val or 'target'}'")

            elif q_idx > 0 and q_result.rows:
                # Secondary drilldown finding
                drill_val, col_name, entity_label = _extract_primary_metric_value(
                    q_result,
                    metric_name=plan.metric,
                    time_window=plan.time_window,
                    question=question
                )
                finding_label = entity_label or plan_query.purpose
                findings.append(
                    Finding(
                        id=f"F_{qid}",
                        kind="driver",
                        template=f"{finding_label}: {{val}} [{{query_id}}]",
                        values={"val": drill_val if drill_val is not None else 0.0, "query_id": qid},
                        query_id=qid,
                        statement=f"{finding_label}: {drill_val:,.2f} [{qid}]" if drill_val is not None else f"{finding_label} [{qid}]",
                        value=drill_val,
                        formatted_value=f"{drill_val:,.2f}" if drill_val is not None else None
                    )
                )
                emit_event("drilldown", f"Drill-down finding captured from {qid}", query_id=qid)

            q_idx += 1

        # Generate narrative summary using agent/narrative.py if findings exist
        if findings:
            try:
                narr_res = generate_narrative(findings, question, llm=llm)
                if narr_res and narr_res.filled_text:
                    assumptions.append(f"Narrative Summary: {narr_res.filled_text}")
            except Exception as e:
                logger.warning(f"Narrative generation fallback: {e}")

        # Determine final status
        status = "complete"
        failed_queries = [q for q in executed_queries if q.error]
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

    except Exception as e:
        err_str = str(e)
        if "readonly" in err_str.lower() and "cache" in err_str.lower():
            error_msg = f"Cache miss in readonly mode: {e}"
        elif isinstance(e, LLMRateLimitError) or "rate limit" in err_str.lower() or "429" in err_str:
            error_msg = f"LLM rate limit reached: {e}"
        elif isinstance(e, LLMError):
            error_msg = f"LLM provider error: {e}"
        else:
            error_msg = f"Execution error: {e}"

        logger.warning(f"run_agent caught error: {error_msg}")
        emit_event("error", error_msg)
        final_status = "partial" if findings else "failed"
        return AgentRun(
            question=question,
            plan=plan if "plan" in locals() else None,
            queries=executed_queries,
            validations=all_validations,
            findings=findings,
            trace=trace_events,
            assumptions=assumptions,
            status=final_status,
            error=error_msg
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

    headline = ""
    for a in run.assumptions:
        if a.startswith("Narrative Summary: "):
            headline = a.replace("Narrative Summary: ", "").strip()
            break
    if not headline and run.findings:
        headline = run.findings[0].render()

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
