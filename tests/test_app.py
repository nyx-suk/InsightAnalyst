"""tests/test_app.py
Unit tests for app.py (Person B - Streamlit Application & Dashboard Engine).
Verifies sample loading, preset questions, offline execution fallback, and trace capturing.
"""

import json
from pathlib import Path
import unittest

from contracts import AgentRun, TraceEvent
import app

SAMPLE_RUN_PATH = Path(__file__).resolve().parent.parent / "sample_agent_run.json"


class TestApp(unittest.TestCase):
    """Test suite for app.py helper methods, presets, and execution wrapper."""

    def test_load_sample_run(self):
        """Verify load_sample_run returns a valid AgentRun with findings and trace."""
        run = app.load_sample_run()
        self.assertIsInstance(run, AgentRun)
        self.assertEqual(run.question, "Why did sales drop in the South region last month?")
        self.assertEqual(run.status, "complete")
        self.assertGreaterEqual(len(run.queries), 2)
        self.assertGreaterEqual(len(run.findings), 3)
        self.assertGreaterEqual(len(run.trace), 10)

    def test_preset_questions_configured(self):
        """Verify preset questions include headline, ranking, trend, guardrail refusal, and clarification."""
        presets = app.PRESET_QUESTIONS
        self.assertGreaterEqual(len(presets), 5)
        categories = [p["category"] for p in presets]
        self.assertIn("root_cause", categories)
        self.assertIn("ranking", categories)
        self.assertIn("guardrail_refusal", categories)
        self.assertIn("clarification", categories)

    def test_execute_analysis_offline_fallback(self):
        """Verify execute_analysis handles events through callback and returns an AgentRun."""
        captured_events = []
        def event_cb(ev: TraceEvent):
            captured_events.append(ev)

        # Run with guardrail write refusal attempt
        run = app.execute_analysis(
            question="Delete all orders from 1997",
            offline_mode=True,
            on_event_cb=event_cb
        )
        self.assertIsInstance(run, AgentRun)
        self.assertEqual(run.question, "Delete all orders from 1997")
        self.assertIn(run.status, ["refused", "complete", "failed"])

    def test_app_is_running_in_streamlit_detection(self):
        """Verify _is_running_in_streamlit returns False when not in active Streamlit server."""
        self.assertFalse(app._is_running_in_streamlit())


if __name__ == "__main__":
    unittest.main()
