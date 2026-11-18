"""agent/planner.py
Query planner adhering to Prompt 2.3 specifications.
Converts user business questions and retrieved knowledge chunks into a structured Plan.
Uses prompts/planner.txt, enforces temperature 0, and validates outputs strictly against the Plan contract.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import sys

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import Plan, PlanQuery
import agent.llm as default_llm

logger = logging.getLogger("InsightAnalyst.Planner")
PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "prompts" / "planner.txt"


def _load_prompt_template() -> str:
    """Loads system instructions and few-shot examples from prompts/planner.txt."""
    if PROMPT_TEMPLATE_PATH.exists():
        with open(PROMPT_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "You are an analytical query planner. Output JSON conforming to Plan contract."


# Backwards-compatible constants for legacy tests
PLANNER_SYSTEM_PROMPT = _load_prompt_template()
FEW_SHOT_EXAMPLES = ""


def _format_context_chunks(chunks: List[Dict[str, Any]]) -> str:
    """Formats retrieved chunks with clear type and identifier headers."""
    formatted = []
    for idx, c in enumerate(chunks, 1):
        c_type = c.get("type") or c.get("metadata", {}).get("type", "knowledge")
        c_id = c.get("id") or c.get("metadata", {}).get("name", f"chunk_{idx}")
        text = c.get("text") or c.get("document", "")
        formatted.append(f"--- [CHUNK {idx}: {c_type.upper()} ({c_id})] ---\n{text.strip()}")
    return "\n\n".join(formatted)


def _check_quick_refusal(question: str) -> Optional[Plan]:
    """Fast deterministic rule check for forbidden write actions."""
    q_lower = question.lower()
    for kw in ["delete", "drop", "truncate", "update", "insert", "alter"]:
        if f"{kw} " in q_lower or f" {kw} " in q_lower or q_lower.startswith(kw):
            if any(term in q_lower for term in ["table", "orders", "database", "from", "rows"]):
                return Plan(
                    metric="none",
                    time_window="none",
                    is_refusal=True,
                    refusal_reason=f"Write attempt rejected: Action '{kw.upper()}' violates read-only safety guard."
                )
    return None


def validate_plan_dict(data: Dict[str, Any]) -> None:
    """Validates that parsed JSON contains the required Plan contract fields."""
    if not isinstance(data, dict):
        raise ValueError(f"Plan output must be a JSON object, got {type(data).__name__}")

    # Check for refusal or clarification states
    is_refusal = data.get("refused") or data.get("is_refusal", False)
    needs_clarification = data.get("needs_clarification") or bool(data.get("clarification_question"))

    if is_refusal:
        if not (data.get("refusal_reason") or data.get("message")):
            raise ValueError("Plan marked as refused but missing 'refusal_reason'.")
        # Refused plans must produce NO queries
        queries = data.get("queries") if "queries" in data else data.get("query_list", [])
        if queries:
            raise ValueError("Plan marked as refused must not produce any SQL queries.")
        return

    if needs_clarification:
        if not (data.get("clarification_question") or data.get("clarification_message") or data.get("assumptions")):
            raise ValueError("Plan marked as needs_clarification must provide a 'clarification_question' or explicit default 'assumptions'.")
        return

    # Normal execution plan requirements
    required_keys = ["metric", "time_window"]
    for k in required_keys:
        if k not in data or data[k] is None:
            raise ValueError(f"Plan validation failed: missing required field '{k}'.")

    queries = data.get("queries") if "queries" in data else data.get("query_list", [])
    if not isinstance(queries, list):
        raise ValueError("Plan validation failed: 'queries' must be a list.")

    for idx, q in enumerate(queries):
        if not isinstance(q, dict):
            raise ValueError(f"Query {idx} must be a dictionary.")
        if "sql" not in q or not q["sql"].strip():
            raise ValueError(f"Query {idx} missing 'sql' statement.")


def plan(question: str, context_chunks: List[Dict[str, Any]], llm: Any = None) -> Plan:
    """Generates an analytical execution Plan from a user question and retrieved context.

    Public API as specified in Prompt 2.3:
        plan(question: str, context_chunks: list[dict], llm) -> Plan

    Args:
        question: The user's business question.
        context_chunks: Grounded snippets from knowledge/retrieve.py.
        llm: LLM module or callable supporting generate_json(prompt, system=...).

    Returns:
        Validated Plan dataclass instance.

    Raises:
        ValueError or LLMError if generated plan is missing required contract fields.
    """
    system_prompt = _load_prompt_template()
    context_str = _format_context_chunks(context_chunks)

    user_prompt = (
        f"RETRIEVED KNOWLEDGE CHUNKS:\n"
        f"{context_str}\n\n"
        f"USER QUESTION:\n"
        f"\"{question}\"\n\n"
        "Generate the analytical execution plan now. Respond ONLY with valid JSON."
    )

    active_llm = llm if llm is not None else default_llm

    # Execute LLM call using temperature 0
    if hasattr(active_llm, "generate_json"):
        parsed = active_llm.generate_json(user_prompt, system=system_prompt)
    elif callable(active_llm):
        raw = active_llm(user_prompt, system=system_prompt, temperature=0.0)
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    else:
        parsed = default_llm.generate_json(user_prompt, system=system_prompt)

    # Validate output strictly against contract
    validate_plan_dict(parsed)

    # Ensure question is preserved
    if "question" not in parsed or not parsed["question"]:
        parsed["question"] = question

    return Plan.from_dict(parsed)


# Backwards-compatible alias for existing callers
def create_plan(question: str, retrieved_context: List[Dict[str, Any]]) -> Plan:
    """Helper alias preserving compatibility with earlier tests and loop."""
    # Fast check for refusal if asked to drop or delete
    q_lower = question.lower()
    for kw in ["delete", "drop", "truncate", "update", "insert", "alter"]:
        if f"{kw} " in q_lower and any(term in q_lower for term in ["table", "orders", "database"]):
            return Plan(
                metric="none",
                time_window="none",
                is_refusal=True,
                refusal_reason=f"Action '{kw.upper()}' violates read-only safety guard."
            )
    return plan(question, retrieved_context, default_llm)


if __name__ == "__main__":
    from knowledge.retrieve import retrieve
    test_q = "Why did sales drop in the South region last month?"
    print(f"Testing planner for: '{test_q}'")
    chunks = retrieve(test_q, k=6)
    try:
        p = plan(test_q, chunks, default_llm)
        print("\nGenerated Plan:")
        print(json.dumps(p.to_dict(), indent=2))
    except Exception as err:
        print(f"Planner error: {err}")
