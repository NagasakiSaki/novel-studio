"""Qt export intent, type/selection, preview invalidation and background output."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
try:
    from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog
    from PySide6.QtCore import Qt
    from novel_studio.core import Core, READ, WRITE
    from novel_studio.desktop.export_ui import ExportDialog
    from novel_studio.desktop.theme import apply_theme
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt developer environment not installed")
class ExportUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app, "light")

    def test_default_formal_draft_mark_preview_invalidation_and_background_save(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            core = Core.create(root, "导出界面合成作品", ROOT)
            d = core.dispatch(core.bind("writer", READ | WRITE), "create_draft", {"text": "# 雾桥\n林舟走向桥头，周岚留在灯下。"}, "export-ui-synthetic")
            dialog = ExportDialog(core, d["document_id"])
            self.assertEqual(dialog.scope.currentData(), "approved")
            self.assertEqual(dialog.sources.count(), 0)
            with patch.object(QMessageBox, "warning") as warning:
                dialog.build_preview()
                self.assertTrue(warning.called)
            dialog.scope.setCurrentIndex(dialog.scope.findData("draft"))
            dialog.format.setCurrentIndex(dialog.format.findData("txt"))
            dialog.build_preview()
            self.assertIn("未经批准", dialog.preview_browser.toPlainText())
            self.assertFalse(dialog.export_button.isEnabled())
            dialog.confirm.setChecked(True)
            self.assertTrue(dialog.export_button.isEnabled())
            dialog.fields["author"].setText("测试作者")
            self.assertIsNone(dialog.preview_value)
            self.assertFalse(dialog.export_button.isEnabled())
            dialog.build_preview()
            dialog.confirm.setChecked(True)
            output = root / "界面导出.txt"
            with patch.object(QFileDialog, "getSaveFileName", return_value=(str(output), "")):
                dialog.save_export()
            deadline = time.monotonic() + 10
            while dialog.job and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertIsNone(dialog.job)
            self.assertIn("测试作者", output.read_text(encoding="utf-8"))
            self.assertIn("未经批准", output.read_text(encoding="utf-8"))
            for theme in ("dark", "light"):
                apply_theme(self.app, theme)
                dialog.show()
                self.app.processEvents()
            dialog.hide()
            self.assertEqual(core.document(d["document_id"])["revision"], d["revision"])


if __name__ == "__main__":
    unittest.main()
