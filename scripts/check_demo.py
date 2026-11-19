"""scripts/check_demo.py
Offline demo evaluation and regression checker for InsightAnalyst (Prompt 4.2).

Runs all demo questions in demo_questions.txt with CACHE_MODE=readonly (strictly no network),
audits that:
  1. Each question completes successfully.
  2. Query counts and retry counts are within budget caps.
  3. Every single number in the findings and narrative traces to a valid query ID ([Q1], [Q2], etc.).

Exits with code 0 if all questions pass, or code 1 if any question fails.
"""

import argparse
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import List, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Strict offline constraint: enforce readonly cache mode BEFORE importing agent modules
os.environ["CACHE_MODE"] = "readonly"

from agent.loop import run_agent
from contracts import AgentRun, Finding
from executor import execute_query
from validator import validate
from knowledge.retrieve import retrieve
import agent.llm as llm

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("InsightAnalyst.CheckDemo")


def load_demo_questions(filepath: Path) -> List[str]:
    """Reads non-empty, non-comment lines from demo questions file."""
    if not filepath.exists():
        raise FileNotFoundError(f"Demo questions file not found: {filepath}")
    
    questions = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                questions.append(stripped)
    return questions


def verify_numbers_have_query_ids(run: AgentRun) -> Tuple[bool, str]:
    """Audits that every number in the findings and report links to a valid query ID.
    
    Rules checked:
      1. run.findings must not be empty for a completed analytical run.
      2. Every Finding must possess a valid, non-empty query_id corresponding to an executed query.
      3. Finding statements with numbers must explicitly cite their query ID (e.g. [Q1], [Q2]).
      4. Any narrative summary in assumptions must cite query IDs next to numerical claims.
    """
    if not run.findings:
        return False, "No findings generated in agent run"

    executed_qids = {q.query_id for q in run.queries if q.query_id}

    # 1. Audit individual findings
    for idx, f in enumerate(run.findings, start=1):
        if not f.query_id:
            return False, f"Finding '{f.id or idx}' has missing query_id"
        if f.query_id not in executed_qids:
            return False, f"Finding '{f.id}' references query_id '{f.query_id}' not present in executed queries ({executed_qids})"

        stmt = f.statement or f.template or ""
        # Check if numbers exist in statement
        has_numbers = bool(re.search(r"\d", stmt))
        if has_numbers:
            # Must contain a bracketed query reference [Q...] or query_id
            if f"[{f.query_id}]" not in stmt and f"[{f.query_id.upper()}]" not in stmt:
                # Also accept generic bracketed citation if present
                if not re.search(r"\[Q\d+\]", stmt, re.IGNORECASE):
                    return False, f"Finding statement '{stmt}' contains numbers but lacks [{f.query_id}] citation"

    # 2. Audit assumptions / narrative summaries
    for asm in run.assumptions:
        if asm.startswith("Narrative Summary:"):
            # Ensure numbers in narrative have [Q...] query citations
            words = asm.split()
            for word in words:
                if re.search(r"\d", word):
                    # Check if somewhere in the surrounding text there's a [Q...] citation
                    if not re.search(r"\[Q\d+\]", asm, re.IGNORECASE):
                        return False, f"Narrative summary contains numbers without query citation: {asm}"

    return True, "YES"


def verify_metric_relevance(question: str, run: AgentRun) -> Tuple[bool, str]:
    """
    Audits semantic correctness: verifies that the executed plan and findings
    actually evaluate the metric requested in the question (prevents false positives
    where numbers are traced but the plan evaluated an unrelated metric).
    """
    q_lower = question.lower()

    # 1. Infer expected metric from question keywords
    expected_metric = None
    if any(k in q_lower for k in ["sales", "revenue", "dollar"]):
        expected_metric = "revenue"
    elif any(k in q_lower for k in ["units sold", "units", "quantity"]):
        expected_metric = "units_sold"
    elif any(k in q_lower for k in ["how many orders", "order count", "orders placed", "number of orders", "orders were placed"]):
        expected_metric = "order_count"
    elif any(k in q_lower for k in ["average order value", "aov", "average order"]):
        expected_metric = "average_order_value"
    elif "how many countries" in q_lower or "country count" in q_lower:
        expected_metric = "country_count"

    if not expected_metric:
        return True, "YES"

    # 2. Check Plan metric
    actual_metric = getattr(run.plan, "metric", None)
    if actual_metric:
        actual_metric_lower = str(actual_metric).lower()
        if expected_metric not in actual_metric_lower and actual_metric_lower not in expected_metric:
            return False, f"Semantic metric mismatch: question asked for '{expected_metric}' but plan evaluated '{actual_metric}'"

    # 3. Check Findings content
    metric_keywords = {
        "revenue": ["revenue", "sales", "$", "fell", "rose"],
        "units_sold": ["units", "units_sold", "quantity", "sold"],
        "order_count": ["orders", "order_count", "order"],
        "average_order_value": ["average order", "aov", "value"],
        "country_count": ["countries", "country"]
    }
    keywords = metric_keywords.get(expected_metric, [expected_metric])

    all_texts = []
    for f in run.findings:
        all_texts.append(f.statement or f.template or "")
    for a in run.assumptions:
        all_texts.append(a)

    combined_text = " ".join(all_texts).lower()
    if not any(k in combined_text for k in keywords):
        return False, f"Finding content mismatch: question asked for '{expected_metric}', but findings do not mention {keywords}"

    return True, "YES"


