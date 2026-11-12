"""Additive migration, transcripts, project boundaries, API diagnostic accounting."""
from contextlib import nullcontext
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_studio.core import Core, CoreError, SafeTree, READ, WRITE
from novel_studio.host import ModelService, OfficialCLI
from novel_studio.cache_policy import normalize_usage


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.core = Core.create(self.root, "整合合成工程", ROOT)
        self.models = ModelService(self.core)

    def test_additive_creative_settings_preserve_old_project_and_cli_state(self):
        c, m = self.core, self.models
        m.save("writer", cid="00000000-0000-4000-8000-000000000125", model_id="synthetic-old-model", effort="low", history=[{"user": "旧合成消息", "assistant": "旧回复"}])
        draft = c.dispatch(c.bind("writer", READ | WRITE), "create_draft", {"text": "# 旧项目\n旧正文不能变。"}, "integration-old-project")
        p = c.author_preview(draft["document_id"], filename="001.md")
        c.author_commit(p["handle"], True)
        before = {r["relative"]: r["revision"] for r in c.documents()}
        with SafeTree(c.project) as tree:
            project_before = tree.read(".novel-studio/project.json")
        from novel_studio.creative import template
        c.creative.save(template("细腻现实主义"))
        c.lengths.add("volume", "卷一")
        reopened = Core(c.project)
        restored = ModelService(reopened)
        self.assertEqual(restored.preferences("writer")["cid"], m.preferences("writer")["cid"])
        self.assertEqual(restored.preferences("writer")["history"][0]["user"], "旧合成消息")
        self.assertEqual(before, {r["relative"]: r["revision"] for r in reopened.documents()})
        with SafeTree(c.project) as tree:
            self.assertEqual(project_before, tree.read(".novel-studio/project.json"))

    def test_append_only_transcript_pagination_keeps_legacy_and_all_turns(self):
        self.models.save("writer", history=[{"user": "旧消息", "assistant": "旧答复"}])
        for i in range(22):
            self.models.append_history("writer", {"user": "作者消息" + str(i), "assistant": "回复", "provider": "antigravity"})
        first = self.models.transcript_page("writer")
        second = self.models.transcript_page("writer", first["cursor"])
        entries = first["entries"] + second["entries"]
        self.assertEqual(len(entries), 23)
        self.assertEqual(entries[0]["user"], "作者消息21")
        self.assertEqual(entries[-1]["user"], "旧消息")
        self.assertEqual(len(self.models.preferences("writer")["history"]), 15)
        self.assertEqual(self.models.transcript_page("editor")["entries"], [])
        state = self.models.preferences("writer")
        with SafeTree(self.core.project) as tree:
            path = ".novel-studio/Transcripts/writer/" + state["transcript_head"]["id"] + ".json"
            raw = tree.read(path)
            from novel_studio.core import digest
            tree.publish(path, b"{}", digest(raw))
        with self.assertRaises(CoreError):
            self.models.transcript_page("writer")

    def test_provider_status_does_not_publish_old_cli_identity_as_api(self):
        self.models.save("writer", cid="old-cli-cid", model_id="old-cli", provider="openai", api_configs={"openai": {"model_id": "synthetic-api", "effort": None}})
        rows = self.core.dispatch(self.core.bind("observer"), "list_agents", {})
        self.assertEqual(rows[0]["provider"], "openai")
        self.assertEqual(rows[0]["model"], "synthetic-api")
        self.assertIsNone(rows[0]["conversation_id"])
        self.assertEqual(self.models.preferences("writer")["cid"], "old-cli-cid")

    def test_numeric_cli_identity_does_not_grant_major_version_permission(self):
        cli = OfficialCLI()
        for version in ("1.5.0", "1.5.1", "1.5.3", "2.0.0"):
            with patch.object(cli, "command", return_value=version):
                self.assertEqual(cli.version(), version)
        from novel_studio.compatibility import evaluate, REQUIRED_FLAGS, CORE_CHECKS
        record={'flags':list(REQUIRED_FLAGS),'proof':{key:True for key in (*CORE_CHECKS,'session')}}
        report=evaluate(record,'2.0.0')
        self.assertFalse(report['model_allowed'])
        self.assertIn('MAJOR_REVIEW_REQUIRED',report['reasons'])
        with patch.object(cli.compatibility,'inspect',return_value=report), self.assertRaises(CoreError) as caught:
            cli.require_compatible()
        self.assertEqual(caught.exception.code,'CLI_INCOMPATIBLE')

    def test_api_author_task_counts_as_diagnostic_not_creative_session(self):
        usage = normalize_usage("openai", {"input_tokens": 10, "output_tokens": 3})
        class Turn:
            def __init__(self, *args):
                self.attempts, self.usage = 1, [usage]
            def request(self):
                return {"calls": [{"name": "deliver_style_preview", "arguments": {"text": "合成预览"}}], "model": "synthetic-model", "usage": usage, "latency_seconds": .01}
        registry = SimpleNamespace(authorize_send=lambda *args: "synthetic-reference", vault=SimpleNamespace(read=lambda *args: "SYNTHETIC-ONLY"),
            client_factory=lambda *args: nullcontext(object()), capability=lambda *args: {})
        self.models.attach_api(registry)
        self.models.save("writer", provider="openai", api_configs={"openai": {"model_id": "synthetic-model"}})
        with patch("novel_studio.providers.NativeTurn", Turn):
            value = self.models.structured_task("style_preview", "合成规则", {"scene": "新场景"}, {"type": "object"})
        self.assertEqual(value["output"]["text"], "合成预览")
        self.assertEqual(self.models.usage("writer")["diagnostic_run_count"], 1)
        self.assertEqual(self.models.usage("writer")["reported_run_count"], 0)
        with patch.object(Turn, "request", side_effect=CoreError("NETWORK_ERROR", "合成故障")), patch("novel_studio.providers.NativeTurn", Turn), self.assertRaises(CoreError):
            self.models.structured_task("style_preview", "规则", {"scene": "合成"}, {})
        self.assertEqual(self.models.usage("writer")["diagnostic_run_count"], 2)
        self.assertFalse(self.core._active)
        with SafeTree(self.core.project) as tree:
            self.assertNotIn(b"SYNTHETIC-ONLY", tree.read(".novel-studio/Desktop.json"))


class IntegrationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("Qt developer environment not installed")
        cls.app = QApplication.instance() or QApplication([])

    def test_cross_project_inputs_require_confirmation_and_are_cleared_only_after_success(self):
        from novel_studio.desktop.window import Workbench
        from novel_studio.desktop.presenter import Presenter
        from PySide6.QtWidgets import QMessageBox
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first, second = Core.create(root, "合成甲", ROOT), Core.create(root, "合成乙", ROOT)
            window = Workbench()
            self.addCleanup(window.deleteLater)
            presenter = Presenter(window, root / "state")
            presenter.open_project(first.project)
            window.chat_inputs["writer"].setPlainText("甲的未发送想法")
            window.chat_inputs["editor"].setPlainText("甲的未发送问题")
            with self.assertRaises(CoreError):
                presenter.open_project(second.project)
            with patch("novel_studio.desktop.presenter.QMessageBox.question", return_value=QMessageBox.No):
                self.assertFalse(presenter.confirm_discard())
            self.assertEqual(presenter.core.project, first.project)
            self.assertIn("甲的", window.chat_inputs["writer"].toPlainText())
            presenter.open_project(second.project, discard_inputs=True)
            self.assertEqual(presenter.core.project, second.project)
            self.assertFalse(window.chat_inputs["writer"].toPlainText())
            self.assertFalse(window.chat_inputs["editor"].toPlainText())
            self.assertIsNone(window.document_id)


if __name__ == "__main__":
    unittest.main()
