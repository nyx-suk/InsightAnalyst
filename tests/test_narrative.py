"""tests/test_narrative.py
Unit tests for agent/narrative.py (Prompt 3.5).
Tests zero-hallucination narrative generation with placeholders:
- Regex detection of naked digits outside placeholders
- Safe Python filling of placeholders
- Regeneration on naked digit detection
- Deterministic code fallback when digits persist
"""

import unittest
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import Finding
from agent.narrative import (
    has_naked_digits,
    fill_placeholders,
    build_fallback_narrative,
    generate_narrative,
    NarrativeResult
)


class FakeNarrativeLLM:
    """Mock LLM returning sequential text responses."""
    def __init__(self, responses):
        self.responses = list(responses) if isinstance(responses, list) else [responses]
        self.calls = []

    def generate(self, prompt: str, temperature: float = 0.0) -> str:
        self.calls.append(prompt)
        if self.responses:
            return self.responses.pop(0)
        return ""


class TestNarrativePrompt35(unittest.TestCase):
    """Test suite for Prompt 3.5 narrative generation."""

    def setUp(self):
        self.findings = [
            Finding(
                id="F_headline",
                kind="headline",
                template="Overall revenue fell by {total_abs_change} ({total_pct_change}%) [{query_id}]",
                values={
                    "total_abs_change": 2295.0,
                    "total_pct_change": -86.44,
                    "query_id": "Q1"
                },
                query_id="Q1"
            ),
            Finding(
                id="driver_1",
                kind="driver",
                template="{dimension_value} revenue fell by {abs_change} ({pct_change}%), driving {contribution_pct}% of total drop [{query_id}]",
                values={
                    "dimension_value": "South",
                    "abs_change": 2295.0,
                    "pct_change": -86.44,
                    "contribution_pct": 100.0,
                    "query_id": "Q1"
                },
                query_id="Q1"
            )
        ]

    def test_has_naked_digits(self):
        """Regex check: Only digits inside {...} are permitted."""
        # Clean: Digits appear ONLY inside placeholder tags
        clean_1 = "Overall revenue fell by {F_headline.total_abs_change} ({driver_1.pct_change}) [{F_headline.query_id}]."
        clean_2 = "Top driver is {driver_1.dimension_value} accounting for {driver_1.contribution_pct} of drop [{Q1.val}]."
        self.assertFalse(has_naked_digits(clean_1))
        self.assertFalse(has_naked_digits(clean_2))

        # Naked digits present outside placeholders
        bad_1 = "Overall revenue fell by 2295.0 dollars [{Q1}]."
        bad_2 = "South revenue dropped -86.4% in September."
        bad_3 = "Sales in 2023 were lower."
        self.assertTrue(has_naked_digits(bad_1))
        self.assertTrue(has_naked_digits(bad_2))
        self.assertTrue(has_naked_digits(bad_3))

    def test_fill_placeholders_code_execution(self):
        """Placeholders are filled deterministically by Python code."""
        tmpl = (
            "Overall revenue dropped {F_headline.total_abs_change} "
            "driven primarily by {driver_1.dimension_value} ({driver_1.pct_change}) "
            "[{F_headline.query_id}]."
        )
        filled, used = fill_placeholders(tmpl, self.findings)
        
        self.assertIn("2,295.00", filled)
        self.assertIn("South", filled)
        self.assertIn("-86.4%", filled)
        self.assertIn("[Q1]", filled)
        self.assertIn("F_headline.total_abs_change", used)
        self.assertIn("driver_1.dimension_value", used)
        self.assertIn("driver_1.pct_change", used)

    def test_generate_narrative_clean_on_first_try(self):
        """LLM returns clean placeholder narrative on attempt 1."""
        llm_reply = (
            "Overall sales fell by {F_headline.total_abs_change} ({F_headline.total_pct_change}) [{F_headline.query_id}]. "
            "The decline was heavily concentrated in {driver_1.dimension_value} [{driver_1.query_id}]."
        )
        fake_llm = FakeNarrativeLLM([llm_reply])

        res = generate_narrative(self.findings, "Why did sales drop?", llm=fake_llm)

        self.assertFalse(res.fallback_used)
        self.assertEqual(len(fake_llm.calls), 1)
        self.assertIn("2,295.00", res.filled_text)
        self.assertIn("South", res.filled_text)
        self.assertIn("[Q1]", res.filled_text)

    def test_generate_narrative_regenerate_on_naked_digits(self):
        """LLM hallucinates naked digits on attempt 1, but fixes them on attempt 2 after reminder."""
        bad_reply = "Sales fell by 2295 dollars in South region."
        good_reply = "Sales fell by {F_headline.total_abs_change} in {driver_1.dimension_value} [{F_headline.query_id}]."
        fake_llm = FakeNarrativeLLM([bad_reply, good_reply])

        res = generate_narrative(self.findings, "Why did sales drop?", llm=fake_llm)

        # Verified that 2 calls occurred
        self.assertEqual(len(fake_llm.calls), 2)
        # Second call contained the reminder error message
        self.assertIn("ERROR IN PREVIOUS ATTEMPT", fake_llm.calls[1])
        # Did not fall back to code template because attempt 2 succeeded
        self.assertFalse(res.fallback_used)
        self.assertIn("2,295.00", res.filled_text)
        self.assertIn("South", res.filled_text)

    def test_generate_narrative_fallback_when_digits_persist(self):
        """LLM includes naked digits in both attempts; falls back to code summary."""
        bad_reply_1 = "Revenue dropped 2295 dollars."
        bad_reply_2 = "Still dropped 2295 dollars."
        fake_llm = FakeNarrativeLLM([bad_reply_1, bad_reply_2])

        res = generate_narrative(self.findings, "Why did sales drop?", llm=fake_llm)

        self.assertTrue(res.fallback_used)
        self.assertEqual(len(fake_llm.calls), 2)
        # Fallback text correctly generated from findings' render()
        self.assertIn("Overall revenue fell by 2295.0 (-86.44%) [Q1]", res.filled_text)
        self.assertIn("South revenue fell by 2295.0 (-86.44%)", res.filled_text)


if __name__ == "__main__":
    unittest.main()
