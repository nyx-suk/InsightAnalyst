"""agent/planner.py
Converts user business questions and retrieved knowledge into a structured Plan.
Enforces read-only safety, detects ambiguous questions, and adheres to grounded metric definitions.
"""

import json
import logging
from typing import Any, Dict, List, Optional
import sys
from pathlib import Path

# Add project root to sys.path for direct imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import Plan, PlanQuery
from agent.llm import generate

logger = logging.getLogger("InsightAnalyst.Planner")

PLANNER_SYSTEM_PROMPT = """You are the Senior Analytics Planner for InsightAnalyst.
Your task is to take a business question, ground it in the provided schema notes and metric definitions, and produce a structured execution plan.

CRITICAL RULES:
1. STRICT READ-ONLY: Never produce or permit INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, ATTACH, DETACH, or PRAGMA statements.
   - If the user asks to modify or delete data, return a plan with "is_refusal": true and an explanation.
2. AMBIGUOUS QUESTIONS: If the question lacks a specific metric or time horizon (e.g., "How are we doing?"), return "needs_clarification": true with a helpful clarification question, OR state reasonable assumptions explicitly.
3. GROUNDED METRICS:
   - Revenue MUST use SUM(UnitPrice * Quantity * (1 - Discount)) from [Order Details].
   - Freight MUST NEVER be included in revenue calculations.
   - Always bracket [Order Details] with square brackets because the table name has a space.
4. REGION MAPPING & NO FAN-OUT:
   - NEVER join EmployeeTerritories or Region tables to calculate customer sales. It creates duplicate rows!
   - Regional breakdowns must join Orders -> Customers -> dim_country_region (c.Country = dcr.Country).
5. RELATIVE DATES:
   - "Last month" means the last complete calendar month in the Northwind database, NOT today's calendar date.
   - "Previous period" or "prior month" means the month immediately preceding the anchored month.

YOUR OUTPUT MUST BE STRICT JSON MATCHING THIS EXACT SCHEMA:
{
  "metric": "revenue" | "units_sold" | "order_count" | "average_order_value",
  "time_window": "string describing the target period (e.g. last_complete_month)",
  "comparison_period": "string describing the baseline period (e.g. previous_month) or null",
  "filters": {"dimension": "value"},
  "drill_down_dimensions": ["region", "product", "time"],
  "query_list": [
    {
      "query_id": "Q1",
      "sql": "SELECT ...;",
      "purpose": "What this query calculates"
    }
  ],
  "needs_clarification": false,
  "clarification_message": null,
  "is_refusal": false,
  "refusal_reason": null
}
"""

FEW_SHOT_EXAMPLES = """
Example 1:
User Question: "Why did sales drop in the South region last month?"
Output JSON:
{
  "metric": "revenue",
  "time_window": "last_complete_month",
  "comparison_period": "previous_month",
  "filters": {"region": "South"},
  "drill_down_dimensions": ["product", "time"],
  "query_list": [
    {
      "query_id": "Q1",
      "sql": "SELECT dcr.Region, strftime('%Y-%m', o.OrderDate) AS OrderMonth, SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) AS Revenue FROM [Order Details] od JOIN Orders o ON od.OrderID = o.OrderID JOIN Customers c ON o.CustomerID = c.CustomerID JOIN dim_country_region dcr ON c.Country = dcr.Country WHERE dcr.Region = 'South' GROUP BY dcr.Region, OrderMonth ORDER BY OrderMonth DESC LIMIT 2;",
      "purpose": "Compare South region revenue between the last complete month and the prior month."
    }
  ],
  "needs_clarification": false,
  "clarification_message": null,
  "is_refusal": false,
  "refusal_reason": null
}

Example 2:
User Question: "Delete all orders from 1997"
Output JSON:
{
  "metric": "none",
  "time_window": "none",
  "comparison_period": null,
  "filters": {},
  "drill_down_dimensions": [],
  "query_list": [],
  "needs_clarification": false,
  "clarification_message": null,
  "is_refusal": true,
  "refusal_reason": "InsightAnalyst is strictly a read-only analytics agent. Data modification or deletion is strictly forbidden."
}

Example 3:
User Question: "How are we doing?"
Output JSON:
{
  "metric": "revenue",
  "time_window": "last_complete_month",
  "comparison_period": "previous_month",
  "filters": {},
  "drill_down_dimensions": ["region", "product"],
  "query_list": [],
  "needs_clarification": true,
  "clarification_message": "Could you please specify which metric you'd like to evaluate (e.g., Revenue, Units Sold, or Order Count) and the time window you are interested in?",
  "is_refusal": false,
  "refusal_reason": null
}
"""


