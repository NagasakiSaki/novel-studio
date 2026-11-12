"""Independent editor settings and exact citations, never literary score tests."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_studio.core import Core, CoreError, READ, WRITE, SafeTree, digest
from novel_studio.aesthetics import template, validate_profile, prepare_report, sources, REVIEW_FORMAT
from novel_studio.creative import template as writer_template
from novel_studio.prompting import assemble


class AestheticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = Core.create(Path(self.temp.name), "证据审稿合成工程", ROOT)
        cap = self.core.bind("writer", READ | WRITE)
        self.draft = self.core.dispatch(cap, "create_draft", {"text": "# 第一章\n林舟6月4日回镇。\n周岚握着蓝信，没有说话。"}, "synthetic-review")
        self.core.snapshot()
        self.core.handoff(self.draft["document_id"])
        self.source = next(r for r in sources(self.core) if "/Drafts/" in r["relative"])

    def finding(self, **changes):
        return {"category": "A/objective", "title": "时间线待核对", "severity": "medium", "source_document_id": self.source["document_id"],
                "source_revision": self.source["revision"], "start_line": 2, "end_line": 2, "quote": "林舟6月4日回镇。",
                "basis": "这里只证明原文存在此日期，不单凭这句话断言冲突。", "recommendation": "请作者提供另一事件时间作对照。", "requires_change": False, **changes}

    def report(self, findings):
        return json.dumps({"format": REVIEW_FORMAT, "summary": "合成审稿，仅核对引文。", "findings": findings}, ensure_ascii=False)

    def test_independent_bindings_versions_and_no_kind_replacement(self):
        writer = self.core.creative.save(writer_template("人物驱动型"))
        editor = self.core.creative.save(template("文学性优先"))
        self.core.creative.bind(writer["profile_id"])
        self.core.creative.bind(editor["profile_id"])
        w, _ = self.core.creative.context("writer")
        e, _ = self.core.creative.context("editor")
        self.assertEqual(w["kind"], "writer_style")
        self.assertEqual(e["kind"], "editor_aesthetic")
        self.assertIn("weights", e)
        self.assertNotIn("weights", w)
        with self.assertRaises(CoreError):
            self.core.creative.save(template("剧情逻辑优先"), writer["profile_id"], writer["revision"])

    def test_matching_quote_is_not_certification_of_judgement(self):
        before = self.core.document(self.draft["document_id"])["revision"]
        output, check = prepare_report(self.core, self.report([self.finding()]))
        self.assertEqual(check["verified_quotes"], 1)
        self.assertIn("不证明模型判断正确", output)
        self.assertIn("B · 创作质量判断", output)
        self.assertIn("C · 审美偏好", output)
        self.assertEqual(self.core.document(self.draft["document_id"])["revision"], before)

    def test_fabricated_quote_stale_version_and_wrong_scope_flagged(self):
        for change in ({"quote": "原文没有的伪造引文"}, {"source_revision": "0" * 64}, {"start_line": 1, "end_line": 1}, {"source_document_id": self.draft["document_id"]}, {"source_document_id": "../../Canonical"}):
            with self.subTest(change=change):
                output, check = prepare_report(self.core, self.report([self.finding(**change)]))
                self.assertEqual(check["status"], "PARTIAL")
                self.assertIn("未核验", output)

    def test_preference_never_forced_and_malformed_output_preserved(self):
        output, check = prepare_report(self.core, self.report([self.finding(category="C/preference", requires_change=True)]))
        self.assertIn("不是必改事实错误", output)
        self.assertIn("已改为作者自选", output)
        raw = "模型没有按结构输出，但这段意见应保留。"
        output, check = prepare_report(self.core, raw)
        self.assertEqual(check["status"], "UNSTRUCTURED")
        self.assertIn(raw, output)
        output, check = prepare_report(self.core, self.report([self.finding(category=["A/objective"])]))
        self.assertEqual(check["status"], "UNSTRUCTURED")

    def test_contract_and_current_sources_are_in_compiler(self):
        profile = self.core.creative.save(template("剧情逻辑优先"))
        self.core.creative.bind(profile["profile_id"])
        compiled, _ = assemble(self.core, "editor", "本轮作者要求：允许保留悬念")
        self.assertIn("editor_aesthetic", compiled.stable_text)
        self.assertIn("C/preference", compiled.stable_text)
        self.assertIn(self.source["document_id"], compiled.dynamic_text)
        self.assertTrue(compiled.dynamic_text.endswith("本轮作者要求：允许保留悬念"))

    def test_invalid_weights_and_extra_capabilities_rejected(self):
        value = template("自定义审美")
        value["weights"]["character"] = True
        with self.assertRaises(CoreError):
            validate_profile(value)
        value = template("自定义审美")
        value["shell"] = "allow"
        with self.assertRaises(CoreError):
            validate_profile(value)


if __name__ == "__main__":
    unittest.main()
