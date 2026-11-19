"""tests/test_retrieval.py
Retrieval acceptance check as specified in Prompt 1.6.
Tests 10 sample business questions against knowledge retrieval and reports the actual hit rate.
"""

import sys
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge.retrieve import retrieve

TEST_CASES = [
    {
        "question": "Why did sales drop in the South region last month?",
        "expected_ids": ["metric_revenue", "rule_region_mapping", "rule_time_anchoring"]
    },
    {
        "question": "Which product category drove the most revenue in August 2023?",
        "expected_ids": ["metric_revenue", "table_categories", "table_products"]
    },
    {
        "question": "How many orders were placed by European customers last month?",
        "expected_ids": ["metric_order_count", "rule_region_mapping", "table_orders"]
    },
    {
        "question": "What was the average order value in the North region?",
        "expected_ids": ["metric_average_order_value", "rule_region_mapping"]
    },
    {
        "question": "What was our total units sold in September 2023?",
        "expected_ids": ["metric_units_sold", "rule_time_anchoring"]
    },
    {
        "question": "How have beverage sales changed over the last two months?",
        "expected_ids": ["metric_revenue", "table_categories", "rule_time_anchoring"]
    },
    {
        "question": "Who are our top customers by total sales?",
        "expected_ids": ["table_customers", "metric_revenue"]
    },
    {
        "question": "Does the revenue calculation include freight shipping charges?",
        "expected_ids": ["rule_revenue_formula", "metric_revenue", "table_orders"]
    },
    {
        "question": "How are customer countries mapped to sales regions?",
        "expected_ids": ["rule_region_mapping", "table_customers"]
    },
    {
        "question": "What was the order count and volume for Brazil?",
        "expected_ids": ["metric_order_count", "metric_units_sold", "rule_region_mapping"]
    }
]


def run_retrieval_check():
    print("=" * 80)
    print("INSIGHTANALYST RETRIEVAL ACCURACY REPORT (10 Sample Questions)")
    print("=" * 80)

    total_expected = 0
    total_found = 0

    print(f"{'#':<3} | {'Question':<45} | {'Expected':<20} | {'Found':<20} | {'Status'}")
    print("-" * 105)

    for idx, case in enumerate(TEST_CASES, 1):
        q = case["question"]
        expected = set(case["expected_ids"])
        total_expected += len(expected)

        results = retrieve(q, k=6)
        retrieved_ids = set([r["id"] for r in results])

        found = expected.intersection(retrieved_ids)
        missed = expected.difference(retrieved_ids)
        total_found += len(found)

        status = "PASS" if not missed else ("PARTIAL" if found else "FAIL")
        q_disp = (q[:42] + "...") if len(q) > 45 else q
        exp_disp = ",".join(list(expected)[:2])
        fnd_disp = ",".join(list(found)[:2]) if found else "NONE"

        print(f"{idx:<3} | {q_disp:<45} | {exp_disp:<20} | {fnd_disp:<20} | {status}")
        if missed:
            print(f"    └── Missed IDs: {list(missed)}")

    hit_rate = (total_found / total_expected) * 100 if total_expected else 0.0
    print("-" * 105)
    print(f"Overall Retrieval Hit Rate: {total_found}/{total_expected} ({hit_rate:.1f}%)")
    print("=" * 80)

    # Acceptance test condition from Prompt 1.6:
    # "A revenue question returns the revenue definition and the region rule in the top results."
    test_q = "Why did sales drop in the South region last month?"
    test_chunks = retrieve(test_q, k=6)
    test_ids = [c["id"] for c in test_chunks]
    assert "metric_revenue" in test_ids, "Acceptance Test Failed: 'metric_revenue' not in top results"
    assert "rule_region_mapping" in test_ids, "Acceptance Test Failed: 'rule_region_mapping' not in top results"
    print("Acceptance Test PASSED: Headline question returns 'metric_revenue' and 'rule_region_mapping'.")
    return hit_rate


class TestRetrievalAcceptance(unittest.TestCase):
    def test_prompt_1_6_retrieval_acceptance(self):
        """Prompt 1.6 Acceptance: Revenue question returns revenue definition and region rule."""
        test_q = "Why did sales drop in the South region last month?"
        test_chunks = retrieve(test_q, k=6)
        test_ids = [c["id"] for c in test_chunks]
        self.assertIn("metric_revenue", test_ids)
        self.assertIn("rule_region_mapping", test_ids)


if __name__ == "__main__":
    unittest.main()
