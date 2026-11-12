"""Synthetic version/approval/role tests; no real model, API key or private novel."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_studio.core import Core, CoreError, SafeTree, READ, WRITE
from novel_studio.host import ModelService
from novel_studio.workflow_planning import merge_scope, validate_outline, OUTLINE_TARGET
from novel_studio.workflow_store import BASE


def fixture(volumes=1, chapters=2):
    return {"concept": "雾港钟楼", "theme": "记忆和选择", "synopsis": "两人修理钟楼并决定是否公开旧信。",
        "characters": "修理工林舟、保管信件的周岚", "conflicts": "知情与沉默的代价", "direction": "从误解到具体选择", "ending": "选择公开信件但保留复杂后果",
        "volumes": [{"number": v, "title": "雾港", "summary": "修理钟楼。", "milestones": "发现蓝信", "relationships": "从怀疑到合作", "foreshadowing": "铜钥匙晚些处理",
            "chapters": [{"number": c, "title": f"合成章节{c}", "summary": "两人在雨中交谈。", "events": "周岚交出损坏的钟轮。", "characters": "林舟、周岚", "relationships": "信任发生微小变化", "foreshadowing": "未回收的铜钥匙", "constraints": "林舟不知道蓝信内容。"} for c in range(1, chapters + 1)]} for v in range(1, volumes + 1)]}


class FakeCLI:
    def __init__(self, output):
        self.output, self.calls = output, []
    def version(self):
        return "1.5.1"
    def stream(self, args, cwd, prompt, notify=None, evidence=None):
        self.calls.append({"args": args, "prompt": prompt, "cwd": str(cwd)})
        return {"init": {"model": "editor-test"}, "result": {"structured_output": self.output, "conversation_id": str(uuid.uuid4()), "usage": {}}, "latency_seconds": 0.01}


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-workflow-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.core = Core.create(Path(self.temp.name), "合成大纲", ROOT)
        with SafeTree(self.core.project) as tree:
            tree.create("Canonical/Canon-Worldbuilding/设定.md", "雾港，钟楼，林舟尚不知道蓝信内容。".encode())
        self.service = self.core.workflow.planning
        self.core.workflow.store.mutate("scale_test", lambda s, t: s["scale"].update(total_chapters=2))

    def test_empty_idea_uses_editor_model_and_fixed_no_file_tools(self):
        cli = FakeCLI(fixture())
        models = ModelService(self.core, cli)
        models.save("editor", provider="antigravity", model_id="editor-test", effort="low")
        models.save("writer", provider="antigravity", model_id="writer-test")
        result = self.service.generate("")
        self.assertEqual(len(cli.calls), 1)
        self.assertIn("editor-test", cli.calls[0]["args"])
        self.assertNotIn("writer-test", cli.calls[0]["args"])
        self.assertIn("novel-editor", cli.calls[0]["args"])
        args = cli.calls[0]["args"]
        settings = json.loads(args[args.index("--settings") + 1])
        self.assertIn("write_file(*)", settings["permissions"]["deny"])
        self.assertIn("read_file(*)", settings["permissions"]["deny"])
        self.assertFalse(self.core.workflow.store.read()["approved_outline"])
        self.assertEqual(result["model"]["provider"], "antigravity")
        self.assertEqual(models.selected("writer")["model_id"], "writer-test")

    def test_blank_planning_retrieves_preset_facts_without_marking_them_always(self):
        source = Path(self.temp.name) / "综合预设.md"
        source.write_text("# 合成预设\n## 全局规则\n克制叙事。\n## 人物\n林舟是修理工，周岚保管蓝信。\n## 世界观\n雾港钟楼。\n## 时间线\n林舟6月4日回港。\n", encoding="utf-8")
        item = self.core.materials.prepare(source, "global_preset")
        always = [s["id"] for s in item.parsed["sections"] if s["title"] == "全局规则"]
        preview = self.core.materials.preview(item, always, "retrieval", True)
        self.core.author_commit(preview["handle"], True)
        cli = FakeCLI(fixture())
        models = ModelService(self.core, cli)
        models.save("editor", provider="antigravity", model_id="editor-test")
        self.service.generate("")
        state = self.core.workflow.store.read()
        request = self.core.workflow.store.blob(next(iter(state["task_inputs"].values())))
        self.assertEqual(request["current"]["author_idea_or_revision"], "")
        self.assertIn("林舟是修理工", request["current"]["retrieved_references"])
        self.assertIn("6月4日", request["current"]["retrieved_references"])
        self.assertNotIn("林舟是修理工", request["current"]["approved_always_rules"])
        self.assertIn("克制叙事", request["current"]["approved_always_rules"])

    def test_scoped_volume_and_chapter_changes_preserve_every_other_field(self):
        old = fixture(2, 2)
        change = copy.deepcopy(old["volumes"][1]); change["summary"] = "另一卷的作者指定修改"
        merged = merge_scope(old, change, {"kind": "volume", "volume": 2})
        self.assertEqual(merged["volumes"][0], old["volumes"][0])
        self.assertEqual({k: merged[k] for k in old if k != "volumes"}, {k: old[k] for k in old if k != "volumes"})
        chapter = copy.deepcopy(merged["volumes"][1]["chapters"][0]); chapter["events"] = "改变指定事件"
        final = merge_scope(merged, chapter, {"kind": "chapter", "volume": 2, "chapter": 1})
        self.assertEqual(final["volumes"][1]["chapters"][1], old["volumes"][1]["chapters"][1])
        self.assertEqual(old["volumes"][1]["summary"], "修理钟楼。")

    def test_history_diff_restore_creates_a_new_unapproved_version(self):
        first = self.service.save(fixture())
        value = fixture(); value["ending"] = "作者指定另一种开放结局"
        second = self.service.save(value, base_version=first["version_id"])
        self.assertIn("开放结局", self.service.diff(first["version_id"], second["version_id"]))
        restored = self.service.restore(first["version_id"])
        self.assertNotEqual(restored["version_id"], first["version_id"])
        self.assertEqual(restored["value"], first["value"])
        self.assertEqual(len(self.service.versions()), 3)
        self.assertIsNone(self.core.workflow.store.read()["approved_outline"])

    def test_only_exact_author_approval_writes_existing_canonical_system(self):
        row = self.service.save(fixture())
        with self.assertRaises(CoreError):
            self.service.approved()
        preview = self.service.preview(row["version_id"])
        with SafeTree(self.core.project) as tree:
            self.assertIsNone(tree.maybe_read(OUTLINE_TARGET))
        self.core.author_commit(preview["handle"], True)
        self.assertEqual(self.service.approved()["version_id"], row["version_id"])
        self.assertTrue(self.core.history())
        with self.assertRaises(CoreError):
            self.core.dispatch(self.core.bind("editor", READ | WRITE), "author_commit", {"confirmed": True})

    def test_missing_details_are_advisory_but_changed_source_still_blocks_approval(self):
        value = fixture(); value["volumes"][0]["chapters"] = []
        empty = self.service.save(value)
        self.assertTrue(self.service.preview(empty["version_id"])["warnings"])
        good = self.service.save(fixture())
        preview = self.service.preview(good["version_id"])
        with SafeTree(self.core.project) as tree:
            tree.create(OUTLINE_TARGET, "外部变化".encode())
        with self.assertRaises((CoreError, ValueError)):
            self.core.author_commit(preview["handle"], True)
        self.assertIsNone(self.core.workflow.store.read()["approved_outline"])

    def test_versions_verify_hash_and_execution_can_normalize_duplicate_source_numbers(self):
        value = fixture(); value["volumes"][0]["chapters"][1]["number"] = 1
        self.assertEqual(validate_outline(value)["volumes"][0]["chapters"][1]["number"], 1)
        row = self.service.save(fixture())
        record = self.core.workflow.store.read()["outlines"][row["version_id"]]
        with SafeTree(self.core.project) as tree:
            tree.publish(record["path"], b'{}', record["sha256"])
        with self.assertRaises(CoreError):
            self.service.get(row["version_id"])

    def test_staged_response_recovers_without_another_model_call(self):
        cli = FakeCLI(fixture())
        models = ModelService(self.core, cli)
        models.save("editor", provider="antigravity", model_id="editor-test")
        with patch.object(type(self.service), "save", side_effect=OSError("synthetic apply interruption")), self.assertRaises(OSError):
            self.service.generate("合成中断")
        attempt = next(iter(self.core.workflow.store.read()["outline_jobs"]))
        record = self.service.recover(attempt)
        again = self.service.recover(attempt)
        self.assertEqual(record, again)
        self.assertEqual(len(self.service.versions()), 1)
        self.assertEqual(len(cli.calls), 1)


if __name__ == "__main__":
    unittest.main()
