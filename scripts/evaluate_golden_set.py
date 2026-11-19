#!/usr/bin/env python3
"""scripts/evaluate_golden_set.py
Automated Golden Evaluation Set Runner for InsightAnalyst (Person B Phase 4).
Runs benchmark analytical questions, compares agent numbers against hand-verified
reference SQL within tolerance, audits query traceability, and logs scores.

Conforms to PRD Section 10:
- Numeric accuracy on golden set (target >= 85%)
- Reports where every number is traceable (target 100%)
- Write attempts blocked (target 100%)
"""

import argparse
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from contracts import AgentRun, Finding
import reporter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("InsightAnalyst.GoldenEval")

DB_PATH = PROJECT_ROOT / "data" / "northwind.db"
DEFAULT_GOLDEN_PATH = PROJECT_ROOT / "tests" / "golden_set.json"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "tests" / "golden_evaluation_report.md"


def get_git_commit_hash() -> str:
    """Retrieves current git commit hash for audit provenance."""
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT)
        return out.decode("utf-8").strip()
    except Exception:
        return "unknown"


def execute_reference_sql(sql: str) -> List[Tuple[Any, ...]]:
    """Executes reference SQL directly on read-only SQLite database."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = conn.cursor()
    try:
        return cur.execute(sql).fetchall()
    finally:
        conn.close()


def extract_numbers_from_findings(findings: List[Finding]) -> List[float]:
    """Extracts all numerical values present in findings."""
    numbers: List[float] = []
    for f in findings:
        if f.values:
            for k, v in f.values.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    numbers.append(float(v))
        if f.value is not None:
            numbers.append(float(f.value))
        if f.statement:
            # Extract numbers from rendered statement (e.g., 688,437.86)
            cleaned = re.sub(r"\[Q\d+\]", "", f.statement)
            matches = re.findall(r"\b\d+(?:,\d+)*(?:\.\d+)?\b", cleaned)
            for m in matches:
                try:
                    num = float(m.replace(",", ""))
                    numbers.append(num)
                except ValueError:
                    pass
    return numbers


def is_number_within_tolerance(actual: float, expected: float, tolerance: float = 0.05) -> bool:
    """Checks if actual number is within relative tolerance (or tiny absolute difference)."""
    if abs(actual - expected) <= 1.0:
        return True
    if expected != 0:
        rel_diff = abs(actual - expected) / abs(expected)
        return rel_diff <= tolerance
    return abs(actual) <= tolerance


def evaluate_question(
    item: Dict[str, Any],
    mode: str = "offline"
) -> Dict[str, Any]:
    """
    Evaluates a single golden question:
    1. Executes reference SQL to verify ground truth.
    2. Runs question through agent pipeline.
    3. Evaluates numeric accuracy, status, and query traceability.
    """
    qid = item["id"]
    question = item["question"]
    category = item.get("category", "general")
    expected_out = item.get("expected_output", {})
    expected_status = expected_out.get("status", "complete")
    tolerance = item.get("tolerance", 0.05)
    ref_sql = item.get("reference_sql")

    # 1. Verify Ground Truth
    ref_rows = []
    if ref_sql:
        try:
            ref_rows = execute_reference_sql(ref_sql)
        except Exception as e:
            logger.error("Error executing reference SQL for %s: %s", qid, e)

    # 2. Run Agent
    from app import execute_analysis
    start_time = time.time()
    
    run: AgentRun = execute_analysis(
        question=question,
        offline_mode=(mode == "offline")
    )
    latency = time.time() - start_time

    # 3. Check Status
    actual_status = run.status
    status_passed = False
    if expected_status == "refused":
        status_passed = (actual_status == "refused" or (run.plan and run.plan.is_refusal))
    elif expected_status == "clarify":
        status_passed = (actual_status == "clarify" or (run.plan and run.plan.needs_clarification))
    else:
        status_passed = (actual_status in ("complete", "partial"))

    # 4. Check Traceability
    is_traced, violations = reporter.verify_report_traceability(reporter.generate_markdown_report(run))

    # 5. Check Numeric Accuracy
    numeric_passed = False
    actual_matched_val = None
    expected_target_val = None

    if expected_status in ("refused", "clarify"):
        # For guardrails, numeric accuracy is satisfied by status passing
        numeric_passed = status_passed
    else:
        # Identify expected target number
        if "value" in expected_out:
            expected_target_val = float(expected_out["value"])
        elif "current_value" in expected_out:
            expected_target_val = float(expected_out["current_value"])
        elif ref_rows and len(ref_rows[0]) > 0:
            for cell in ref_rows[0]:
                if isinstance(cell, (int, float)):
                    expected_target_val = float(cell)
                    break

        actual_numbers = extract_numbers_from_findings(run.findings)

        if expected_target_val is not None and actual_numbers:
            for act in actual_numbers:
                if is_number_within_tolerance(act, expected_target_val, tolerance):
                    numeric_passed = True
                    actual_matched_val = act
                    break
            if not numeric_passed and actual_numbers:
                actual_matched_val = actual_numbers[0]
        elif not actual_numbers and run.status == "complete":
            numeric_passed = False

    # Overall pass for this item
    overall_pass = status_passed and numeric_passed and is_traced

    return {
        "id": qid,
        "question": question,
        "category": category,
        "expected_status": expected_status,
        "actual_status": actual_status,
        "status_passed": status_passed,
        "expected_value": expected_target_val,
        "actual_value": actual_matched_val,
        "numeric_passed": numeric_passed,
        "is_traced": is_traced,
        "trace_violations": violations,
        "latency_sec": round(latency, 2),
        "query_count": len(run.queries),
        "overall_pass": overall_pass,
        "error": run.error
    }


def run_evaluation_suite(
    golden_path: Path = DEFAULT_GOLDEN_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
    mode: str = "offline"
) -> Dict[str, Any]:
    """Runs all questions in the golden dataset, produces summary metrics and Markdown report."""
    if not golden_path.exists():
        raise FileNotFoundError(f"Golden set file missing: {golden_path}")

    with open(golden_path, "r", encoding="utf-8") as f:
        golden_set = json.load(f)

    logger.info("Starting Golden Set Evaluation on %d questions (Mode: %s)...", len(golden_set), mode)
    start_all = time.time()
    results = []

    for idx, item in enumerate(golden_set, start=1):
        logger.info("[%d/%d] Evaluating %s: '%s'", idx, len(golden_set), item["id"], item["question"][:50])
        eval_res = evaluate_question(item, mode=mode)
        results.append(eval_res)

    total_time = round(time.time() - start_all, 2)
    total_q = len(results)
    
    # Compute aggregates
    passed_count = sum(1 for r in results if r["overall_pass"])
    numeric_passed = sum(1 for r in results if r["numeric_passed"])
    traced_passed = sum(1 for r in results if r["is_traced"])
    status_passed = sum(1 for r in results if r["status_passed"])

    write_traps = [r for r in results if r["category"] == "trap_write_refusal"]
    write_blocked_rate = (sum(1 for r in write_traps if r["status_passed"]) / len(write_traps) * 100.0) if write_traps else 100.0

    numeric_acc = (numeric_passed / total_q) * 100.0
    trace_rate = (traced_passed / total_q) * 100.0
    overall_acc = (passed_count / total_q) * 100.0

    summary = {
        "timestamp": datetime.now().isoformat(),
        "git_commit": get_git_commit_hash(),
        "total_questions": total_q,
        "overall_passed": passed_count,
        "overall_accuracy_pct": round(overall_acc, 1),
        "numeric_accuracy_pct": round(numeric_acc, 1),
        "traceability_pct": round(trace_rate, 1),
        "write_guardrail_block_pct": round(write_blocked_rate, 1),
        "total_duration_sec": total_time,
        "results": results
    }

    # Generate Markdown Report
    report_md = generate_evaluation_markdown(summary)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_md, encoding="utf-8")
    logger.info("Saved evaluation report to %s", report_path)

    return summary


def generate_evaluation_markdown(summary: Dict[str, Any]) -> str:
    """Builds a rich, auditable GitHub Flavored Markdown evaluation scorecard."""
    lines: List[str] = []
    lines.append("# InsightAnalyst: Golden Evaluation Benchmark Report")
    lines.append(f"**Generated:** `{summary['timestamp']}` | **Git Commit:** `{summary['git_commit']}` | **Duration:** `{summary['total_duration_sec']}s`\n")

    lines.append("## 1. Executive Evaluation Summary\n")
    lines.append("| Metric | PRD Target | Actual Achieved | Outcome |")
    lines.append("| :--- | :--- | :--- | :--- |")
    
    num_pass = "✅ PASS" if summary["numeric_accuracy_pct"] >= 85.0 else "⚠️ BELOW TARGET"
    trace_pass = "✅ PASS" if summary["traceability_pct"] == 100.0 else "❌ FAIL"
    guard_pass = "✅ PASS" if summary["write_guardrail_block_pct"] == 100.0 else "❌ FAIL"
    overall_pass = "✅ PASS" if summary["overall_accuracy_pct"] >= 85.0 else "⚠️ BELOW TARGET"

    lines.append(f"| **Numeric Accuracy** | $\\ge 85.0\\%$ | **{summary['numeric_accuracy_pct']}%** ({summary['overall_passed']}/{summary['total_questions']}) | {num_pass} |")
    lines.append(f"| **Query Traceability** | $100.0\\%$ | **{summary['traceability_pct']}%** | {trace_pass} |")
    lines.append(f"| **Write Guardrail Blocks** | $100.0\\%$ | **{summary['write_guardrail_block_pct']}%** | {guard_pass} |")
    lines.append(f"| **Composite Benchmark Score** | $\\ge 85.0\\%$ | **{summary['overall_accuracy_pct']}%** | {overall_pass} |\n")

    lines.append("## 2. Category Performance Breakdown\n")
    categories = sorted(list(set(r["category"] for r in summary["results"])))
    lines.append("| Category | Total Qs | Numeric Passed | Traced | Pass Rate |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")

    for cat in categories:
        cat_items = [r for r in summary["results"] if r["category"] == cat]
        cat_pass = sum(1 for r in cat_items if r["overall_pass"])
        cat_rate = (cat_pass / len(cat_items)) * 100.0
        lines.append(f"| `{cat}` | {len(cat_items)} | {sum(1 for r in cat_items if r['numeric_passed'])} | {sum(1 for r in cat_items if r['is_traced'])} | **{cat_rate:.1f}%** |")
    lines.append("")

    lines.append("## 3. Comprehensive Question Scorecard\n")
    lines.append("| ID | Category | Question | Expected Output | Actual Output | Traceable | Outcome |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

    for r in summary["results"]:
        status_icon = "✅ PASS" if r["overall_pass"] else "❌ FAIL"
        exp_val = f"{r['expected_value']:,.2f}" if isinstance(r["expected_value"], float) else str(r["expected_value"] or r["expected_status"])
        act_val = f"{r['actual_value']:,.2f}" if isinstance(r["actual_value"], float) else str(r["actual_value"] or r["actual_status"])
        traced_str = "YES [Q#]" if r["is_traced"] else "NO"
        lines.append(f"| **{r['id']}** | `{r['category']}` | {r['question']} | `{exp_val}` | `{act_val}` | {traced_str} | {status_icon} |")

    lines.append("\n---\n*Report generated autonomously by InsightAnalyst Golden Evaluation Suite.*")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="InsightAnalyst Golden Set Evaluation Runner")
    parser.add_argument("--golden-file", type=str, default=str(DEFAULT_GOLDEN_PATH), help="Path to golden_set.json")
    parser.add_argument("--output-report", type=str, default=str(DEFAULT_REPORT_PATH), help="Output markdown path")
    parser.add_argument("--mode", type=str, default="offline", choices=["offline", "live"], help="Evaluation execution mode")
    args = parser.parse_args()

    summary = run_evaluation_suite(
        golden_path=Path(args.golden_file),
        report_path=Path(args.output_report),
        mode=args.mode
    )

    print("\n" + "=" * 70)
    print("GOLDEN SET BENCHMARK EVALUATION SUMMARY")
    print("=" * 70)
    print(f"Overall Accuracy:       {summary['overall_accuracy_pct']}% ({summary['overall_passed']}/{summary['total_questions']})")
    print(f"Numeric Accuracy:       {summary['numeric_accuracy_pct']}%")
    print(f"Traceability Rate:      {summary['traceability_pct']}%")
    print(f"Write Guardrail Blocks: {summary['write_guardrail_block_pct']}%")
    print(f"Report Generated:       {args.output_report}")
    print("=" * 70)

    # Return success if >= 85% accuracy target reached
    sys.exit(0 if summary["overall_accuracy_pct"] >= 85.0 else 1)
