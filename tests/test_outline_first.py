"""Macro outlines, incremental metadata and elastic length on synthetic books."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from novel_studio.core import Core, CoreError, SafeTree, digest
from novel_studio.workflow_planning import OUTLINE_TARGET
from novel_studio.workflow_store import DEFAULT_SCALE
from novel_studio.text_metrics import measure
from test_workflow_planning import fixture
from test_workflow_pipeline import ScriptedModels


def macro(volumes=0):
    value = {key: val for key, val in fixture().items() if key != "volumes"}
    value["volumes"] = [{"summary": "钟楼修复与蓝信的知情边界，保持作者结局。", "chapters": []} for _ in range(volumes)]
    return value


class AdaptiveModels(ScriptedModels):
    """No CLI/API. Decisions are explicit fixture data, never real AI evidence."""
    def __init__(self, core, sizes=None, finish_volume_at=2, book_at=2):
        super().__init__(core)
        self.sizes = sizes or [50] * 30
        self.writes, self.finish_volume_at, self.book_at = 0, finish_volume_at, book_at
    def structured_task(self, name, instructions, current, schema, **kwargs):
        result = super().structured_task(name, instructions, current, schema, **kwargs)
        if name == "workflow_plan":
            result["output"]["word_range"] = {key: current["goal"][key] for key in ("minimum", "target", "maximum")}
        if name == "workflow_write":
            index = self.writes
            self.writes += 1
            chapter = current["chapter"]
            title = "雨里的钟轮" + str(index + 1)
            text = "# " + title + "\n" + "雨" * self.sizes[index]
            result["output"].update(title=title, text=text, execution={"volume_title": "钟楼与蓝信",
                "chapter_goal": "用具体行动推进钟楼修复，保留林舟的知情边界。",
                "volume_complete": chapter["number"] >= self.finish_volume_at,
                "book_complete": self.writes >= self.book_at})
        return result


class OutlineFirstTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-outline-first-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.core = Core.create(Path(self.temp.name), "合成宏观创作", ROOT)
        self.pipeline = self.core.workflow.pipeline

    def prepare(self, value, **model_args):
        self.core.workflow.scale.save(DEFAULT_SCALE)
        row = self.core.workflow.planning.save(value)
        preview = self.core.workflow.planning.preview(row["version_id"])
        self.core.author_commit(preview["handle"], True)
        self.outline = row
        with SafeTree(self.core.project) as tree:
            self.outline_bytes = tree.read(OUTLINE_TARGET)
        self.models = AdaptiveModels(self.core, **model_args)
        return row

    def unchanged_outline(self):
        with SafeTree(self.core.project) as tree:
            self.assertEqual(tree.read(OUTLINE_TARGET), self.outline_bytes)
        self.assertEqual(self.core.workflow.planning.approved()["version_id"], self.outline["version_id"])

    def drive(self, run, limit=160):
        for _ in range(limit):
            value = self.pipeline.advance(run["run_id"])
            if value["status"] == "COMPLETED":
                self.unchanged_outline()
                return value
            self.assertEqual(value["status"], "RUNNING", value)
        self.fail("Bounded synthetic run did not finish")

    def start(self, mode="chapters", count=1):
        return self.pipeline.start(self.pipeline.preview(mode, count), True)

    def test_01_only_macro_outline_starts_without_materials_or_chapter_fields(self):
        self.prepare(macro())
        preview = self.pipeline.preview("chapters", 1)
        self.assertTrue(preview["execution_scope"]["dynamic"])
        self.drive(self.pipeline.start(preview, True))
        self.assertEqual(self.models.writes, 1)
        self.assertEqual(len(self.models.calls), 3)
        row = self.core.workflow.chapters.catalog()[0]
        self.assertEqual((row["volume"], row["number"]), (1, 1))
        self.assertTrue(row["title"] and row["volume_title"])
        self.assertIsNone(row["canonical"])

    def test_02_volume_names_are_preserved_without_chapter_names(self):
        value = macro(1)
        value["volumes"][0]["title"] = "作者确认的卷名"
        self.prepare(value)
        self.drive(self.start())
        row = self.core.workflow.chapters.catalog()[0]
        self.assertEqual(row["volume_title"], "作者确认的卷名")
        self.assertEqual(row["title"], "雨里的钟轮1")

    def test_03_chapter_names_are_preserved_and_volume_suggestion_generated(self):
        value = fixture()
        value["volumes"][0]["title"] = ""
        self.prepare(value)
        self.drive(self.start())
        row = self.core.workflow.chapters.catalog()[0]
        self.assertEqual(row["title"], value["volumes"][0]["chapters"][0]["title"])
        self.assertEqual(row["volume_title"], "钟楼与蓝信")
        from novel_studio.exporting import ExportService
        exported = next(r for r in ExportService(self.core).catalog() if r["document_id"] == row["draft"]["document_id"])
        self.assertEqual(exported["title"], "第1章 " + row["title"])
        self.assertIn("钟楼与蓝信", exported["volume"])

    def test_04_all_names_empty_and_source_numbers_missing_duplicate_or_gapped(self):
        value = macro(2)
        value["volumes"][0]["number"] = 3
        value["volumes"][1]["number"] = 3
        value["volumes"][0]["chapters"] = [{"number": 4}, {"number": 4}, {}]
        self.prepare(value)
        self.drive(self.start(count=3))
        rows = self.core.workflow.chapters.catalog()[:3]
        self.assertEqual([(r["volume"], r["number"]) for r in rows], [(1, 1), (1, 2), (1, 3)])
        self.assertEqual(len({r["chapter_id"] for r in rows}), 3)
        self.assertTrue(all(r["title"] for r in rows))

    def test_05_eight_volumes_override_twenty_chapter_one_volume_suggestion(self):
        self.prepare(macro(8), finish_volume_at=1, book_at=1)
        before = self.core.workflow.scale.get()
        run = self.drive(self.start("book"))
        rows = [r for r in self.core.workflow.chapters.catalog() if r["draft"]]
        self.assertEqual([r["volume"] for r in rows], list(range(1, 9)))
        self.assertEqual(len(run["completed_chapters"]), 8)
        self.assertEqual(self.core.workflow.scale.get(), before)
        self.assertTrue(self.core.workflow.execution.describe()["story_complete"])

    def test_06_1800_3200_4500_characters_save_review_and_continue(self):
        self.prepare(macro(1), sizes=[1800, 3200, 4500], finish_volume_at=3, book_at=3)
        self.drive(self.start("book"))
        rows = self.core.workflow.chapters.catalog()
        self.assertEqual([r["local_length"]["count"] for r in rows], [1800, 3200, 4500])
        self.assertEqual([r["local_length"]["status"] for r in rows], ["UNDER", "WITHIN", "OVER"])
        self.assertTrue(all(r["local_length"]["blocking"] is False and r["review"] and r["next_direction"] for r in rows))
        self.assertEqual(len(self.models.calls), 9)

    def test_07_pause_reopen_resume_keeps_ids_and_does_not_rewrite(self):
        self.prepare(macro(), book_at=3, finish_volume_at=3)
        run = self.start(count=3)
        self.models.hook = lambda: self.pipeline.control(run["run_id"], "pause")
        self.assertEqual(self.pipeline.advance(run["run_id"])["status"], "PAUSED")
        cid = self.core.workflow.chapters.catalog()[0]["chapter_id"]
        self.core = Core(self.core.project, ROOT)
        self.core.models = self.models
        self.models.core = self.core
        self.pipeline = self.core.workflow.pipeline
        self.pipeline.resume(run["run_id"])
        self.drive(run)
        rows = self.core.workflow.chapters.catalog()
        self.assertEqual([r["number"] for r in rows], [1, 2, 3])
        self.assertEqual(rows[0]["chapter_id"], cid)
        self.assertEqual(self.models.writes, 3)
        for _ in range(3):
            self.core.workflow.chapters.ensure()
        self.assertEqual(self.core.workflow.chapters.catalog(), rows)

    def test_08_generated_metadata_does_not_bypass_formal_author_approval(self):
        self.prepare(macro(), finish_volume_at=1, book_at=1)
        self.drive(self.start())
        rows = self.core.workflow.chapters.catalog()
        self.assertTrue(all(r["canonical"] is None for r in rows))
        preview = self.core.workflow.chapters.preview([rows[0]["chapter_id"]])
        with self.assertRaises(CoreError):
            self.core.author_commit(preview["handle"], False)
        preview = self.core.workflow.chapters.preview([rows[0]["chapter_id"]])
        self.core.author_commit(preview["handle"], True)
        self.assertTrue(self.core.workflow.chapters.catalog()[0]["canonical"])
        self.unchanged_outline()

    def test_natural_language_markdown_preserves_raw_outline_and_names(self):
        raw = "# 全书宏观大纲\r\n林舟与周岚修理钟楼。\r\n## 第一卷 雾港旧事\r\n### 第一章 蓝信\r\n保留人物知情差。\r\n### 第三章\r\n结局允许留白。\r\n"
        self.prepare(raw)
        self.assertEqual(self.outline_bytes, raw.encode())
        self.drive(self.start(count=2))
        rows = self.core.workflow.chapters.catalog()
        self.assertEqual(rows[0]["title"], "蓝信")
        self.assertEqual(rows[0]["volume_title"], "雾港旧事")
        self.assertEqual([r["number"] for r in rows], [1, 2])
        restored = self.core.workflow.planning.restore(self.outline["version_id"])
        self.assertEqual(restored["text"], raw)

    def test_unknown_book_grows_volumes_from_existing_writer_response_only(self):
        self.prepare(macro(), finish_volume_at=1, book_at=3)
        run = self.drive(self.start("book"))
        rows = self.core.workflow.chapters.catalog()
        self.assertEqual([(r["volume"], r["number"]) for r in rows], [(1, 1), (2, 1), (3, 1)])
        self.assertEqual(run["completion_reason"], "story_complete")
        self.assertEqual(len(self.models.calls), 9)

    def test_volume_scope_stops_without_consuming_next_volume_authorization(self):
        self.prepare(macro(), finish_volume_at=2, book_at=5)
        run = self.drive(self.start("volumes", 1))
        rows = self.core.workflow.chapters.catalog()
        self.assertEqual(len(run["completed_chapters"]), 2)
        self.assertEqual(len([r for r in rows if r["draft"]]), 2)
        self.assertEqual(rows[-1]["volume"], 2)
        self.assertIsNone(rows[-1]["draft"])
        self.assertFalse(self.core.workflow.execution.describe()["story_complete"])

    def test_multiple_finite_runs_continue_same_volume_without_duplicate_records(self):
        self.prepare(macro(), finish_volume_at=3, book_at=3)
        for _ in range(3):
            self.drive(self.start(count=1))
        rows = self.core.workflow.chapters.catalog()
        self.assertEqual([r["number"] for r in rows], [1, 2, 3])
        self.assertEqual(self.models.writes, 3)
        self.assertEqual(len({r["chapter_id"] for r in rows}), 3)

    def test_failed_publication_replays_response_without_new_metadata_or_call(self):
        self.prepare(macro(), finish_volume_at=1, book_at=1)
        run = self.start()
        self.pipeline.advance(run["run_id"])
        with patch.object(self.core, "_artifact", side_effect=OSError("synthetic apply interruption")), self.assertRaises(OSError):
            self.pipeline.advance(run["run_id"])
        self.pipeline.resume(run["run_id"])
        self.drive(run)
        self.assertEqual(self.models.writes, 1)
        self.assertEqual(len(self.core.workflow.chapters.catalog()), 1)

    def test_author_can_rename_generated_directory_without_changing_outline_or_body(self):
        self.prepare(macro(), finish_volume_at=1, book_at=1)
        self.drive(self.start())
        row = self.core.workflow.chapters.catalog()[0]
        text = self.core.document(row["draft"]["document_id"])["text"]
        self.core.workflow.execution.retitle(row["chapter_id"], "作者重命名", "作者卷名")
        for _ in range(2):
            self.core.workflow.chapters.ensure()
        row = self.core.workflow.chapters.catalog()[0]
        self.assertEqual((row["title"], row["volume_title"]), ("作者重命名", "作者卷名"))
        self.assertEqual(self.core.document(row["draft"]["document_id"])["text"], text)
        self.unchanged_outline()

    def test_real_blockers_no_approval_corrupt_source_and_active_run_still_block(self):
        with self.assertRaises(CoreError):
            self.pipeline.preview()
        self.prepare(macro())
        run = self.start()
        with self.assertRaises(CoreError) as error:
            self.pipeline.preview()
        self.assertEqual(error.exception.code, "ACTIVE_RUN")
        self.pipeline.control(run["run_id"], "stop")
        with SafeTree(self.core.project) as tree:
            tree.publish(OUTLINE_TARGET, b"tampered", digest(self.outline_bytes))
        with self.assertRaises(CoreError):
            self.pipeline.preview()

    def test_bounded_unknown_book_stops_at_authorized_safety_ceiling_not_word_quota(self):
        with patch("novel_studio.workflow_execution.MAX_CHAPTERS", 3), patch("novel_studio.workflow_pipeline.MAX_CHAPTERS", 3):
            self.prepare(macro(), finish_volume_at=50, book_at=50)
            preview = self.pipeline.preview("book", 1)
            self.assertIsNone(preview["estimated_model_tasks"])
            self.assertEqual(preview["max_model_tasks"], 27)
            run = self.drive(self.pipeline.start(preview, True))
            self.assertEqual(run["completion_reason"], "authorized_range")
            self.assertFalse(self.core.workflow.execution.describe()["story_complete"])
            self.assertEqual(self.models.writes, 3)

    def test_old_detailed_catalog_migrates_without_changing_ids_or_reviews(self):
        self.prepare(fixture())
        self.drive(self.start(count=2))
        rows = self.core.workflow.chapters.catalog()
        self.core.workflow.store.mutate("synthetic_old_format", lambda state, tree: state.pop("execution_plans"))
        self.core.workflow.chapters.ensure()
        self.assertEqual(self.core.workflow.chapters.catalog(), rows)
        self.assertEqual(self.models.writes, 2)

    def test_legacy_length_pause_can_resume_without_accepting_a_length_veto(self):
        self.prepare(macro(), book_at=1, finish_volume_at=1)
        run = self.start()
        self.pipeline.advance(run["run_id"])
        self.core.workflow.store.mutate("synthetic_legacy_length_block", lambda state, tree: state["runs"][run["run_id"]].update(status="BLOCKED", block={"kind": "length", "message": "old length veto"}))
        self.pipeline.resume(run["run_id"])
        self.drive(run)
        self.assertEqual(self.models.writes, 1)

    def test_corrupt_execution_identity_is_a_real_blocker_not_auto_repaired(self):
        self.prepare(macro())
        self.core.workflow.chapters.ensure()
        cid = self.core.workflow.chapters.catalog()[0]["chapter_id"]
        self.core.workflow.store.mutate("synthetic_corruption", lambda state, tree: state["chapters"][cid].update(number=999))
        with self.assertRaises(CoreError) as error:
            self.pipeline.preview()
        self.assertEqual(error.exception.code, "EXECUTION_CONFLICT")

    def test_local_title_fallback_uses_heading_without_an_extra_model_request(self):
        self.prepare(macro(), book_at=1, finish_volume_at=1)
        original = self.models.structured_task
        def output(name, *args, **kwargs):
            result = original(name, *args, **kwargs)
            if name == "workflow_write":
                result["output"].update(title="", text="# 第一章 雨后的钟楼\n周岚把钟轮放在桌上。")
            return result
        self.models.structured_task = output
        self.drive(self.start())
        self.assertEqual(self.core.workflow.chapters.catalog()[0]["title"], "雨后的钟楼")
        self.assertEqual(len(self.models.calls), 3)

    def test_malformed_execution_result_is_retained_and_not_silently_applied(self):
        self.prepare(macro())
        original = self.models.structured_task
        def output(name, *args, **kwargs):
            result = original(name, *args, **kwargs)
            if name == "workflow_write":
                result["output"]["execution"]["volume_complete"] = "false"
            return result
        self.models.structured_task = output
        run = self.start()
        self.pipeline.advance(run["run_id"])
        with self.assertRaises(CoreError) as error:
            self.pipeline.advance(run["run_id"])
        self.assertEqual(error.exception.code, "INVALID_ARTIFACT")
        self.assertEqual(self.pipeline.get(run["run_id"])["status"], "FAILED")
        self.assertIsNone(self.core.workflow.chapters.catalog()[0]["draft"])

    def test_zero_unassigned_numbers_and_missing_output_title_do_not_block(self):
        value = macro(1)
        value["volumes"][0].update(number=0, title=None, chapters=[{"number": 0, "title": None}])
        self.prepare(value)
        original = self.models.structured_task
        def output(name, *args, **kwargs):
            result = original(name, *args, **kwargs)
            if name == "workflow_write":
                result["output"].pop("title")
                result["output"]["execution"].pop("volume_title")
            return result
        self.models.structured_task = output
        self.drive(self.start())
        row = self.core.workflow.chapters.catalog()[0]
        self.assertEqual((row["volume"], row["number"]), (1, 1))
        self.assertTrue(row["title"] and row["volume_title"])


if __name__ == "__main__":
    unittest.main()
