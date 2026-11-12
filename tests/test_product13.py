"""1.3 synthetic integration: real Core/files, no real model or private novels."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from novel_studio.core import CoreError, SafeTree, digest
from novel_studio.workflow_planning import OUTLINE_TARGET
from novel_studio.workflow_store import BASE
from ui2_fixtures import project
from test_outline_first import macro, AdaptiveModels


class ProductModels(AdaptiveModels):
    def __init__(self, core, bad_reviews=1):
        super().__init__(core, finish_volume_at=3, book_at=3)
        self.bad_reviews = bad_reviews
        self.review_counts = {}

    def structured_task(self, name, instructions, current, schema, **kwargs):
        result = super().structured_task(name, instructions, current, schema, **kwargs)
        if name in {"workflow_plan", "author_plan"}:
            result["output"]["word_range"] = {"minimum": 1800, "target": 2500, "maximum": 5000}
        if name == "workflow_review":
            ordinal = current["chapter"]["ordinal"]
            count = self.review_counts.get(ordinal, 0) + 1
            self.review_counts[ordinal] = count
            if ordinal == 1 and count <= self.bad_reviews:
                quote = current["current_manuscript"]["text"].splitlines()[1]
                result["output"]["current_objective"] = [{"title": "知情边界需要修复", "quote": quote, "basis": "合成设定中的知情范围", "recommendation": "保留动作，删除提前知情描述", "kind": "identity", "severity": "high", "certainty": "supported", "start_line": 2, "end_line": 2}]
        if name in {"workflow_revise", "author_revise"}:
            result["output"]["text"] += "\n林舟转身，没有问蓝信。"
        return result


class Product13Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-product13-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.core, _ = project(Path(self.temp.name), "1.3合成验收", approve=False)
        record = self.core.workflow.planning.save(macro(1))
        preview = self.core.workflow.planning.preview(record["version_id"])
        self.core.author_commit(preview["handle"], True)
        self.core.workflow.chapters.ensure()
        self.models = self.core.models
        self.script = ProductModels(self.core)
        self.core.models = self.models
        self.models.structured_task = self.script.structured_task
        self.pipeline = self.core.workflow.pipeline
        with SafeTree(self.core.project) as tree:
            self.outline_hash = digest(tree.read(OUTLINE_TARGET))

    def start(self, count=3, creative_mode="continuous"):
        return self.pipeline.start(self.pipeline.preview("chapters", count, creative_mode), True)

    def drive(self, run, target="COMPLETED", limit=120):
        for _ in range(limit):
            result = self.pipeline.advance(run["run_id"])
            if result["status"] == target:
                return result
            self.assertEqual(result["status"], "RUNNING", result)
        self.fail("bounded test failed to finish")

    def test_three_chapters_first_plan_repair_rereview_ranges_and_author_approval(self):
        run = self.drive(self.start())
        tasks = [r["task"] for r in self.script.calls]
        self.assertEqual(tasks, ["workflow_plan", "workflow_write", "workflow_review", "workflow_repair_plan", "workflow_revise", "workflow_review"] + ["workflow_plan", "workflow_write", "workflow_review"] * 2)
        self.assertEqual(len(run["completed_chapters"]), 3)
        rows = self.core.workflow.chapters.catalog()
        self.assertEqual([r["number"] for r in rows], [1, 2, 3])
        self.assertEqual(rows[0]["repair_rounds"], 1)
        self.assertEqual(len(rows[0]["review_history"]), 2)
        self.assertTrue(rows[0]["revision_history"] and rows[0]["revision_plans"])
        for call in self.script.calls:
            if call["task"] in {"workflow_write", "workflow_review", "workflow_revise"}:
                self.assertEqual(call["current"]["editor_execution_plan"]["value"]["word_range"]["target"], 2500)
                self.assertEqual(call["current"]["goal"]["target"], 2500)
            if call["task"] == "workflow_review":
                self.assertGreater(call["current"]["actual_word_count"], 0)
        self.assertTrue(all(r["status"] == "AUTHOR_APPROVAL_PENDING" and not r.get("canonical") for r in rows))
        with SafeTree(self.core.project) as tree:
            self.assertEqual(digest(tree.read(OUTLINE_TARGET)), self.outline_hash)
        preview = self.core.workflow.chapters.preview([rows[0]["chapter_id"]])
        with self.assertRaises(CoreError):
            self.core.author_commit(preview["handle"], False)
        self.assertFalse(self.core.workflow.chapters.catalog()[0]["canonical"])

    def test_two_repair_round_cap_keeps_all_versions_and_stops_requests(self):
        self.script.bad_reviews = 99
        result = self.drive(self.start(1), "BLOCKED")
        row = self.core.workflow.chapters.catalog()[0]
        self.assertEqual(row["repair_rounds"], 2)
        self.assertEqual(len(row["review_history"]), 3)
        self.assertEqual(len(row["revision_plans"]), 2)
        self.assertEqual(len(self.script.calls), 9)
        for _ in range(3):
            self.pipeline.advance(result["run_id"])
        self.assertEqual(len(self.script.calls), 9)
        self.assertFalse(row["canonical"])

    def test_assisted_mode_pauses_after_plan_then_writer_and_review(self):
        self.script.bad_reviews = 0
        run = self.start(1, "assisted")
        value = self.pipeline.advance(run["run_id"])
        self.assertEqual(value["status"], "PAUSED")
        row = self.core.workflow.chapters.catalog()[0]
        self.assertTrue(row["editor_plan"])
        self.assertFalse(row["draft"])
        self.pipeline.resume(run["run_id"])
        self.assertEqual(self.pipeline.advance(run["run_id"])["status"], "PAUSED")
        self.assertTrue(self.core.workflow.chapters.catalog()[0]["draft"])
        self.pipeline.resume(run["run_id"])
        self.drive(run, "PAUSED")
        self.assertEqual([r["task"] for r in self.script.calls], ["workflow_plan", "workflow_write", "workflow_review"])

    def test_pause_reopen_retains_plan_numbering_and_does_not_repeat_request(self):
        self.script.bad_reviews = 0
        run = self.start()
        self.script.hook = lambda: self.pipeline.control(run["run_id"], "pause")
        self.assertEqual(self.pipeline.advance(run["run_id"])["status"], "PAUSED")
        first = self.core.workflow.chapters.catalog()[0]
        self.pipeline.resume(run["run_id"])
        self.drive(run)
        self.assertEqual(self.core.workflow.chapters.catalog()[0]["chapter_id"], first["chapter_id"])
        self.assertEqual(len([r for r in self.script.calls if r["task"] == "workflow_plan"]), 3)

    def test_incomplete_editor_plan_does_not_dispatch_writer(self):
        original = self.models.structured_task
        def invalid(*a, **kw):
            value = original(*a, **kw)
            value["output"]["events"] = ""
            return value
        self.models.structured_task = invalid
        run = self.start(1)
        with self.assertRaises(CoreError) as caught:
            self.pipeline.advance(run["run_id"])
        self.assertEqual(caught.exception.code, "PLAN_INVALID")
        self.assertEqual([r["task"] for r in self.script.calls], ["workflow_plan"])
        self.assertFalse(self.core.workflow.chapters.catalog()[0]["draft"])

    def test_author_direct_revision_review_plan_and_persistent_role_histories(self):
        self.script.bad_reviews = 0
        self.drive(self.start(1))
        row = self.core.workflow.chapters.catalog()[0]
        old = copy.deepcopy(row["draft"])
        result = self.core.workflow.author.send("writer", "保留剧情，加强结尾的具体动作。", operation="revise", chapter_id=row["chapter_id"])
        updated = self.core.workflow.chapters.catalog()[0]
        self.assertNotEqual(updated["draft"], old)
        self.assertIsNotNone(updated["local_length"])
        self.assertFalse(updated["local_length"]["blocking"])
        self.assertEqual(result["artifact"]["document_id"], updated["draft"]["document_id"])
        self.core.workflow.chapters.checked_document(old, "drafts")
        self.core.workflow.author.send("editor", "只审逻辑与人物知情。", operation="review", chapter_id=row["chapter_id"])
        self.core.workflow.author.send("editor", "下一版计划保持克制的结尾。", operation="plan", chapter_id=row["chapter_id"])
        self.assertEqual(self.models.transcript_page("writer")["entries"][0]["operation"], "revise")
        self.assertEqual(len(self.models.transcript_page("editor")["entries"]), 2)
        self.assertFalse(self.core.workflow.chapters.catalog()[0]["canonical"])

    def test_book_guidance_local_no_model_request_then_explicit_disable(self):
        count = len(self.script.calls)
        self.core.workflow.author.send("writer", "保留作者的慢热与留白，不强加商业钩子。", operation="book_guidance")
        self.assertEqual(len(self.script.calls), count)
        record = self.core.workflow.store.read()["author_instructions"][0]
        self.assertEqual(record["status"], "ACTIVE")
        self.core.workflow.author.disable_guidance(record["id"])
        self.assertEqual(self.core.workflow.store.read()["author_instructions"][0]["status"], "DISABLED")

    def test_directory_reassignment_preserves_identity_drafts_and_outline(self):
        self.script.bad_reviews = 0
        self.drive(self.start())
        before = self.core.workflow.chapters.catalog()
        cid = before[2]["chapter_id"]
        self.core.workflow.chapters.place(cid, 2, 1, "作者阅读目录")
        after = self.core.workflow.chapters.catalog()
        for old, new in zip(before, after):
            for key in ("chapter_id", "volume", "number", "draft", "review", "editor_plan"):
                self.assertEqual(old[key], new[key])
        self.assertEqual(after[2]["directory"]["volume"], 2)
        self.assertEqual([r["directory"]["position"] for r in after[:2]], [1, 2])

    def test_old_run_requires_author_upgrade_and_preserves_raw_state_history(self):
        run = self.start(1)
        self.pipeline.control(run["run_id"], "pause")
        self.core.workflow.store.mutate("legacy_test_fixture", lambda s, t: s["runs"][run["run_id"]].pop("workflow_version"))
        with self.assertRaises(CoreError) as caught:
            self.pipeline.resume(run["run_id"])
        self.assertEqual(caught.exception.code, "WORKFLOW_UPGRADE_REQUIRED")
        self.assertEqual(self.script.calls, [])
        self.pipeline.resume(run["run_id"], upgrade_workflow=True)
        self.assertTrue(self.pipeline.get(run["run_id"])["upgraded_by_author"])


if __name__ == "__main__":
    unittest.main()