def _check_quick_refusal(question: str) -> Optional[Plan]:
    """Fast deterministic rule check for forbidden write actions."""
    q_lower = question.lower()
    destructive_keywords = ["delete", "drop", "truncate", "update", "insert", "alter", "attach", "detach"]
    for kw in destructive_keywords:
        # Check if used as an imperative verb at start or with table
        if f"{kw} " in q_lower or f" {kw} " in q_lower:
            if "table" in q_lower or "orders" in q_lower or "rows" in q_lower or "database" in q_lower or "from" in q_lower:
                return Plan(
                    metric="none",
                    time_window="none",
                    is_refusal=True,
                    refusal_reason=f"Write attempt rejected: Action '{kw.upper()}' violates read-only safety guard."
                )
    return None


def create_plan(question: str, retrieved_context: List[Dict[str, Any]]) -> Plan:
    """Creates a structured Plan from a user question and retrieved knowledge chunks.

    Args:
        question: The user query in natural language.
        retrieved_context: Knowledge base snippets from retriever.py.

    Returns:
        A validated Plan dataclass instance.
    """
    # 1. Deterministic safety check
    quick_refusal = _check_quick_refusal(question)
    if quick_refusal:
        return quick_refusal

    # 2. Format retrieved context for prompt
    context_blocks = []
    for idx, c in enumerate(retrieved_context, 1):
        meta = c.get("metadata", {})
        header = f"[{meta.get('type', 'info').upper()} - {meta.get('name', 'General')}]"
        context_blocks.append(f"--- Context Block {idx} {header} ---\n{c.get('document', '')}")

    context_str = "\n\n".join(context_blocks)

    user_prompt = f"""RETRIEVED KNOWLEDGE BASE:
{context_str}

FEW-SHOT EXAMPLES:
{FEW_SHOT_EXAMPLES}

USER QUESTION:
"{question}"

Generate the structured JSON plan now. Respond ONLY with valid JSON."""

    # 3. Call LLM with JSON mode
    try:
        raw_response = generate(
            prompt=user_prompt,
            system_prompt=PLANNER_SYSTEM_PROMPT,
            json_mode=True
        )
    except Exception as e:
        logger.error(f"LLM planner call failed: {e}")
        # Return fallback clarification plan rather than crashing
        return Plan(
            metric="unknown",
            time_window="unknown",
            needs_clarification=True,
            clarification_message=f"I encountered a temporary error while generating the analysis plan: {e}. Please try again."
        )

    # 4. Parse and validate against the Plan contract
    try:
        plan = Plan.from_dict(raw_response)
        return plan
    except Exception as parse_err:
        logger.error(f"Failed to instantiate Plan from LLM response: {parse_err}")
        return Plan(
            metric="unknown",
            time_window="unknown",
            needs_clarification=True,
            clarification_message="Could not parse the analytical plan. Please rephrase your query."
        )


if __name__ == "__main__":
    from knowledge.retriever import retrieve
    q = "Why did sales drop in the South region last month?"
    print(f"Planning for: {q}")
    docs = retrieve(q, k=3)
    plan = create_plan(q, docs)
    print("\nGenerated Plan:")
    print(json.dumps(plan.to_dict(), indent=2))
