"""Complete synthetic multi-chapter runs, crash windows and author gates."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from novel_studio.core import Core, CoreError, SafeTree
from novel_studio.workflow_store import BASE, DEFAULT_SCALE
from test_workflow_planning import fixture
from test_workflow_review import review_fixture


class ScriptedModels:
    def __init__(self, core):
        self.core = core
        core.models = self
        self.config = {"writer": {"provider": "antigravity", "model_id": "writer-synthetic", "effort": "low"},
                       "editor": {"provider": "antigravity", "model_id": "editor-synthetic", "effort": "high"}}
        self.calls, self.hook, self.fail = [], None, None
        self.severe = False
    def selected(self, role):
        return dict(self.config[role])
    def structured_task(self, name, instructions, current, schema, **kwargs):
        self.calls.append({"task": name, "current": copy.deepcopy(current)})
        if self.hook:
            hook, self.hook = self.hook, None
            hook()
        if self.fail == name:
            self.fail = None
            raise CoreError("NETWORK_ERROR", "合成模型故障")
        role = "writer" if name in {"workflow_write", "workflow_revise", "author_revise"} else "editor"
        if name in {"workflow_plan", "author_plan"}:
            output = {"volume": current["chapter"]["volume"], "chapter": current["chapter"]["number"], "volume_title": "雾港", "title": "",
                "plot_goal": "交接钟轮，留下蓝信疑问", "events": "周岚交接物件，林舟检查", "character_actions": "林舟检查，周岚不解释",
                "emotional_change": "信任产生细小裂隙", "world_facts": "雾港有旧钟楼", "continuity": "林舟仍不知道蓝信内容",
                "ending_goal": "停在具体物件与人物距离，不总结", "length_reason": "合成短场景，避免填充", "word_range": {"minimum": 10, "target": 50, "maximum": 200}}
        elif name == "workflow_repair_plan":
            output = {"diagnosis": "知情范围不符", "preserve": "钟轮交接", "changes": "去掉林舟已知蓝信的描述", "acceptance_criteria": "知情范围与设定一致", "author_question": "", "requires_author_decision": False}
        elif name in {"workflow_write", "workflow_revise", "author_revise"}:
            output = {"title": "雨里的钟轮", "text": "# 雨里的钟轮\n周岚把钟轮放到桌上。林舟接过，拇指摸到齿间的锈。他们没有提那封蓝信。", "continuity_summary": "两人交换损坏的钟轮，未谈蓝信。"}
        else:
            output = review_fixture()
            if self.severe:
                output["current_objective"] = [{"title": "需要作者裁决的潜在身份冲突", "quote": "周岚把钟轮放到桌上。", "basis": "合成风险，不当作已证实事实。", "recommendation": "作者检查", "kind": "identity", "severity": "high", "certainty": "uncertain", "start_line": 2, "end_line": 2}]
        return {"output": output, "model": {"provider": self.config[role]["provider"], "requested_model": self.config[role]["model_id"], "conversation_id": role + "-synthetic-task"}}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-pipeline-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.core = Core.create(Path(self.temp.name), "合成流水线", ROOT)
        with SafeTree(self.core.project) as tree:
            tree.create("Canonical/Canon-Worldbuilding/设定.md", "雾港。林舟是修理工，周岚保管蓝信；林舟不知道信件内容。".encode())
        self.core.workflow.scale.save({**DEFAULT_SCALE, "total_chapters": 2, "chapter_minimum": 10, "chapter_target": 40, "chapter_maximum": 200})
        outline = self.core.workflow.planning.save(fixture())
        p = self.core.workflow.planning.preview(outline["version_id"])
        self.core.author_commit(p["handle"], True)
        self.models = ScriptedModels(self.core)
        self.pipeline = self.core.workflow.pipeline

    def start(self, mode="chapters", count=2):
        return self.pipeline.start(self.pipeline.preview(mode, count), True)

    def complete(self, run):
        for _ in range(30):
            row = self.pipeline.advance(run["run_id"])
            if row["status"] == "COMPLETED":
                return row
            self.assertEqual(row["status"], "RUNNING", row)
        self.fail("finite synthetic run did not finish")

    def test_two_chapters_review_direction_and_drafts_never_auto_accepted(self):
        before = {r["relative"]: r["revision"] for r in self.core.documents() if r["area"] == "canonical"}
        run = self.complete(self.start())
        self.assertEqual(len(run["completed_chapters"]), 2)
        self.assertEqual([r["task"] for r in self.models.calls], ["workflow_plan", "workflow_write", "workflow_review"] * 2)
        second = self.models.calls[4]["current"]
        self.assertTrue(second["guidance"])
        self.assertTrue(second["previous_editor_direction"])
        self.assertEqual(second["recent_manuscripts"][0]["authority"], "unapproved_draft")
        for chapter in self.core.workflow.chapters.catalog():
            self.assertEqual(chapter["status"], "AUTHOR_APPROVAL_PENDING")
            self.assertIsNone(chapter["canonical"])
            self.core.workflow.chapters.checked_document(chapter["draft"], "drafts")
        after = {r["relative"]: r["revision"] for r in self.core.documents() if r["area"] == "canonical"}
        self.assertEqual(before, after)

    def test_pause_during_request_keeps_result_then_resume_does_not_rewrite(self):
        run = self.start()
        self.models.hook = lambda: self.pipeline.control(run["run_id"], "pause")
        paused = self.pipeline.advance(run["run_id"])
        self.assertEqual(paused["status"], "PAUSED")
        self.assertEqual(len(self.models.calls), 1)
        self.assertTrue(self.core.workflow.chapters.catalog()[0]["editor_plan"])
        self.pipeline.resume(run["run_id"])
        self.complete(run)
        self.assertEqual(len([r for r in self.models.calls if r["task"] == "workflow_write"]), 2)

    def test_failed_review_requires_explicit_retry_and_keeps_completed_draft(self):
        run = self.start()
        self.pipeline.advance(run["run_id"])
        self.pipeline.advance(run["run_id"])
        self.pipeline.advance(run["run_id"])
        draft = self.core.workflow.chapters.catalog()[0]["draft"]
        self.models.fail = "workflow_review"
        with self.assertRaises(CoreError):
            self.pipeline.advance(run["run_id"])
        with self.assertRaises(CoreError):
            self.pipeline.resume(run["run_id"])
        self.pipeline.resume(run["run_id"], retry_confirmed=True)
        self.complete(run)
        self.assertEqual(draft, self.core.workflow.chapters.catalog()[0]["draft"])
        self.assertEqual(len([r for r in self.models.calls if r["task"] == "workflow_write"]), 2)

    def test_severe_review_pauses_for_author_and_never_rewrites_current_chapter(self):
        run = self.start(count=1)
        self.models.severe = True
        self.pipeline.advance(run["run_id"])
        self.pipeline.advance(run["run_id"])
        self.pipeline.advance(run["run_id"])
        draft = self.core.workflow.chapters.catalog()[0]["draft"]
        self.pipeline.advance(run["run_id"])
        blocked = self.pipeline.advance(run["run_id"])
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertEqual(blocked["block"]["kind"], "review")
        self.pipeline.resume(run["run_id"], accept_warning=True)
        self.complete(run)
        self.assertEqual(draft, self.core.workflow.chapters.catalog()[0]["draft"])

    def test_finite_chapter_volume_and_whole_book_ranges(self):
        self.assertEqual(self.pipeline.preview("chapters", 1)["chapter_count"], 1)
        self.assertEqual(self.pipeline.preview("volumes", 1)["chapter_count"], 2)
        self.assertEqual(self.pipeline.preview("book", 1)["chapter_count"], 2)
        for count in (0, -1, True, 1001):
            with self.assertRaises(CoreError):
                self.pipeline.preview("chapters", count)

    def test_model_change_requires_author_adoption_and_other_role_stays_independent(self):
        run = self.start(count=1)
        self.models.config["writer"]["model_id"] = "other-writer"
        blocked = self.pipeline.advance(run["run_id"])
        self.assertEqual(blocked["status"], "BLOCKED")
        with self.assertRaises(CoreError):
            self.pipeline.resume(run["run_id"])
        self.pipeline.resume(run["run_id"], adopt_models=True, accept_warning=True)
        self.assertEqual(self.models.config["editor"]["model_id"], "editor-synthetic")

    def test_stop_during_model_call_retains_output_and_does_not_schedule_next(self):
        run = self.start()
        self.models.hook = lambda: self.pipeline.control(run["run_id"], "stop")
        ended = self.pipeline.advance(run["run_id"])
        self.assertEqual(ended["status"], "STOPPED")
        self.pipeline.advance(run["run_id"])
        self.assertEqual(len(self.models.calls), 1)

    def test_saved_model_result_survives_apply_failure_without_another_model_request(self):
        run = self.start(count=1)
        self.pipeline.advance(run["run_id"])
        original = self.core._artifact
        with patch.object(self.core, "_artifact", side_effect=OSError("synthetic publication interruption")), self.assertRaises(OSError):
            self.pipeline.advance(run["run_id"])
        # A persisted result is discovered automatically; it is applied locally.
        self.pipeline.resume(run["run_id"])
        self.complete(run)
        self.assertEqual(len([r for r in self.models.calls if r["task"] == "workflow_write"]), 1)

    def test_changed_draft_preflight_stops_instead_of_scheduling_forever(self):
        run = self.start(count=1)
        self.pipeline.advance(run["run_id"])
        self.pipeline.advance(run["run_id"])
        row = self.core.document(self.core.workflow.chapters.catalog()[0]["draft"]["document_id"])
        with SafeTree(self.core.project) as tree:
            tree.publish(row["relative"], b'changed outside workflow', row["revision"])
        with self.assertRaises(CoreError):
            self.pipeline.advance(run["run_id"])
        self.assertEqual(self.pipeline.get(run["run_id"])["status"], "FAILED")
        self.pipeline.advance(run["run_id"])
        self.assertEqual(len(self.models.calls), 2)

    def test_raw_staged_result_replay_remains_bound_to_its_old_configuration(self):
        run = self.start(count=1)
        self.pipeline.advance(run["run_id"])
        with patch.object(self.core, "_artifact", side_effect=OSError("synthetic apply failure")), self.assertRaises(OSError):
            self.pipeline.advance(run["run_id"])
        self.models.config["writer"]["model_id"] = "other-writer"
        with self.assertRaises(CoreError) as captured:
            self.pipeline.resume(run["run_id"], adopt_models=True)
        self.assertEqual(captured.exception.code, "STALE_RESULT")
        self.assertEqual(len(self.models.calls), 2)


if __name__ == "__main__":
    unittest.main()
