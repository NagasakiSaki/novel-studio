"""Versioned author guidance and consistent local goals; no model requests."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_studio.core import Core, CoreError, READ, WRITE, SafeTree
from novel_studio.creative import CreativeService, template, validate_profile
from novel_studio.lengths import LengthService, DEFAULT_GOAL, evaluate
from novel_studio.text_metrics import measure, plaintext


class CreativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = Core.create(Path(self.temp.name), "创作配置合成工程", ROOT)
        self.styles = CreativeService(self.core)

    def test_pinned_binding_history_copy_and_archive(self):
        initial = template("细腻现实主义")
        first = self.styles.save(initial)
        self.styles.bind(first["profile_id"])
        changed = template("文学性叙事")
        second = self.styles.save(changed, first["profile_id"], first["revision"])
        self.assertEqual(second["version"], 2)
        stable, _ = self.styles.context("writer")
        self.assertEqual(stable["version"], 1)
        self.assertEqual(self.styles.profile(first["profile_id"], 1)["value"], initial)
        self.styles.bind(second["profile_id"])
        self.assertEqual(self.styles.context("writer")[0]["version"], 2)
        duplicate = self.styles.save(changed)
        self.assertNotEqual(duplicate["profile_id"], second["profile_id"])
        self.styles.archive(second["profile_id"])
        self.assertEqual(self.styles.context("writer"), ({}, {}))
        self.assertEqual(self.styles.profile(second["profile_id"], 1)["value"], initial)
        self.assertNotEqual(self.styles.diff(initial, changed), "")

    def test_stale_profile_and_noop_version(self):
        first = self.styles.save(template("人物驱动型"))
        same = self.styles.save(template("人物驱动型"), first["profile_id"], first["revision"])
        self.assertEqual(same["version"], 1)
        changed = template("剧情驱动型")
        self.styles.save(changed, first["profile_id"], first["revision"])
        with self.assertRaises(CoreError):
            self.styles.save(changed, first["profile_id"], first["revision"])
        with SafeTree(self.core.project) as tree:
            self.assertGreater(len(tree.files(".novel-studio/Creative/Config-History")), 0)

    def test_import_rejects_permissions_and_path_traversal(self):
        value = template("自定义风格")
        value["permissions"] = {"allow": "all"}
        with self.assertRaises(CoreError):
            validate_profile(value)
        with self.assertRaises(CoreError):
            self.styles.profile("../Canonical")

    def test_chapter_override_and_editor_are_separate(self):
        cap = self.core.bind("writer", READ | WRITE)
        draft = self.core.dispatch(cap, "create_draft", {"text": "合成草稿"}, "synthetic-draft")
        self.styles.override(draft["document_id"], {"pace": "本章允许静止的心理场景"}, "当前指令仍优先")
        self.assertIn("本章", self.styles.context("writer", draft["document_id"])[1]["fields"]["pace"])
        self.assertEqual(self.styles.context("editor", draft["document_id"]), ({}, {}))


class LengthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = Core.create(Path(self.temp.name), "字数合成工程", ROOT)
        self.lengths = LengthService(self.core)
        self.cap = self.core.bind("writer", READ | WRITE)

    def test_markdown_frontmatter_headings_links_and_chinese(self):
        text = "\ufeff---\n作者: 合成\n---\n# 第一章\n\n中文 **人物**，[hello](https://example.invalid) 2026。\n\n> 证据\n\n```text\nABC\n```\n"
        plain = plaintext(text)
        self.assertNotIn("example.invalid", plain)
        self.assertNotIn("第一章", plain)
        self.assertNotIn("**", plain)
        result = measure(text)
        self.assertEqual(result["han"], 6)
        self.assertEqual(result["latin_letters"], 8)
        self.assertEqual(result["digits"], 4)
        self.assertEqual(result["count"], 20)
        self.assertEqual(measure(text, "han_letters_digits_v1")["count"], 18)
        self.assertEqual(measure(text, "han_english_words_v1")["count"], 9)

    def test_four_levels_same_policy_no_revision_double_count(self):
        vol = self.lengths.add("volume", "第一卷")
        chapter = self.lengths.add("chapter", "第一章", vol)
        scene = self.lengths.add("scene", "钟楼场景", chapter)
        draft = self.core.dispatch(self.cap, "create_draft", {"text": "# 章节标题\n林舟握住钥匙。\n周岚保管蓝信。"}, "synthetic-one")
        self.lengths.link(chapter, draft["document_id"])
        self.lengths.link(scene, draft["document_id"], 2, 2)
        counts = self.lengths.counts()["nodes"]
        self.assertEqual(counts[chapter]["count"], 14)
        self.assertEqual(counts[vol]["count"], 14)
        self.assertEqual(counts["book"]["count"], 14)
        self.assertEqual(counts[scene]["count"], 7)
        another = self.lengths.add("chapter", "第二章", vol)
        with self.assertRaises(CoreError):
            self.lengths.link(another, draft["document_id"])
        self.assertFalse(self.lengths.counts()["nodes"]["book"]["measured_complete"])
        self.core.dispatch(self.cap, "update_draft", {"document_id": draft["document_id"], "expected_revision": draft["revision"], "text": "# 新标题\n新的正文。"}, "synthetic-two")
        self.assertEqual(self.lengths.counts()["nodes"][scene]["missing_sources"][0]["status"], "STALE_RANGE")

    def test_goal_bands_inheritance_and_invalid_parent(self):
        chapter = self.lengths.add("chapter", "第一章")
        self.lengths.goal("book", {**DEFAULT_GOAL, "target": 1000, "strict": True})
        self.lengths.goal(chapter, {**DEFAULT_GOAL, "inherit": True})
        self.assertEqual(self.lengths.resolved_goal(chapter)["target"], 1000)
        self.assertEqual(evaluate(900, self.lengths.resolved_goal(chapter))["status"], "WITHIN")
        self.assertEqual(evaluate(899, self.lengths.resolved_goal(chapter))["status"], "UNDER")
        self.assertEqual(evaluate(1101, self.lengths.resolved_goal(chapter))["status"], "OVER")
        with self.assertRaises(CoreError):
            self.lengths.add("scene", "不能挂在全书", "book")
        with self.assertRaises(CoreError):
            self.lengths.goal(chapter, {**DEFAULT_GOAL, "minimum": 200, "target": 100})


if __name__ == "__main__":
    unittest.main()
