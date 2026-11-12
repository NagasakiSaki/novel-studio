"""Qt offscreen workflow operation tests; not native Windows/IME acceptance."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from novel_studio.core import Core, CoreError, SafeTree
from novel_studio.host import ModelService
from novel_studio.workflow_store import DEFAULT_SCALE
from test_workflow_planning import fixture, FakeCLI
from test_workflow_pipeline import ScriptedModels
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    from novel_studio.desktop.window import Workbench
    from novel_studio.desktop.presenter import Presenter
    from novel_studio.desktop.workflow_ui import StartDialog, ScaleDialog, OutlineEditDialog
    from novel_studio.desktop import workflow_controller
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt environment unavailable")
class WorkflowUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-workflow-qt-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.w = Workbench()
        self.addCleanup(self.w.deleteLater)
        self.p = Presenter(self.w, self.base / "state", FakeCLI(fixture()))
        self.core = Core.create(self.base, "合成作者界面", ROOT)
        with SafeTree(self.core.project) as tree:
            tree.create("Canonical/Canon-Worldbuilding/资料.md", "雾港，林舟和周岚；蓝信有知情边界。".encode())
        self.p.open_project(self.core.project)
        self.core = self.p.core
        self.core.models.save("writer", provider="antigravity", model_id="writer-synthetic")
        self.core.models.save("editor", provider="antigravity", model_id="editor-synthetic")
        self.core.workflow.scale.save({**DEFAULT_SCALE, "total_chapters": 2, "chapter_minimum": 10, "chapter_target": 40, "chapter_maximum": 200})

    def prepare_outline(self):
        row = self.core.workflow.planning.save(fixture())
        preview = self.core.workflow.planning.preview(row["version_id"])
        self.core.author_commit(preview["handle"], True)
        self.core.workflow.chapters.ensure()
        self.p.refresh_view()

    def tearDown(self):
        self.p.workflow_auto = False
        self.p.workflow_timer.stop()
        if self.p.job is not None:
            self.p.job.wait(30000)
        self.app.processEvents()

    def drain(self, predicate, seconds=90):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.01)
        run = self.core.workflow.pipeline.active()
        self.fail("Qt workflow did not reach the expected finite state: " + str({"status": run.get("status") if run else None,
            "index": run.get("index") if run else None, "error": run.get("error") if run else None,
            "job_running": bool(self.p.job and self.p.job.isRunning()), "auto": self.p.workflow_auto}))

    def test_primary_entry_shows_scope_and_legacy_dialog_remains_separate(self):
        self.prepare_outline()
        self.assertIs(self.w.tabs.currentWidget(), self.w.workflow_panel)
        self.assertTrue(self.w.workflow_panel.start_button.text().startswith('一键开始创作'))
        self.assertIn('全书',self.w.workflow_panel.home_task_scope.text())
        dialog = StartDialog(self.core, self.w)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.start.isEnabled())
        dialog.confirm.setChecked(True)
        self.assertTrue(dialog.start.isEnabled())
        dialog.count.setValue(2)
        self.assertIsNone(dialog.preview)
        self.assertFalse(dialog.start.isEnabled())
        dialog.prepare()
        self.assertEqual(dialog.preview["chapter_count"], 2)

    def test_outline_form_preserves_other_volumes_and_unsent_text_blocks_project_switch(self):
        value = fixture(2, 2)
        dialog = OutlineEditDialog(value, self.w)
        self.addCleanup(dialog.deleteLater)
        root = dialog.tree.topLevelItem(0)
        dialog.tree.setCurrentItem(root.child(1).child(0))
        dialog.editors["summary"].setPlainText("作者只改第二卷第一章")
        dialog.save()
        self.assertEqual(dialog.value["volumes"][0], value["volumes"][0])
        self.assertEqual(dialog.value["volumes"][1]["chapters"][1], value["volumes"][1]["chapters"][1])
        self.w.workflow_panel.instruction.setPlainText("尚未发送的大纲构思")
        other = Core.create(self.base, "另一部合成作品", ROOT)
        with self.assertRaises(CoreError):
            self.p.open_project(other.project)
        self.assertEqual(self.w.workflow_panel.instruction.toPlainText(), "尚未发送的大纲构思")

    def test_scale_dialog_shows_conflicts_without_silently_changing_inputs(self):
        dialog = ScaleDialog(self.core, self.w)
        self.addCleanup(dialog.deleteLater)
        dialog.fields["total_volumes"].setValue(10)
        self.assertEqual(dialog.fields["total_volumes"].value(), 10)
        self.assertIn("不同", dialog.explanation.toPlainText())
        self.assertEqual(self.core.workflow.scale.get()["total_volumes"], 1)

    def test_gui_controller_runs_two_chapters_then_author_batch_approval(self):
        self.prepare_outline()
        models = self.core.models
        scripted = ScriptedModels(self.core)
        self.core.models = models
        models.structured_task = scripted.structured_task
        errors = []
        self.w.workflow_panel.task_scope.setCurrentIndex(self.w.workflow_panel.task_scope.findData('chapters'))
        self.w.workflow_panel.task_count.setValue(2)
        with patch.object(workflow_controller.StartDialog, 'exec', side_effect=AssertionError('Routine start must not open a confirmation dialog')), patch.object(QMessageBox, "warning", side_effect=lambda *args: errors.append(str(args))):
            self.p.action("workflow_start", {})
            self.drain(lambda: self.core.workflow.pipeline.active()["status"] == "COMPLETED" and self.p.job is None)
        self.assertFalse(errors, errors)
        self.assertEqual(len(scripted.calls), 6)
        rows = self.core.workflow.chapters.catalog()
        self.assertTrue(all(r["canonical"] is None for r in rows))
        self.assertEqual(self.w.workflow_panel.table.rowCount(), 2)
        for index in range(2):
            self.w.workflow_panel.table.item(index, 0).setCheckState(Qt.Checked)
        ids = self.w.workflow_panel.checked_chapters()
        def numbering(core_rows, parent):
            from novel_studio.desktop.workflow_ui import NumberingDialog
            dialog = NumberingDialog(core_rows, parent)
            dialog.exec = lambda: QDialog.Accepted
            return dialog
        def approve_dialog(dialog):
            dialog.confirm.setChecked(True)
            self.assertTrue(dialog.approve.isEnabled())
            self.assertTrue(dialog.diff_view.toPlainText())
            return QDialog.Accepted
        with patch.object(workflow_controller, "NumberingDialog", numbering), patch.object(workflow_controller.DiffDialog, "exec", approve_dialog):
            self.p.action("workflow_accept", {"chapter_ids": ids})
        self.assertTrue(all(r["canonical"] for r in self.core.workflow.chapters.catalog()))
        self.assertEqual(self.w.workflow_panel.progress.value(), 100)

    def test_pause_control_is_available_while_the_shared_job_is_busy(self):
        self.prepare_outline()
        run = self.core.workflow.pipeline.start(self.core.workflow.pipeline.preview("chapters", 1), True)
        self.p.workflow_auto = True
        self.p.workflow_run = run["run_id"]
        self.p.job = object()
        try:
            self.p.action("workflow_pause", {})
            self.assertFalse(self.p.workflow_auto)
            self.assertEqual(self.core.workflow.pipeline.get(run["run_id"])["status"], "PAUSED")
        finally:
            self.p.job = None

    def test_first_ai_guide_configures_two_roles_and_approves_one_chapter(self):
        from novel_studio.desktop.first_ai import FirstCollaborationDialog
        from novel_studio.desktop.provider_ui import ProviderDialog
        from test_official_providers import MemoryVault

        fresh = Core.create(self.base, "第一次 AI 协作合成作品", ROOT)
        self.p.open_project(fresh.project)
        self.core = self.p.core
        self.p.providers._vault = MemoryVault()
        models = self.core.models
        models.catalog_rows = [{"model_id": "writer-synthetic", "display_name": "合成主笔"}]
        scripted = ScriptedModels(self.core)
        self.core.models = models
        outline_calls = []
        errors = []

        def transport(name, instructions, current, schema, **kwargs):
            for role in scripted.config:
                scripted.config[role] = models.selected(role)
            if name == "outline_plan":
                selected = scripted.config["editor"]
                self.p.providers.authorize_send(self.core, "editor", selected["provider"])
                outline_calls.append(current)
                return {"output": fixture(), "model": {"provider": selected["provider"], "requested_model": selected["model_id"]}}
            return scripted.structured_task(name, instructions, current, schema, **kwargs)

        models.structured_task = transport
        panel = self.w.workflow_panel

        def choose_model(dialog):
            if dialog.role.currentData() == "writer":
                dialog.model.setCurrentIndex(dialog.model.findData("writer-synthetic"))
            else:
                dialog.provider.setCurrentIndex(dialog.provider.findData("openai"))
                dialog.secret.setText("SYNTHETIC-CREDENTIAL")
                dialog.store_key()
                dialog.model.setCurrentText("editor-synthetic")
                dialog.apply.click()
                self.assertEqual(models.selected("editor")["provider"], "antigravity")
                dialog.consent.setChecked(True)
            dialog.apply.click()
            dialog.reject()
            return dialog.result()

        def prepare_outline(dialog):
            self.assertFalse(dialog.check.isEnabled())
            self.assertEqual(dialog.idea.toPlainText(), "先记下故事的开端。")
            dialog.configure_buttons["writer"].click()
            self.assertFalse(dialog.check.isEnabled())
            dialog.configure_buttons["editor"].click()
            self.assertTrue(dialog.check.isEnabled())
            dialog.idea.clear()
            dialog.check.click()
            self.assertIn("输入故事构思", dialog.summary.toPlainText())
            self.assertFalse(dialog.proceed.isEnabled())
            dialog.idea.setPlainText("修钟人发现全镇的钟同时停下。")
            dialog.check.click()
            preview = dialog.summary.toPlainText()
            self.assertIn("https://api.openai.com/v1", preview)
            self.assertIn("可能计费", preview)
            self.assertNotIn("SYNTHETIC-CREDENTIAL", preview)
            self.assertEqual(outline_calls, [])
            dialog.idea.insertPlainText("保留蓝信的秘密。")
            self.assertFalse(dialog.proceed.isEnabled())
            dialog.check.click()
            dialog.proceed.click()
            return dialog.result()

        def approve_diff(dialog):
            self.assertFalse(dialog.approve.isEnabled())
            self.assertTrue(dialog.diff_view.toPlainText())
            dialog.confirm.setChecked(True)
            dialog.approve.click()
            return dialog.result()

        def prepare_chapter(dialog):
            dialog.check.click()
            preview = dialog.summary.toPlainText()
            self.assertIn("第 1 卷第 1 章", preview)
            self.assertIn("Antigravity 官方宿主", preview)
            self.assertIn("https://api.openai.com/v1", preview)
            self.assertIn("最多 9 项模型任务", preview)
            self.assertEqual(scripted.calls, [])
            dialog.proceed.click()
            return dialog.result()

        def leave_without_sending(dialog):
            dialog.idea.setPlainText("先记下故事的开端。")
            dialog.reject()
            return dialog.result()

        with patch.object(self.p, "show_error", side_effect=errors.append), \
                patch.object(ProviderDialog, "exec", new=choose_model), \
                patch.object(workflow_controller.DiffDialog, "exec", new=approve_diff):
            self.w.navigate("home")
            with patch.object(FirstCollaborationDialog, "exec", new=leave_without_sending):
                panel.home_ai.click()
            self.assertEqual(outline_calls, [])
            with patch.object(FirstCollaborationDialog, "exec", new=prepare_outline):
                panel.home_ai.click()
            self.drain(lambda: self.p.job is None)
            self.assertEqual(errors, [])
            self.assertEqual(len(outline_calls), 1)
            self.assertEqual(outline_calls[0]["approved_context"], [])
            self.assertIsNone(self.core.workflow.store.read()["approved_outline"])
            panel.outline_approve.click()
            self.assertIsNotNone(self.core.workflow.planning.approved())
            with patch.object(FirstCollaborationDialog, "exec", new=prepare_chapter):
                panel.home_ai.click()
            self.drain(lambda: self.p.job is None and (self.core.workflow.pipeline.active() or {}).get("status") == "COMPLETED")
            self.assertEqual(errors, [])
            self.assertEqual([r["task"] for r in scripted.calls], ["workflow_plan", "workflow_write", "workflow_review"])
            self.assertFalse(panel.review_pending_button.isHidden())
            panel.review_pending_button.click()
            self.assertEqual(self.w.navigation_page, "chapters")
            self.assertEqual(panel.chapter_filter.currentData(), "pending")
            chapter, other = self.core.workflow.chapters.catalog()
            self.assertEqual(chapter["status"], "AUTHOR_APPROVAL_PENDING")
            self.assertIsNone(chapter["canonical"])
            self.assertIsNone(other["draft"])
            panel.table.item(0, 0).setCheckState(Qt.Checked)
            with patch.object(workflow_controller.NumberingDialog, "exec", new=lambda dialog: QDialog.Accepted):
                panel.accept_button.click()
            self.assertEqual(errors, [])
            chapter, other = self.core.workflow.chapters.catalog()
            self.assertTrue(chapter["canonical"])
            self.assertIsNone(other["canonical"])

    def test_outline_send_rejects_stale_model_binding(self):
        plan = self.core.workflow.planning
        binding, sources, _ = plan.preview_generation("合成故事构思")
        self.assertIn("Canonical/Canon-Worldbuilding/资料.md", sources["documents"])
        self.core.models.save("editor", model_id="changed-synthetic")
        with patch.object(self.core.models, "structured_task", side_effect=AssertionError("Stale preview must not send")):
            with self.assertRaises(CoreError) as failure:
                plan.generate("合成故事构思", preview_binding=binding)
        self.assertEqual(failure.exception.code, "REVISION_CONFLICT")
        self.assertEqual(self.core.workflow.store.read()["outline_jobs"], {})

    def test_close_before_queued_step_cancels_owned_timer_and_persists_pause(self):
        self.prepare_outline()
        run = self.core.workflow.pipeline.start(self.core.workflow.pipeline.preview("chapters", 1), True)
        workflow_controller.activate(self.p, run)
        self.assertTrue(self.p.workflow_timer.isActive())
        self.w.close()
        self.app.processEvents()
        self.assertFalse(self.p.workflow_timer.isActive())
        self.assertFalse(self.p.workflow_auto)
        self.assertIsNone(self.p.job)
        self.assertEqual(self.core.workflow.pipeline.get(run["run_id"])["status"], "PAUSED")


if __name__ == "__main__":
    unittest.main()
