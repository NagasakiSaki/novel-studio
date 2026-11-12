from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_studio.core import Core, CoreError
from novel_studio.workflow_review import DIRECTION_FIELDS, validate_review


def review_fixture():
    return {"summary": "合成审稿：具体场景与信息边界。", "current_objective": [], "current_quality": ["可加强人物语言差异，这是质量建议。"],
        "future_notes": [{"topic": "人物语言", "text": "后续保持林舟简短、周岚更留意细节；不返工本章。", "kind": "style", "scope": "next", "expires_after_chapter": 0}],
        "next_direction": {**{key: "围绕下一章已确认细纲，保持蓝信知情边界。" for key in DIRECTION_FIELDS}, "suggested_words": 3000}}


class ReviewWorkflowTests(unittest.TestCase):
    def test_four_categories_and_preference_do_not_become_objective_fault(self):
        check = validate_review(review_fixture(), "# 合成正文\n周岚把钟轮放在桌上。")
        self.assertEqual(set(check["categories"]), {"A", "B", "C", "D"})
        self.assertFalse(check["serious_flags"])
        self.assertIn("质量建议", check["value"]["current_quality"][0])

    def test_serious_flags_keep_quote_evidence_and_uncertainty_separate(self):
        value = review_fixture()
        issue = {"title": "身份可能矛盾", "quote": "林舟说出蓝信内容。", "basis": "设定说他不知道；仍需作者裁决。", "recommendation": "暂停核对", "kind": "identity", "severity": "high", "certainty": "uncertain", "start_line": 2, "end_line": 2}
        value["current_objective"] = [issue]
        result = validate_review(value, "# 标题\n林舟说出蓝信内容。")
        self.assertTrue(result["quote_checks"][0]["quote_matches"])
        self.assertEqual(result["serious_flags"][0]["certainty"], "uncertain")
        issue["quote"] = "不存在的原文"
        self.assertFalse(validate_review(value, "# 标题\n林舟说出蓝信内容。")["quote_checks"][0]["quote_matches"])

    def test_guidance_has_versions_expiry_author_disable_and_retrieval(self):
        with tempfile.TemporaryDirectory(prefix="novel-guidance-synthetic-") as folder:
            core = Core.create(Path(folder), "合成指导", ROOT)
            service = core.workflow.guidance
            chapter = {"chapter_id": "a" * 32, "ordinal": 1}
            notes = review_fixture()["future_notes"]
            notes += [{"topic": "铜钥匙", "text": "铜钥匙尚未回收，在钟楼场景核对。", "kind": "thread", "scope": "relevant", "expires_after_chapter": 4}]
            refs = service.ingest(notes, chapter, {"document_id": "b" * 32, "revision": "c" * 64})
            self.assertEqual(len(service.select(2, "钟楼铜钥匙")), 2)
            self.assertEqual(len(service.select(3, "钟楼铜钥匙")), 1)
            self.assertEqual(service.select(5, "钟楼铜钥匙"), [])
            service.update(refs[1]["guidance_id"], active=False)
            self.assertEqual(service.select(3, "铜钥匙"), [])
            self.assertEqual(core.workflow.store.read()["guidance"][refs[1]["guidance_id"]]["version"], 2)

    def test_author_locked_guidance_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory(prefix="novel-guidance-synthetic-") as folder:
            core = Core.create(Path(folder), "合成锁定指导", ROOT)
            service = core.workflow.guidance
            notes = review_fixture()["future_notes"]
            ref = service.ingest(notes, {"chapter_id": "a" * 32, "ordinal": 1}, {})[0]
            service.update(ref["guidance_id"], locked=True)
            notes[0]["text"] = "另一项相反建议"
            with self.assertRaises(CoreError):
                service.ingest(notes, {"chapter_id": "b" * 32, "ordinal": 2}, {})


if __name__ == "__main__":
    unittest.main()
