"""scripts/run_once.py
First end-to-end execution script adhering to Prompt 2.5.
Takes a business question from command line, retrieves grounded context, creates a plan,
resolves relative time window, executes the first query via Person B's executor (or stub),
and prints the plan, the SQL, and the raw result.
"""

import argparse
from datetime import date
import json
import logging
import os
from pathlib import Path
import sys

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import Plan, QueryResult
from knowledge.retrieve import retrieve
from agent.planner import plan
from agent.timewindow import resolve_window
import agent.llm as llm

logger = logging.getLogger("InsightAnalyst.RunOnce")

# Default max date from Person B's dataset facts (person_a_facts.md)
DATASET_MAX_DATE = date(2023, 10, 28)


def get_executor():
    """Attempts to import Person B's executor.py. Falls back to stub if not implemented."""
    try:
        import executor
        if hasattr(executor, "execute"):
            def run_exec(sql: str, query_id: str) -> QueryResult:
                # Try execute(sql, query_id) then execute(query_id, sql)
                try:
                    res = executor.execute(sql, query_id)
                except TypeError:
                    res = executor.execute(query_id, sql)
                if isinstance(res, QueryResult):
                    return res
                if isinstance(res, dict):
                    return QueryResult.from_dict(res)
                return QueryResult(query_id=query_id, sql=sql, error="Unknown executor return type")
            return run_exec, "Person B executor.py"
    except ImportError:
        pass

    # Stub executor as required by Prompt 2.5
    def stub_executor(sql: str, query_id: str) -> QueryResult:
        return QueryResult(
            query_id=query_id,
            sql=sql,
            columns=["Region", "OrderMonth", "Revenue", "OrderCount"],
            rows=[
                ("South", "2023-09", 14250.00, 38),
                ("South", "2023-08", 22100.00, 52)
            ],
            row_count=2,
            error=None
        )

    return stub_executor, "STUB (Person B's executor.py is not yet implemented)"


def run_once(question: str) -> None:
    """Executes the single-question planning and execution pipeline."""
    print("=" * 80)
    print("INSIGHTANALYST: SINGLE-QUERY PIPELINE (Prompt 2.5)")
    print("=" * 80)
    print(f"QUESTION: \"{question}\"\n")

    # Step 1: Retrieve context chunks
    print("[1/4] Retrieving grounded knowledge chunks...")
    chunks = retrieve(question, k=6)
    print(f"      Retrieved {len(chunks)} chunks:")
    for c in chunks:
        print(f"      - [{c.get('type')}] {c.get('id')}")

    # Step 2: Create structured plan
    print("\n[2/4] Generating plan via planner...")
    p = plan(question, chunks, llm)
    print("      Plan generated successfully.")

    if p.is_refusal:
        print("\n" + "!" * 80)
        print("REQUEST REFUSED BY SAFETY GUARD:")
        print(f"Reason: {p.refusal_reason}")
        print("!" * 80)
        return

    if p.needs_clarification:
        print("\n" + "?" * 80)
        print("CLARIFICATION REQUIRED:")
        print(f"Question: {p.clarification_message}")
        print("?" * 80)
        return

    # Step 3: Resolve relative time window
    print("\n[3/4] Resolving relative time window against dataset max date...")
    try:
        # Check if database has a live max date
        data_max = DATASET_MAX_DATE
        db_path = Path("data/northwind.db")
        if db_path.exists():
            import sqlite3
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()
            val = cur.execute("SELECT MAX(OrderDate) FROM Orders").fetchone()[0]
            if val:
                from agent.timewindow import _parse_date
                data_max = _parse_date(val)
            conn.close()

        start_date, end_date = resolve_window(p.time_window, data_max)
        print(f"      Dataset Max Date: {data_max}")
        print(f"      Resolved Target Window: {start_date} to {end_date}")
    except Exception as e:
        print(f"      Warning: could not resolve time window: {e}")
        start_date, end_date = None, None

    # Step 4: Execute first query
    print("\n[4/4] Executing first query (Q1)...")
    executor_fn, executor_status = get_executor()
    print(f"      Executor Status: {executor_status}")

    if not p.query_list:
        print("      No queries present in plan.")
        return

    first_query = p.query_list[0]
    result = executor_fn(first_query.sql, first_query.query_id)

    # Print Report Outputs
    print("\n" + "=" * 80)
    print("PIPELINE EXECUTION OUTPUT")
    print("=" * 80)

    print("\n--- A. STRUCTURED PLAN ---")
    plan_dict = p.to_dict()
    print(json.dumps(plan_dict, indent=2))

    print("\n--- B. EXECUTED SQL (Q1) ---")
    print(f"Purpose : {first_query.purpose}")
    print(f"Query ID: {first_query.query_id}")
    print(f"SQL     :\n{first_query.sql}")

    print("\n--- C. RAW QUERY RESULT ---")
    if result.error:
        print(f"ERROR: {result.error}")
    else:
        print(f"Columns: {result.columns}")
        print(f"Rows ({result.row_count} total):")
        for r in result.rows:
            print(f"  {r}")

    print("\n" + "=" * 80)
    print("END OF RUN")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="InsightAnalyst First End-to-End Single Query Script")
    parser.add_argument(
        "question",
        nargs="?",
        default="Why did sales drop in the South region last month?",
        help="Business question to answer (default: headline South region drop question)"
    )
    args = parser.parse_args()
    run_once(args.question)


if __name__ == "__main__":
    main()
