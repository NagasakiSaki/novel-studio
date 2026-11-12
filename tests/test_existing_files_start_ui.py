"""Regress the actual disabled-start dialog with a populated synthetic Canonical."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from PySide6.QtWidgets import QApplication
from novel_studio.core import Core, SafeTree
from novel_studio.desktop.window import Workbench
from novel_studio.desktop.presenter import Presenter
from novel_studio.desktop.workflow_ui import StartDialog
from novel_studio.desktop.lifecycle import dispose_windows
from ui2_fixtures import LocalOnlyCLI
from test_outline_first import macro


class ExistingFilesUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_existing_files_leave_real_start_dialog_enabled_after_author_confirmation(self):
        with tempfile.TemporaryDirectory(prefix="novel-existing-start-qt-synthetic-") as folder:
            w = Workbench()
            p = Presenter(w, Path(folder) / "isolated-state", LocalOnlyCLI())
            core = Core.create(Path(folder), "合成现有文件界面", LocalOnlyCLI().installed_plugin())
            p.open_project(core.project)
            core = p.core
            core.models.save("writer", provider="antigravity", model_id="writer-synthetic")
            core.models.save("editor", provider="antigravity", model_id="editor-synthetic")
            row = core.workflow.planning.save(macro(8))
            core.author_commit(core.workflow.planning.preview(row["version_id"])["handle"], True)
            with SafeTree(core.project) as tree:
                tree.create("Canonical/Final-Manuscript/独立旧稿.md", "合成旧稿，不得触碰。".encode())
                tree.create("Canonical/Final-Manuscript/README.md", "合成说明。".encode())
                tree.create("Canonical/Final-Manuscript/service.json", b'{}')
            before = {r["relative"]: r["revision"] for r in core.documents() if r["area"] == "canonical"}
            try:
                dialog = StartDialog(core, w)
                self.assertIsNotNone(dialog.preview)
                self.assertFalse(dialog.start.isEnabled())
                self.assertIn("先写独立草稿", dialog.summary.toPlainText())
                self.assertNotIn("确认映射后开始", dialog.summary.toPlainText())
                dialog.confirm.setChecked(True)
                self.assertTrue(dialog.start.isEnabled())
                self.assertEqual(len(dialog.preview["unplaced_files"]), 1)
                self.assertEqual(before, {r["relative"]: r["revision"] for r in core.documents() if r["area"] == "canonical"})
            finally:
                dispose_windows(self.app)


if __name__ == "__main__":
    unittest.main()
