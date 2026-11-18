"""scripts/warm_cache.py
Cache pre-warmer for InsightAnalyst demo questions (Prompt 4.2).

Runs demo questions once with a live provider (or fake provider if offline/no key)
in CACHE_MODE=readwrite so responses are saved to .cache/llm/.
This enables scripts/check_demo.py to run completely offline without network or API quota.
"""

import argparse
import logging
import os
from pathlib import Path
import sys
import time

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.loop import run_agent
from executor import execute_query
from validator import validate
from knowledge.retrieve import retrieve
import agent.llm as llm

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("InsightAnalyst.WarmCache")


def load_demo_questions(filepath: Path) -> list[str]:
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


def warm_cache_for_questions(
    questions: list[str],
    provider: str | None = None,
    canned_file: str | None = None
) -> bool:
    """Runs each question through the agent loop with CACHE_MODE=readwrite.
    
    Returns:
        True if all questions completed successfully, False otherwise.
    """
    # Force readwrite cache mode
    os.environ["CACHE_MODE"] = "readwrite"

    # Select provider
    if provider:
        os.environ["LLM_PROVIDER"] = provider
    else:
        # If no provider explicitly specified, check for available live keys
        if os.getenv("GEMINI_API_KEY"):
            os.environ["LLM_PROVIDER"] = "gemini"
        elif os.getenv("GROQ_API_KEY"):
            os.environ["LLM_PROVIDER"] = "groq"
        else:
            logger.info("No live API keys detected in environment. Using 'fake' provider with canned plans.")
            os.environ["LLM_PROVIDER"] = "fake"

    curr_provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    if curr_provider == "fake":
        canned_path = canned_file or str(PROJECT_ROOT / "tests" / "canned_plans.json")
        os.environ["FAKE_LLM_FILE"] = canned_path
        logger.info(f"Using fake provider with canned plans: {canned_path}")
    else:
        logger.info(f"Using live LLM provider: {curr_provider}")

    print("=" * 80)
    print("INSIGHTANALYST: WARMING DEMO CACHE (Prompt 4.2)")
    print(f"Provider: {curr_provider} | Cache Mode: readwrite | Cache Dir: .cache/llm/")
    print(f"Total Questions: {len(questions)}")
    print("=" * 80)

    all_succeeded = True
    start_time = time.time()

    for idx, question in enumerate(questions, start=1):
        print(f"\n[{idx}/{len(questions)}] Warming cache for: \"{question}\"")
        try:
            run = run_agent(
                question=question,
                retriever=retrieve,
                llm=llm,
                executor=execute_query,
                validator=validate
            )

            retries = len([e for e in run.trace if e.event == "retry"])
            print(f"    Status: {run.status}")
            print(f"    Queries Executed: {len(run.queries)} | Retries: {retries}")
            print(f"    Findings Generated: {len(run.findings)}")
            for f in run.findings:
                print(f"      - [{f.query_id}] {f.statement}")

            if run.status != "complete":
                print(f"    WARNING: Run did not reach complete status (got '{run.status}'). Error: {run.error}")
                all_succeeded = False
            else:
                print("    Success: Cached responses saved to .cache/llm/.")

        except Exception as e:
            logger.error(f"Error warming question '{question}': {e}", exc_info=True)
            all_succeeded = False

    elapsed = time.time() - start_time
    cache_dir = Path(".cache/llm")
    cached_count = len(list(cache_dir.glob("*.json"))) if cache_dir.exists() else 0

    print("\n" + "=" * 80)
    print("CACHE WARMING SUMMARY")
    print(f"Elapsed Time: {elapsed:.2f}s")
    print(f"Total Cached Files in {cache_dir}: {cached_count}")
    print(f"Status: {'ALL DEMO QUESTIONS WARMED' if all_succeeded else 'ONE OR MORE QUESTIONS FAILED'}")
    print("=" * 80)

    return all_succeeded


def main():
    parser = argparse.ArgumentParser(description="InsightAnalyst Demo Cache Warmer")
    parser.add_argument(
        "--questions-file",
        default=str(PROJECT_ROOT / "demo_questions.txt"),
        help="Path to demo questions file (default: demo_questions.txt)"
    )
    parser.add_argument(
        "--provider",
        choices=["gemini", "groq", "ollama", "fake"],
        default=None,
        help="LLM provider to use (default: auto-detected from environment keys, or 'fake')"
    )
    parser.add_argument(
        "--canned-file",
        default=None,
        help="Path to canned plans JSON if using 'fake' provider"
    )

    args = parser.parse_args()

    q_file = Path(args.questions_file)
    if not q_file.exists():
        print(f"Error: Questions file '{q_file}' does not exist.", file=sys.stderr)
        sys.exit(1)

    questions = load_demo_questions(q_file)
    if not questions:
        print("Error: No questions found to warm.", file=sys.stderr)
        sys.exit(1)

    success = warm_cache_for_questions(
        questions=questions,
        provider=args.provider,
        canned_file=args.canned_file
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
