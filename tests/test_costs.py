"""Synthetic prices only; no vendor rates or billable API requests."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_studio.core import Core, CoreError
from novel_studio.cache_policy import estimate_cost, merge_usage, normalize_usage
from novel_studio.costs import CostService

PRICE = {"source": "合成价格，不是当前官方费率", "as_of": "2026-11-05", "currency": "USD", "input_per_million": "2", "read_per_million": ".2", "output_per_million": "8", "write_5m_per_million": "2.5", "write_1h_per_million": "4", "write_30m_per_million": "3", "write_12h_per_million": "5"}


class CostTests(unittest.TestCase):
    def test_mixed_ttl_cost_is_separate_and_not_double_counted(self):
        usage = normalize_usage("anthropic", {"input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 2_000_000,
            "output_tokens": 1_000_000, "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 1_000_000}})
        self.assertEqual(estimate_cost(usage, PRICE)["estimated_cost"], "16.7")
        merged = merge_usage([usage, usage])
        self.assertEqual(estimate_cost(merged, PRICE)["estimated_cost"], "33.4")
        unknown = {**usage, "cache_write_by_ttl": {"ephemeral_5m_input_tokens": 1_000_000}}
        self.assertEqual(estimate_cost(unknown, PRICE)["reason"], "WRITE_TTL_SCOPE_UNKNOWN")
        self.assertNotIn("cache_write_by_ttl", merge_usage([usage, unknown]))
        legacy = {**PRICE, "write_per_million": "2.5", "write_rate_scope": "uniform_all_ttl"}
        self.assertIsNotNone(estimate_cost(unknown, legacy)["estimated_cost"])

    def test_openai_ttl_missing_prices_and_invalid_counts_are_unknown(self):
        row = normalize_usage("openai", {"input_tokens": 3_000_000, "input_tokens_details": {"cached_tokens": 1_000_000, "cache_write_tokens": 1_000_000, "cache_write_tokens_details": {"ttl_30m_tokens": 500_000, "ttl_12h_tokens": 500_000}}, "output_tokens": 1_000_000})
        self.assertEqual(estimate_cost(row, PRICE)["estimated_cost"], "14.2")
        self.assertIsNone(estimate_cost(row, {**PRICE, "write_12h_per_million": None})["estimated_cost"])
        self.assertIsNone(estimate_cost(normalize_usage("antigravity", {}), PRICE)["estimated_cost"])
        self.assertIsNone(estimate_cost({**row, "output_tokens": -1}, PRICE)["estimated_cost"])

    def test_project_price_versions_missing_and_failed_usage_never_count_as_free(self):
        with tempfile.TemporaryDirectory() as folder:
            core = Core.create(Path(folder), "费用合成工程", ROOT)
            service = CostService(core)
            service.save("anthropic", "synthetic-model", PRICE)
            with self.assertRaises(CoreError):
                service.save("anthropic", "synthetic-model", {**PRICE, "input_per_million": "NaN"})
            usage = normalize_usage("anthropic", {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})
            core._state(lambda s: s.update(api_runs=[{"run_id": "synthetic-one", "provider": "anthropic", "role": "writer", "model_id": "synthetic-model", "usage": usage, "status": "PASS"}, {"run_id": "synthetic-two", "provider": "anthropic", "role": "writer", "model_id": "synthetic-model", "usage": normalize_usage("anthropic", {}), "status": "FAIL", "diagnostic": True}]))
            report = service.report("writer")
            self.assertEqual(report["groups"]["USD"]["known_subtotal"], "10")
            self.assertEqual(report["groups"]["UNKNOWN"]["unknown"], 1)
            self.assertIsNone(report["rows"][1]["estimated_cost"])
            self.assertFalse(service.report("editor")["rows"])
            service.save("anthropic", "synthetic-model", {**PRICE, "input_per_million": "3"})
            self.assertEqual(service.report("writer")["groups"]["USD"]["known_subtotal"], "11")

    def test_gemini_thinking_is_billed_once_and_missing_is_unknown(self):
        row = normalize_usage("gemini", {"total_input_tokens": 1_000_000, "total_cached_tokens": 0, "total_output_tokens": 1_000_000, "total_thought_tokens": 500_000}, endpoint="interactions")
        self.assertEqual(row["output_tokens"], 1_000_000)
        self.assertEqual(estimate_cost(row, PRICE)["estimated_cost"], "14")
        self.assertEqual(estimate_cost(merge_usage([row, row]), PRICE)["estimated_cost"], "28")
        missing = normalize_usage("gemini", {"total_input_tokens": 1_000_000, "total_cached_tokens": 0, "total_output_tokens": 1_000_000}, endpoint="interactions")
        self.assertIsNone(estimate_cost(missing, PRICE)["estimated_cost"])


class CostUITests(unittest.TestCase):
    def test_local_price_form_and_unknown_ledger(self):
        try:
            from PySide6.QtWidgets import QApplication
            from novel_studio.desktop.cost_ui import CostDialog
            from novel_studio.desktop.theme import apply_theme
        except ImportError:
            self.skipTest("Qt developer environment not installed")
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            core = Core.create(Path(folder), "费用界面合成作品", ROOT)
            dialog = CostDialog(core)
            self.addCleanup(dialog.deleteLater)
            self.assertIn("费用未知", dialog.summary.toPlainText())
            dialog.model.setText("synthetic-model")
            dialog.source.setText(PRICE["source"])
            dialog.rates["input_per_million"].setText("2")
            dialog.save()
            dialog.source.clear()
            dialog.load()
            self.assertEqual(dialog.source.text(), PRICE["source"])
            self.assertFalse(dialog.uniform.isChecked())
            for theme in ("light", "dark"):
                apply_theme(app, theme)
                dialog.show()
                app.processEvents()
            dialog.hide()


if __name__ == "__main__":
    unittest.main()
