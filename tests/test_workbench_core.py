"""Core safety contract, synthetic projects only; no host/network calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_studio.core import Core, CoreError, Capability, READ, WRITE, SafeTree, admin, digest
from novel_studio.bridge import handle
from novel_studio.usage import summarize


class CoreWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = Core.create(Path(self.temp.name), "合成测试小说", ROOT)
        self.writer = self.core.bind("writer", READ | WRITE)
        self.editor = self.core.bind("editor", READ | WRITE)

    def draft(self, text="第一章：铜钥匙。"):
        return self.core.dispatch(self.writer, "create_draft", {"text": text}, uuid.uuid4().hex)

    def test_author_workflow_and_stale_approval(self):
        draft = self.draft()
        self.core.snapshot()
        self.core.handoff(draft["document_id"])
        report = self.core.dispatch(self.editor, "create_review_report", {"text": "【客观】时间线矛盾，证据：六月四日/五日。\n【作者待决】钟楼年份。"}, uuid.uuid4().hex)
        self.core.feedback(report["document_id"], "仅采纳日期核对，年份由作者另定。")
        plan = self.core.author_preview(draft["document_id"])
        first = self.core.author_commit(plan["handle"], True)
        revised = self.core.dispatch(self.writer, "update_draft", {"document_id": draft["document_id"], "expected_revision": draft["revision"], "text": "修订稿：五月三日，铜钥匙。"}, uuid.uuid4().hex)
        plan = self.core.author_preview(draft["document_id"])
        self.core.author_commit(plan["handle"], True)
        version = self.core.history()[0]
        # Phase-two safety contract: this old standalone promote flow has no
        # unique Workflow chapter binding. It must not bypass the coordinator.
        # Positive approved Restore is exercised by test_workflow_chapters.
        formal = "Canonical/Final-Manuscript/" + self.core.document(draft["document_id"])["title"]
        with SafeTree(self.core.project) as tree:
            before = tree.read(formal)
            receipt = tree.read('Backups/Transactions/' + version['version_id'] + '/COMMITTED.json')
        with self.assertRaises(CoreError) as blocked:
            self.core.author_preview(transaction=version["version_id"])
        self.assertEqual(blocked.exception.code, 'RECOVERY_CONFLICT')
        with SafeTree(self.core.project) as tree:
            self.assertEqual(tree.read(formal), before)
            self.assertEqual(before.decode(), '修订稿：五月三日，铜钥匙。')
            self.assertEqual(tree.read('Backups/Transactions/' + version['version_id'] + '/COMMITTED.json'), receipt)
        self.assertEqual(len(self.core.history()), 2)
        with self.assertRaises(CoreError):
            self.core.author_commit(plan["handle"], True)
        stale = self.core.author_preview(draft["document_id"])
        self.core.dispatch(self.writer, "update_draft", {"document_id": draft["document_id"], "expected_revision": revised["revision"], "text": "新的未批准修订"}, uuid.uuid4().hex)
        with self.assertRaises(ValueError):
            self.core.author_commit(stale["handle"], True)

    def test_editor_cannot_write_or_read_writer_draft(self):
        draft = self.draft()
        for method, params in [("create_draft", {"text": "越权"}), ("read_document", {"document_id": draft["document_id"]}),
                               ("update_draft", {"document_id": draft["document_id"], "expected_revision": draft["revision"], "text": "越权"})]:
            with self.assertRaises(CoreError):
                self.core.dispatch(self.editor, method, params, uuid.uuid4().hex)
        self.assertEqual(self.core.document(draft["document_id"])["revision"], draft["revision"])

    def test_idempotency_revision_and_dry_run(self):
        key = uuid.uuid4().hex
        first = self.core.dispatch(self.writer, "create_draft", {"text": "甲"}, key)
        self.assertEqual(first, self.core.dispatch(self.writer, "create_draft", {"text": "甲"}, key))
        with self.assertRaisesRegex(CoreError, "不同内容"):
            self.core.dispatch(self.writer, "create_draft", {"text": "乙"}, key)
        self.core.dispatch(self.writer, "create_draft", {"text": "不落盘", "dry_run": True}, uuid.uuid4().hex)
        self.assertEqual(len(self.core.documents("writer")), 1)
        with self.assertRaisesRegex(CoreError, "阻止覆盖"):
            self.core.dispatch(self.writer, "update_draft", {"document_id": first["document_id"], "text": "乙", "expected_revision": "0" * 64}, uuid.uuid4().hex)

    def test_bridge_default_readonly_and_no_impersonation(self):
        cap = self.core.bind("observer")
        request = {"protocol_version": "1", "request_id": "r1", "correlation_id": "c1", "method": "create_draft", "params": {"text": "越权"}, "idempotency_key": uuid.uuid4().hex}
        self.assertEqual(handle(self.core, cap, request)["error"]["code"], "ROLE_DENIED")
        request["role"] = "author"
        self.assertEqual(handle(self.core, cap, request)["error"]["code"], "INVALID_REQUEST")
        request.pop("role")
        request.update(method="author_commit", params={})
        self.assertFalse(handle(self.core, cap, request)["ok"])
        request.update(method="get_status", params={})
        self.assertTrue(handle(self.core, cap, request)["ok"])
        alien = Capability("other-project", "writer", READ | WRITE)
        self.assertEqual(handle(self.core, alien, request)["error"]["code"], "PROJECT_SCOPE_DENIED")

    def test_busy_does_not_allow_bridge_mutation(self):
        self.core._active = True
        with self.assertRaises(CoreError):
            self.draft()

    def test_bridge_schema_matches_actual_routes(self):
        schema = json.loads((ROOT / "schema/bridge-request-v1.json").read_text(encoding="utf-8"))
        self.assertEqual({b["properties"]["method"]["const"] for b in schema["oneOf"]}, READ | WRITE)

    def test_usage_missing_duplicate_and_invalid_denominator(self):
        row = {"run_id": "run", "role": "writer", "usage": {"input_tokens": 10, "cache_read_tokens": 30}}
        data = summarize([row, row], "writer")
        self.assertEqual(data["reported_run_count"], 1)
        self.assertIsNone(data["hit_rate"])
        self.assertIsNone(data["latest"]["output_tokens"])
        self.assertIsNone(summarize([], "editor")["latest"]["cache_read_tokens"])

    def test_cumulative_usage_resume_switch_and_reset(self):
        first = {"run_id": "1", "cid": "same", "role": "writer", "num_turns": 1, "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 300}}
        second = {"run_id": "2", "cid": "same", "role": "writer", "num_turns": 2, "requested_model": "new-model", "usage": {"input_tokens": 130, "output_tokens": 25, "cache_read_tokens": 800}}
        data = summarize([first, second, second], "writer")
        self.assertEqual(data["latest"]["input_tokens"], 30)
        self.assertEqual(data["latest"]["cache_read_tokens"], 500)
        self.assertEqual(data["cumulative"]["input_tokens"], 130)
        self.assertIsNone(data["hit_rate"])
        reset = {**second, "run_id": "3", "num_turns": 1, "usage": {"input_tokens": 5}}
        self.assertIsNone(summarize([first, second, reset], "writer")["latest"]["input_tokens"])
        self.assertIn("RESET", summarize([first, second, reset], "writer")["scope"])


if __name__ == "__main__":
    unittest.main()
