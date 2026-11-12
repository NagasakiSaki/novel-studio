"""Existing-source Qt flow and safe scene input, offscreen synthetic only."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from novel_studio.core import Core, READ, WRITE
    from novel_studio.host import ModelService
    from novel_studio.desktop.style_analysis_ui import StyleAnalysisDialog
    from novel_studio.desktop.theme import apply_theme
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt developer environment not installed")
class StyleAnalysisUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_existing_source_local_report_profile_does_not_auto_bind(self):
        with tempfile.TemporaryDirectory() as folder:
            core = Core.create(Path(folder), "风格分析界面合成工程", ROOT)
            ModelService(core)
            draft = core.dispatch(core.bind("writer", READ | WRITE), "create_draft", {"text": "# 参考文稿\n林舟站在灯下。\n\n周岚说：“再等一等。”"}, "synthetic-style-ui")
            dialog = StyleAnalysisDialog(core)
            selector = dialog.selectors["primary"]
            selector.setCurrentIndex(selector.findData("d_" + draft["document_id"]))
            self.assertIn("林舟", dialog.source_previews["primary"].toPlainText())
            dialog.begin_analysis(False)
            deadline = time.monotonic() + 8
            while dialog.job is not None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertIsNone(dialog.job)
            self.assertIsNotNone(dialog.record)
            self.assertIn("仅本地统计", dialog.report_view.toPlainText())
            self.assertIn("不是中文词汇丰富度", dialog.report_view.toPlainText())
            dialog.build_profile()
            self.assertIsNotNone(dialog.profile_editor.value().get("origin"))
            self.assertEqual(core.creative.context("writer"), ({}, {}))
            apply_theme(self.app, "dark")
            apply_theme(self.app, "light")
            dialog.scene.setPlainText("尚未保存的新场景输入")
            with patch("novel_studio.desktop.style_analysis_ui.QMessageBox.question", return_value=QMessageBox.No):
                self.assertFalse(dialog.may_leave_scene())
            dialog.scene.clear()
            dialog.reject()
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
