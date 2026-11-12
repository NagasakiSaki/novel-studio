"""Existing files must not veto drafts; real acceptance conflicts still stop."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from novel_studio.core import Core, CoreError, SafeTree
from novel_studio.workflow_chapters import formal_target
from test_outline_first import macro, AdaptiveModels


class ExistingFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-existing-files-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.core = Core.create(Path(self.temp.name), "合成存量文件", ROOT)
        row = self.core.workflow.planning.save(macro(1))
        self.core.author_commit(self.core.workflow.planning.preview(row["version_id"])["handle"], True)
        self.models = AdaptiveModels(self.core)
        self.pipeline = self.core.workflow.pipeline

    def add(self, name, body="# 合成旧文件\n此内容不能因启动而被改写，也不能擅自当作已完成章节。"):
        relative = "Canonical/Final-Manuscript/" + name
        with SafeTree(self.core.project) as tree:
            tree.mkdir(str(Path(relative).parent))
            tree.create(relative, body.encode())
        return relative

    def canonical(self):
        return {r["relative"]: r["revision"] for r in self.core.documents() if r["area"] == "canonical"}

    def complete(self, count=1):
        preview = self.pipeline.preview("chapters", count)
        run = self.pipeline.start(preview, True)
        for _ in range(35):
            value = self.pipeline.advance(run["run_id"])
            if value["status"] == "COMPLETED":
                return run, preview
            self.assertEqual(value["status"], "RUNNING")
        self.fail("Finite synthetic run did not finish")

    def first_id(self):
        return self.core.workflow.chapters.catalog()[0]["chapter_id"]

    def test_unnumbered_existing_text_allows_start_and_only_creates_draft(self):
        self.add("没有编号的历史旧稿.md")
        before = self.canonical()
        run, preview = self.complete()
        self.assertEqual(len(preview["unplaced_files"]), 1)
        self.assertTrue(any("先写独立草稿" in warning for warning in preview["warnings"]))
        self.assertEqual(before, self.canonical())
        self.assertIsNone(self.core.workflow.chapters.catalog()[0]["canonical"])
        self.assertEqual(len(self.models.calls), 3)

    def test_readme_and_json_are_not_misclassified_as_chapters(self):
        self.add("README.md", "# 使用说明\n这是文件夹说明。")
        self.add("metadata.json", '{"purpose":"synthetic service metadata"}')
        self.add("说明.txt", "仅合成说明，不是小说章。")
        before = self.canonical()
        self.assertEqual(self.core.workflow.chapters.unindexed(), [])
        self.complete()
        preview = self.core.workflow.chapters.preview([self.first_id()])
        self.core.author_commit(preview["handle"], True)
        after = self.canonical()
        self.assertTrue(all(after[name] == sha for name, sha in before.items()))

    def test_unplaced_other_number_does_not_block_unrelated_acceptance(self):
        self.add("097_合成旧章.md")
        before = self.canonical()
        self.complete()
        preview = self.core.workflow.chapters.preview([self.first_id()])
        self.assertIn("不移动、不修改", preview["diff"])
        self.core.author_commit(preview["handle"], True)
        after = self.canonical()
        self.assertTrue(all(after[name] == sha for name, sha in before.items()))
        self.assertIn(formal_target(1, 1), after)

    def test_same_number_legacy_file_allows_draft_but_blocks_that_acceptance(self):
        self.add("001_合成已有正文.md")
        before = self.canonical()
        self.complete()
        with self.assertRaises(CoreError) as error:
            self.core.workflow.chapters.preview([self.first_id()])
        self.assertEqual(error.exception.code, "EXISTING_CHAPTER_CONFLICT")
        self.assertIn("本次第1卷第1章", str(error.exception))
        self.assertEqual(before, self.canonical())

    def test_exact_existing_target_is_never_overwritten_by_start_or_acceptance(self):
        self.add("Volume-001/Chapter-001.md")
        before = self.canonical()
        self.complete()
        with self.assertRaises(CoreError) as error:
            self.core.workflow.chapters.preview([self.first_id()])
        self.assertEqual(error.exception.code, "TARGET_EXISTS")
        self.assertEqual(before, self.canonical())

    def test_unidentified_file_stays_independent_instead_of_fake_auto_mapping(self):
        self.add("作者留存的独立片段.md")
        before = self.canonical()
        self.complete()
        self.assertFalse(any(r.get("existing_import") for r in self.core.workflow.chapters.catalog()))
        preview = self.core.workflow.chapters.preview([self.first_id()])
        self.core.author_commit(preview["handle"], True)
        self.assertEqual(before.keys() - self.canonical().keys(), set())
        self.assertTrue(all(self.canonical()[name] == sha for name, sha in before.items()))

    def test_batch_conflict_is_detected_before_any_new_formal_file(self):
        self.add("002_合成已有正文.md")
        before = self.canonical()
        self.complete(2)
        ids = [r["chapter_id"] for r in self.core.workflow.chapters.catalog() if r.get("draft")]
        with self.assertRaises(CoreError) as error:
            self.core.workflow.chapters.preview(ids)
        self.assertEqual(error.exception.code, "EXISTING_CHAPTER_CONFLICT")
        self.assertEqual(before, self.canonical())

    def test_conflict_created_after_preview_is_checked_before_commit(self):
        self.complete()
        preview = self.core.workflow.chapters.preview([self.first_id()])
        self.add("001_批准前新出现的合成旧章.md")
        before = self.canonical()
        with self.assertRaises(CoreError) as error:
            self.core.author_commit(preview["handle"], True)
        self.assertEqual(error.exception.code, "EXISTING_CHAPTER_CONFLICT")
        self.assertEqual(before, self.canonical())

    def test_file_change_during_start_confirmation_invalidates_preview(self):
        relative = self.add("独立合成旧稿.md")
        preview = self.pipeline.preview()
        with SafeTree(self.core.project) as tree:
            raw = tree.read(relative)
            from novel_studio.core import digest
            tree.publish(relative, "作者刚修改的合成旧稿".encode(), digest(raw))
        with self.assertRaises(CoreError) as error:
            self.pipeline.start(preview, True)
        self.assertEqual(error.exception.code, "REVISION_CONFLICT")
        self.assertEqual(self.models.calls, [])

    def test_unplaced_text_not_implicitly_sent_to_the_model(self):
        sentinel = "CANONICAL_UNPLACED_PRIVATE_SENTINEL_合成秘密"
        self.add("独立旧文件.md", sentinel)
        self.complete()
        self.assertNotIn(sentinel, json.dumps(self.models.calls, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
