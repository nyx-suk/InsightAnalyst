"""tests/test_llm.py
Unit tests for agent/llm.py using the fake provider as required by Prompt 2.1.
Verifies markdown code fence stripping, JSON parsing single-retry recovery,
and on-disk caching modes (readwrite, readonly, off).
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
import sys

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.llm import (
    generate,
    generate_json,
    LLMError,
    clean_markdown_fences,
    register_fake_response,
    clear_fake_responses
)


class TestLLMModule(unittest.TestCase):
    """Test suite for LLM wrapper and caching mechanics."""

    def setUp(self):
        # Configure environment for offline fake provider
        self.temp_cache_dir = tempfile.mkdtemp()
        os.environ["LLM_PROVIDER"] = "fake"
        os.environ["LLM_CACHE_DIR"] = self.temp_cache_dir
        os.environ["CACHE_MODE"] = "readwrite"
        clear_fake_responses()

    def tearDown(self):
        clear_fake_responses()
        shutil.rmtree(self.temp_cache_dir, ignore_errors=True)

    def test_markdown_fence_stripping(self):
        """Verifies that markdown fences (```json ... ```) are cleanly stripped."""
        prompt = "test_fence_stripping"
        raw_markdown = (
            "```json\n"
            "{\n"
            '  "metric": "revenue",\n'
            '  "value": 15000.50\n'
            "}\n"
            "```"
        )
        register_fake_response(prompt, raw_markdown)

        result = generate_json(prompt)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("metric"), "revenue")
        self.assertEqual(result.get("value"), 15000.50)

    def test_json_retry_recovery(self):
        """Verifies that generate_json retries once with a reminder when initial JSON parsing fails."""
        initial_bad_json = "I cannot fulfill this in json: {value: invalid"
        recovered_good_json = '{"metric": "revenue", "status": "recovered"}'

        # Register sequential responses: 1st call fails, 2nd call (with reminder) succeeds
        register_fake_response("test_retry_prompt", [initial_bad_json, recovered_good_json])

        result = generate_json("test_retry_prompt")
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("metric"), "revenue")
        self.assertEqual(result.get("status"), "recovered")

    def test_json_retry_exhaustion_raises_llmerror(self):
        """Verifies that if both attempts fail to produce valid JSON, LLMError is raised."""
        bad_json = "still not json at all"
        register_fake_response("always_fail_prompt", [bad_json, bad_json])

        with self.assertRaises(LLMError):
            generate_json("always_fail_prompt")

    def test_cache_readwrite_behavior(self):
        """Verifies that first call writes to cache, and second identical call returns cached result."""
        prompt = "cache_readwrite_prompt"
        register_fake_response(prompt, '{"first": "response"}')

        # First call: cache miss, written to disk
        resp1 = generate(prompt)
        self.assertEqual(resp1, '{"first": "response"}')

        # Change the fake response registration; if cached, it should still return the first response
        register_fake_response(prompt, '{"second": "overridden"}')
        resp2 = generate(prompt)
        self.assertEqual(resp2, '{"first": "response"}', "Second call should be a cache hit returning the initial response.")

    def test_cache_readonly_mode(self):
        """Verifies that readonly cache mode serves existing hits, and raises LLMError on a miss."""
        prompt_hit = "prompt_for_readonly_hit"
        prompt_miss = "prompt_for_readonly_miss"

        # 1. Populate cache in readwrite mode
        os.environ["CACHE_MODE"] = "readwrite"
        register_fake_response(prompt_hit, "cached value")
        generate(prompt_hit)

        # 2. Switch to readonly mode
        os.environ["CACHE_MODE"] = "readonly"

        # Hit should succeed
        hit_resp = generate(prompt_hit)
        self.assertEqual(hit_resp, "cached value")

        # Miss should raise LLMError
        register_fake_response(prompt_miss, "should not be called")
        with self.assertRaises(LLMError) as ctx:
            generate(prompt_miss)
        self.assertIn("Cache miss in readonly mode", str(ctx.exception))

    def test_cache_off_mode(self):
        """Verifies that CACHE_MODE='off' bypasses the disk cache completely."""
        os.environ["CACHE_MODE"] = "off"
        prompt = "cache_off_prompt"

        register_fake_response(prompt, ["call 1", "call 2"])

        resp1 = generate(prompt)
        resp2 = generate(prompt)

        self.assertEqual(resp1, "call 1")
        self.assertEqual(resp2, "call 2", "In CACHE_MODE='off', every call executes against the provider.")


if __name__ == "__main__":
    unittest.main()
