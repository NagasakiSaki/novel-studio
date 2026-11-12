"""Traceable local style extraction, unknown subjective metrics and copy guard."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_studio.core import Core, CoreError, READ, WRITE
from novel_studio.creative import template
from novel_studio.style_analysis import StyleAnalysisService, local_features, validate_analysis, copied_fragments
from novel_studio.host import ModelService


class StyleAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.core = Core.create(self.base, "文风分析合成工程", ROOT)
        self.service = StyleAnalysisService(self.core)
        cap = self.core.bind("writer", READ | WRITE)
        self.draft = self.core.dispatch(cap, "create_draft", {"text": "# 合成参考\n林舟推开钟楼的门。周岚没有回头。\n\n“你来早了。”她说。\n"}, "synthetic-style-source")
        self.source_id = "d_" + self.draft["document_id"]

    def test_local_numbers_are_defined_not_literary_scores(self):
        result = local_features("# 标题\n短句。另一句。\n\n他说：“等一等。”")
        self.assertEqual(result["kind"], "LOCAL_MEASUREMENT")
        self.assertIn("不是中文词汇丰富度", result["definitions"]["distinct_character_ratio"])
        self.assertNotIn("literary_score", result)
        self.assertGreater(result["sentences"]["samples"], 0)
        with self.assertRaises(CoreError):
            local_features("# 只有标题")

    def test_source_scope_ranges_and_original_hash(self):
        source = self.service.select(self.source_id)
        self.assertEqual(source["original_sha256"], self.draft["revision"])
        self.assertEqual(source["start_line"], 1)
        with self.assertRaises(CoreError):
            self.service.select("../../private.txt")
        with self.assertRaises(CoreError):
            self.service.select(self.source_id, 100, 101)

    def test_local_history_three_modes_and_binding_not_changed(self):
        profile = self.core.creative.save(template("人物驱动型"))
        self.core.creative.bind(profile["profile_id"])
        selection = self.service.select(self.source_id)
        first = self.service.analyze([selection])
        second = self.service.analyze([selection])
        self.assertEqual(second["version"], 2)
        self.assertEqual(self.service.get(first["analysis_id"])["analysis"], first["analysis"])
        for mode in ("light", "medium", "strong"):
            value = self.service.profile(first, mode)
            self.assertEqual(value["origin"]["sources"][0]["original_sha256"], self.draft["revision"])
            saved = self.core.creative.save(value)
            self.assertNotEqual(saved["profile_id"], profile["profile_id"])
        self.assertEqual(self.core.creative.context("writer")[0]["profile_id"], profile["profile_id"])

    def test_approved_import_can_be_used_without_picking_original_again(self):
        source = self.base / "合成风格参考.md"
        source.write_text("# 已导入的参考\n钟楼的灯慢慢暗了。风从海上吹来。", encoding="utf-8")
        prepared = self.core.materials.prepare(source, "research")
        preview = self.core.materials.preview(prepared, [], "retrieval", True)
        self.core.author_commit(preview["handle"], True)
        catalog = self.service.catalog()
        selected = next(row for row in catalog if row["source_id"].startswith("m_"))
        source.unlink()  # Synthetic fixture proves no source file reopening.
        result = self.service.select(selected["source_id"])
        self.assertIn("海上", result["text"])
        self.assertEqual(result["version"], 1)

    def test_auxiliary_fills_gaps_and_cannot_replace_primary(self):
        other = self.core.dispatch(self.core.bind("writer", READ | WRITE), "create_draft", {"text": "# 另一参考\n两个人在车站等雨停。"}, "synthetic-aux-source")
        first, second = self.service.select(self.source_id), self.service.select("d_" + other["document_id"])
        record = self.service.analyze([first, second])
        record["analysis"]["primary"]["fields"]["pace"] = "主参考的安静节奏"
        record["analysis"]["auxiliary"]["fields"]["pace"] = "辅助不得覆盖"
        record["analysis"]["auxiliary"]["fields"]["dialogue"] = "辅助补充对白差异"
        value = self.service.profile(record)
        self.assertEqual(value["fields"]["pace"], "主参考的安静节奏")
        self.assertEqual(value["fields"]["dialogue"], "辅助补充对白差异")

    def test_generated_verbatim_long_fragment_is_blocked(self):
        source = self.service.select(self.source_id)
        source["text"] = "这是一段专门用于测试长片段重复检测的合成句子，不能作为抽象文风直接复制进档案中。"
        record = self.service.analyze([source])
        record["analysis"]["primary"]["rules"] = source["text"]
        with self.assertRaises(CoreError):
            self.service.profile(record)
        value = self.service.profile(record, enforce_copy=False)
        self.assertTrue(copied_fragments(value, record["selections"]))

    def test_separate_model_analysis_and_preview_keep_primary_cid(self):
        selection = self.service.select(self.source_id)
        analysis = self.service.local_analysis([selection])
        requests = []
        class CLI:
            def version(self):
                return "1.5.0"
            def stream(self, args, cwd, prompt, notify=None, evidence=None):
                requests.append((args, cwd, prompt))
                schema = json.loads(args[args.index("--json-schema") + 1])
                output = {"text": "新的风格预览，只有合成内容。"} if set(schema["properties"]) == {"text"} else analysis
                return {"init": {"model": "synthetic-model"}, "result": {"structured_output": output, "conversation_id": str(uuid.uuid4()), "num_turns": 1, "usage": {}}, "latency_seconds": .01}
        models = ModelService(self.core, CLI())
        original = str(uuid.uuid4())
        models.save("writer", model_id="synthetic-model", cid=original)
        record = self.service.analyze([selection], model=True)
        profile = self.service.profile(record)
        result = self.service.preview(profile, "两个人在钟楼外等一场雨。")
        self.assertEqual(self.service.get_preview(result["preview_id"])["scene"], "两个人在钟楼外等一场雨。")
        self.assertEqual(models.preferences("writer")["cid"], original)
        self.assertEqual(len(requests), 2)
        for args, cwd, prompt in requests:
            settings = json.loads(args[args.index("--settings") + 1])
            self.assertIn("read_file(*)", settings["permissions"]["deny"])
            self.assertIn("write_file(*)", settings["permissions"]["deny"])
            self.assertIn("command(*)", settings["permissions"]["deny"])
        self.assertEqual(len(self.core.documents("writer")), 1)


if __name__ == "__main__":
    unittest.main()
