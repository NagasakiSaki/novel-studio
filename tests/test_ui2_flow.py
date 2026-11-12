import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from PySide6.QtWidgets import QApplication, QDialogButtonBox
from PySide6.QtTest import QTest
from novel_studio.desktop.window import Workbench
from novel_studio.desktop.presenter import Presenter
from novel_studio.desktop.lifecycle import dispose_windows
from novel_studio.desktop.workflow_ui import StartDialog
from ui2_fixtures import LocalOnlyCLI, project


class FlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-ui2-flow-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.w = Workbench()
        self.p = Presenter(self.w, Path(self.temp.name) / "state", LocalOnlyCLI())
        self.core, self.model = project(Path(self.temp.name), presenter=self.p)
        self.w.show()
        self.app.processEvents()

    def tearDown(self):
        if self.p.job:
            self.p.job.wait(10000)
        self.p.workflow_auto = False
        self.p.workflow_timer.stop()
        dispose_windows(self.app)

    def test_changed_range_recalculates_without_an_extra_button_or_retaining_approval(self):
        dialog = StartDialog(self.core, self.w)
        dialog.confirm.setChecked(True)
        self.assertTrue(dialog.start.isEnabled())
        dialog.count.setValue(2)
        self.assertFalse(dialog.start.isEnabled())
        dialog.confirm.setChecked(True)  # Author cannot approve an unseen future summary.
        QTest.qWait(230)
        self.assertEqual(dialog.preview["chapter_count"], 2)
        self.assertFalse(dialog.confirm.isChecked())
        self.assertFalse(dialog.start.isEnabled())
        dialog.confirm.setChecked(True)
        self.assertTrue(dialog.start.isEnabled())
        self.assertEqual(self.model.calls, [])

    def test_background_work_keeps_navigation_live_and_close_guarded(self):
        gate = threading.Event()
        self.p.start_job(lambda progress: gate.wait(10), lambda value: None, "合成后台操作")
        try:
            expanded = self.w.nav_dock.expanded
            self.w.nav_dock.buttons["outline"].click()
            self.w.nav_toggle.click()
            QTest.qWait(200)
            self.assertEqual(self.w.navigation_page, "outline")
            self.assertNotEqual(self.w.nav_dock.expanded, expanded)
            with patch("PySide6.QtWidgets.QMessageBox.information"):
                self.assertFalse(self.w.close())
            self.assertEqual(self.model.calls, [])
        finally:
            gate.set()
        deadline = time.monotonic() + 5
        while self.p.job is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.p.job)

    def test_reduced_motion_and_contexts_survive_page_changes_without_model_requests(self):
        self.w.reduce_motion.setChecked(True)
        self.w.nav_toggle.click()
        self.app.processEvents()
        self.assertEqual(self.w.nav_dock.animation.duration(), 0)
        self.assertTrue(self.w.capture_layout()["reduce_motion"])
        self.w.navigate("outline")
        self.assertIn("大纲", self.w.drawer_title.text())
        self.w.navigate("materials")
        self.assertIn("资料", self.w.drawer_title.text())
        self.assertEqual(self.model.calls, [])

    def test_home_and_settings_keep_daily_toolbar_actions_out_of_the_main_surface(self):
        self.w.navigate("home")
        for button in (self.w.refresh_models_button, self.w.connection_button, self.w.writer_settings_button, self.w.history_button, self.w.new_project_button):
            self.assertFalse(button.isVisible())
        self.w.navigate("settings")
        self.assertFalse(self.w.model_controls.isVisible())
        self.assertEqual(self.w.settings_categories.count(), 6)
        self.assertFalse(self.w.nav_toggle.icon().isNull())

    def test_native_standard_buttons_are_chinese_without_changing_author_text(self):
        box = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Yes | QDialogButtonBox.No, self.w)
        self.assertEqual(box.button(QDialogButtonBox.Cancel).text(), "取消")
        self.assertEqual(box.button(QDialogButtonBox.Yes).text(), "是")
        self.w.chat_inputs["writer"].setPlainText("No 是作者在正文里保留的词。")
        self.assertEqual(self.w.chat_inputs["writer"].toPlainText(), "No 是作者在正文里保留的词。")


if __name__ == "__main__":
    unittest.main()
