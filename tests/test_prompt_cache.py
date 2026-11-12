"""Client-visible prefix and accounting contracts, not provider hit-rate tests."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_studio.core import Core, CoreError
from novel_studio.prompting import compile_prompt, changes, assemble
from novel_studio.cache_policy import normalize_usage, merge_usage, estimate_cost, capabilities, validate_policy


class CompilerTests(unittest.TestCase):
    def test_request_and_snapshot_are_after_stable_prefix(self):
        common = dict(role="writer", project_id="synthetic", always="长期规则：允许慢热", profile={"voice": "冷静"})
        a = compile_prompt(**common, current={"snapshot": "uuid-1"}, request="第一轮")
        b = compile_prompt(**common, current={"snapshot": "uuid-2"}, request="第二轮")
        self.assertEqual(a.stable_text, b.stable_text)
        self.assertNotEqual(a.dynamic_text, b.dynamic_text)
        self.assertNotIn("uuid-1", a.stable_text)
        self.assertTrue(a.cli_text().endswith("第一轮"))
        self.assertEqual(changes(changes({}, a), b)["changed_components"], [])

    def test_sorting_semantics_and_configuration_separation(self):
        a = compile_prompt(role="editor", project_id="s", project_rules=[{"name": "b", "text": "乙"}, {"name": "a", "text": "甲"}],
                           tools=[{"name": "z", "schema": {"y": 1, "x": 2}}, {"name": "a"}], configuration={"model_id": "m", "api_key": "SECRET"})
        b = compile_prompt(role="editor", project_id="s", project_rules=[{"text": "甲", "name": "a"}, {"text": "乙", "name": "b"}],
                           tools=[{"name": "a"}, {"schema": {"x": 2, "y": 1}, "name": "z"}], configuration={"model_id": "m"})
        self.assertEqual(a.fingerprints, b.fingerprints)
        self.assertNotIn("SECRET", a.cli_text())
        c = compile_prompt(role="editor", project_id="s", profile={"strictness": 3})
        self.assertIn("profile", changes(changes({}, a), c)["changed_components"])

    def test_no_silent_history_or_rules_truncation(self):
        rules = "规" * 340000
        compiled = compile_prompt(role='writer', project_id='s', always=rules)
        self.assertIn(rules, compiled.stable_text)
        with patch('novel_studio.prompting.MAX_PROMPT_BYTES', 1_000_000):
            with self.assertRaises(CoreError) as error:
                compile_prompt(role='writer', project_id='s', always=rules)
        self.assertEqual(error.exception.code, 'CONTEXT_LIMIT')

    def test_real_synthetic_snapshot_compilation(self):
        with tempfile.TemporaryDirectory() as folder:
            core = Core.create(Path(folder), "上下文合成工程", ROOT)
            core.snapshot()
            a, _ = assemble(core, "writer", "仅讨论")
            self.assertIn("SNAPSHOT.json", a.dynamic_text)
            api, _ = assemble(core, "writer", "仅讨论", api=True)
            self.assertGreater(len(json.loads(api.stable_text)["role_rules"]), 100)
            self.assertNotIn(str(core.project), api.stable_text)


class AccountingTests(unittest.TestCase):
    def test_missing_is_not_zero_and_booleans_not_counts(self):
        for provider in ("openai", "anthropic", "gemini", "antigravity"):
            row = normalize_usage(provider, {"input_tokens": True})
            self.assertIsNone(row["input_tokens"])
            self.assertIsNone(row["cache_read_share"])
            self.assertIsNone(row["provider_hit_rate"])

    def test_openai_modern_and_legacy_distinct(self):
        data = {"input_tokens": 100, "output_tokens": 10, "input_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 20}}
        a = normalize_usage("openai", data)
        self.assertEqual(a["uncached_input_tokens"], 40)
        self.assertEqual(a["cache_read_share"], .4)
        b = normalize_usage("openai", {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 40}}, legacy_openai=True)
        self.assertEqual(b["uncached_input_tokens"], 60)
        self.assertIsNone(b["cache_write_tokens"])
        self.assertIsNone(normalize_usage("openai", {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 40}})["uncached_input_tokens"])

    def test_anthropic_ordinary_is_not_total_and_thinking_not_added(self):
        row = normalize_usage("anthropic", {"input_tokens": 20, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 30,
                                             "output_tokens": 12, "output_tokens_details": {"reasoning_tokens": 5}})
        self.assertEqual(row["input_tokens"], 100)
        self.assertEqual(row["total_tokens"], 112)

    def test_gemini_interfaces_are_not_interchangeable(self):
        a = normalize_usage("gemini", {"promptTokenCount": 100, "cachedContentTokenCount": 40}, endpoint="generateContent")
        b = normalize_usage("gemini", {"total_input_tokens": 100, "total_cached_tokens": 40}, endpoint="interactions")
        self.assertEqual(a["uncached_input_tokens"], b["uncached_input_tokens"])
        self.assertIsNone(normalize_usage("gemini", {"promptTokenCount": 100}, endpoint="interactions")["input_tokens"])

    def test_inconsistent_counts_and_merge_unknowns(self):
        bad = normalize_usage("openai", {"input_tokens": 2, "input_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 0}})
        self.assertIsNone(bad["cache_read_share"])
        self.assertIsNone(bad["uncached_input_tokens"])
        known = normalize_usage("anthropic", {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})
        self.assertEqual(merge_usage([known, known])["input_tokens"], 20)
        self.assertIsNone(merge_usage([known, normalize_usage("anthropic", {})])["input_tokens"])

    def test_cost_requires_known_buckets_and_price_source(self):
        row = normalize_usage("anthropic", {"input_tokens": 1000000, "cache_read_input_tokens": 1000000, "cache_creation_input_tokens": 1000000, "output_tokens": 1000000})
        price = {"source": "synthetic example; not current pricing", "as_of": "2026-11-05", "currency": "USD", "input_per_million": 2, "read_per_million": .2, "write_per_million": 2.5, "output_per_million": 8, "write_rate_scope": "uniform_all_ttl"}
        self.assertEqual(estimate_cost(row, price)["estimated_cost"], "12.7")
        self.assertIsNone(estimate_cost(normalize_usage("anthropic", {}), price)["estimated_cost"])
        self.assertIsNone(estimate_cost(row, {})["estimated_cost"])

    def test_policy_does_not_silently_fall_back(self):
        for provider in ("antigravity", "gemini", "openai", "anthropic"):
            validate_policy(provider, {}, capabilities(provider))
            with self.assertRaises(CoreError):
                validate_policy(provider, {"mode": "explicit", "ttl": "1h"}, capabilities(provider))
        anth = capabilities("anthropic", verified=True)
        self.assertEqual(validate_policy("anthropic", {"mode": "explicit", "ttl": "1h"}, anth)["ttl"], "1h")
        with self.assertRaises(CoreError):
            validate_policy("anthropic", {"mode": "implicit"}, anth)


if __name__ == "__main__":
    unittest.main()
