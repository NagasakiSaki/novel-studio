"""Real Qt controls + Core, explicitly fake model transport and author choices."""
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
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from novel_studio.core import Core, SafeTree
from novel_studio.workflow_planning import OUTLINE_TARGET
from novel_studio.desktop.window import Workbench
from novel_studio.desktop.presenter import Presenter
from novel_studio.desktop.workflow_ui import StartDialog, ScaleDialog
from novel_studio.desktop.lifecycle import dispose_windows
from novel_studio.desktop import workflow_controller as controller
from ui2_fixtures import LocalOnlyCLI
from test_outline_first import macro, AdaptiveModels


class OutlineFirstUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-outline-first-qt-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.w = Workbench()
        self.p = Presenter(self.w, Path(self.temp.name) / "isolated-state", LocalOnlyCLI())
        self.w.show()

    def settle(self):
        loop = QEventLoop()
        QTimer.singleShot(20, loop.quit)
        loop.exec()

    def tearDown(self):
        self.p.workflow_auto = False
        self.p.workflow_timer.stop()
        if self.p.job:
            self.p.job.wait(30000)
        dispose_windows(self.app)

    def prepare(self, value):
        core = Core.create(Path(self.temp.name), "合成大纲优先界面", LocalOnlyCLI().installed_plugin())
        self.p.open_project(core.project)
        core = self.p.core
        core.models.save("writer", provider="antigravity", model_id="writer-synthetic")
        core.models.save("editor", provider="antigravity", model_id="editor-synthetic")
        models = core.models
        self.model = AdaptiveModels(core, sizes=[1800, 3200, 4500], finish_volume_at=3, book_at=3)
        core.models = models
        core.models.structured_task = self.model.structured_task
        candidate = core.workflow.planning.save(value)
        def approve(dialog):
            self.assertTrue(dialog.diff_view.toPlainText())
            self.assertFalse(dialog.approve.isEnabled())
            dialog.confirm.setChecked(True)
            return QDialog.Accepted
        with patch.object(controller.DiffDialog, "exec", approve):
            self.p.action("outline_approve", {"version_id": candidate["version_id"]})
        return core

    def test_macro_approval_and_start_dialog_have_no_detail_or_scale_blocker(self):
        core = self.prepare(macro(8))
        dialog = StartDialog(core, self.w)
        self.assertIsNotNone(dialog.preview)
        self.assertFalse(dialog.start.isEnabled())
        self.assertIn("自动规划和补全", dialog.summary.toPlainText())
        self.assertIn("优先采用", dialog.summary.toPlainText())
        self.assertNotIn("请先补全", dialog.summary.toPlainText())
        self.assertEqual(core.workflow.scale.get()["total_volumes"], 1)
        self.assertEqual(core.workflow.execution.describe()["volumes"], 8)
        dialog.confirm.setChecked(True)
        self.assertTrue(dialog.start.isEnabled())
        self.assertEqual(self.model.calls, [])

    def test_actual_controller_runs_elastic_chapters_without_length_approval(self):
        core = self.prepare(macro())
        with SafeTree(core.project) as tree:
            before = tree.read(OUTLINE_TARGET)
        self.w.workflow_panel.task_scope.setCurrentIndex(self.w.workflow_panel.task_scope.findData('chapters'))
        self.w.workflow_panel.task_count.setValue(3)
        errors = []
        with patch.object(controller.StartDialog, "exec", side_effect=AssertionError("No routine start dialog")), patch.object(QMessageBox, "warning", side_effect=lambda *args: errors.append(str(args))):
            self.p.action("workflow_start", {})
            deadline = time.monotonic() + 100
            while time.monotonic() < deadline:
                self.settle()
                if core.workflow.pipeline.active()["status"] == "COMPLETED" and self.p.job is None:
                    break
        self.assertFalse(errors, errors)
        self.assertEqual(core.workflow.pipeline.active()["status"], "COMPLETED")
        rows = core.workflow.chapters.catalog()
        self.assertEqual([r["local_length"]["count"] for r in rows], [1800, 3200, 4500])
        self.assertTrue(all(r["title"] and r["volume_title"] and r["canonical"] is None for r in rows))
        self.assertEqual(self.w.workflow_panel.table.rowCount(), 3)
        with SafeTree(core.project) as tree:
            self.assertEqual(tree.read(OUTLINE_TARGET), before)

    def test_settings_and_explicit_author_directory_retitle(self):
        core = self.prepare(macro())
        dialog = ScaleDialog(core, self.w)
        from PySide6.QtWidgets import QLabel
        self.assertTrue(any("篇幅设置仅供 AI 创作参考" in label.text() for label in dialog.findChildren(QLabel)))
        row = core.workflow.chapters.catalog()[0]
        with patch.object(controller.QInputDialog, "getText", side_effect=[("作者选择的标题", True), ("作者选择的卷名", True)]):
            self.p.action("workflow_rename", {"chapter_id": row["chapter_id"]})
        changed = core.workflow.chapters.catalog()[0]
        self.assertEqual((changed["title"], changed["volume_title"]), ("作者选择的标题", "作者选择的卷名"))
        self.assertEqual(self.model.calls, [])


if __name__ == "__main__":
    unittest.main()