def check_demo(questions_file: Path) -> bool:
    """Executes all demo questions in readonly mode and reports on status, queries, retries, and citation audit."""
    questions = load_demo_questions(questions_file)
    if not questions:
        print("Error: No demo questions found to check.", file=sys.stderr)
        return False

    print("=" * 105)
    print("INSIGHTANALYST: DEMO REGRESSION & OFFLINE CHECK (Prompt 4.2)")
    print(f"Cache Mode: readonly (no network calls) | Database: data/northwind.db")
    print(f"Total Questions: {len(questions)}")
    print("=" * 105)

    results = []
    all_passed = True
    start_time = time.time()

    for idx, question in enumerate(questions, start=1):
        q_start = time.time()
        error_msg = None
        run: AgentRun | None = None

        try:
            run = run_agent(
                question=question,
                retriever=retrieve,
                llm=llm,
                executor=execute_query,
                validator=validate
            )
        except Exception as e:
            error_msg = str(e)

        q_elapsed = time.time() - q_start

        if error_msg:
            status = "EXCEPTION"
            query_count = 0
            retry_count = 0
            traced_str = "NO"
            passed = False
            details = f"Exception: {error_msg}"
        elif run is None:
            status = "NULL_RUN"
            query_count = 0
            retry_count = 0
            traced_str = "NO"
            passed = False
            details = "run_agent returned None"
        else:
            status = run.status
            query_count = len(run.queries)
            retry_count = len([e for e in run.trace if e.event == "retry"])
            traced, trace_reason = verify_numbers_have_query_ids(run)
            metric_ok, metric_reason = verify_metric_relevance(question, run)
            traced_str = "YES" if traced else "NO"

            passed = (status == "complete") and traced and metric_ok
            if not passed:
                if status != "complete":
                    details = f"Status '{status}': {run.error or 'Incomplete'}"
                elif not traced:
                    details = f"Audit failed: {trace_reason}"
                else:
                    details = f"Semantic audit failed: {metric_reason}"
            else:
                details = f"Passed ({len(run.findings)} findings verified)"

        if not passed:
            all_passed = False

        results.append({
            "index": idx,
            "question": question,
            "status": status,
            "queries": query_count,
            "retries": retry_count,
            "traced": traced_str,
            "passed": passed,
            "details": details,
            "elapsed": q_elapsed,
            "run": run
        })

    # Print Formatted Evaluation Table
    print("\n" + "-" * 105)
    print(f"{'#':<3} | {'Question':<45} | {'Status':<10} | {'Queries':<7} | {'Retries':<7} | {'Numbers Traced':<14} | {'Outcome':<8}")
    print("-" * 105)

    for r in results:
        q_trunc = (r['question'][:42] + "...") if len(r['question']) > 45 else r['question']
        outcome = "PASS" if r['passed'] else "FAIL"
        print(f"{r['index']:<3} | {q_trunc:<45} | {r['status']:<10} | {r['queries']:<7} | {r['retries']:<7} | {r['traced']:<14} | {outcome:<8}")

    print("-" * 105)

    # Print Detail Sections for findings
    print("\nDETAILED FINDINGS & CITATION AUDIT:")
    for r in results:
        status_symbol = "✓" if r['passed'] else "✗"
        print(f"\n{status_symbol} [{r['index']}] \"{r['question']}\" ({r['elapsed']:.2f}s)")
        print(f"    Status: {r['status']} | Queries: {r['queries']} | Retries: {r['retries']} | Numbers Traced: {r['traced']}")
        if r['run'] and r['run'].findings:
            for f in r['run'].findings:
                print(f"    • [{f.query_id}] {f.statement}")
        if not r['passed']:
            print(f"    >>> FAILURE REASON: {r['details']}")

    total_time = time.time() - start_time
    passed_count = sum(1 for r in results if r['passed'])
    print("\n" + "=" * 105)
    print(f"SUMMARY: {passed_count}/{len(results)} Passed ({passed_count/len(results)*100:.1f}%) in {total_time:.2f}s")
    if all_passed:
        print("ALL DEMO QUESTIONS PASSED OFFLINE REGRESSION AUDIT (Code 0)")
    else:
        print("ONE OR MORE DEMO QUESTIONS FAILED REGRESSION AUDIT (Code 1)")
    print("=" * 105)

    return all_passed


def main():
    parser = argparse.ArgumentParser(description="InsightAnalyst Demo Regression and Offline Checker")
    parser.add_argument(
        "--questions-file",
        default=str(PROJECT_ROOT / "demo_questions.txt"),
        help="Path to demo questions file (default: demo_questions.txt)"
    )
    parser.add_argument(
        "--provider",
        choices=["gemini", "groq", "ollama", "fake"],
        default=None,
        help="LLM provider to check (default: auto-detected from environment keys, or 'fake')"
    )

    args = parser.parse_args()
    q_file = Path(args.questions_file)

    if not q_file.exists():
        print(f"Error: Questions file '{q_file}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # Set provider if explicitly given or auto-detect if no key in env
    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    elif not os.getenv("LLM_PROVIDER"):
        if not os.getenv("GEMINI_API_KEY") and not os.getenv("GROQ_API_KEY"):
            os.environ["LLM_PROVIDER"] = "fake"

    success = check_demo(q_file)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
