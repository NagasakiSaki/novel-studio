"""Actual native Qt widgets offscreen; no desktop interaction or model calls."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from novel_studio.desktop.window import Workbench
from novel_studio.desktop.lifecycle import dispose_windows


class ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.w = Workbench()
        self.w.resize(1440, 920)
        self.w.show()
        self.app.processEvents()

    def tearDown(self):
        dispose_windows(self.app)

    def test_default_is_one_navigation_with_no_model_toolbar_or_inspector(self):
        self.assertEqual(list(self.w.nav_dock.buttons), ["home", "create", "outline", "chapters", "chat", "materials", "export", "settings"])
        self.assertFalse(self.w.inspector_dock.expanded)
        self.assertFalse(self.w.model_controls.isVisible())
        self.assertFalse(self.w.tabs.tabBar().isVisible())

    def test_animated_rail_and_drawer_release_central_space(self):
        w = self.w
        initial = w.center_view.width()
        w.toggle_navigation()
        QTest.qWait(210)
        self.assertEqual(w.nav_dock.width(), 64)
        self.assertGreater(w.center_view.width(), initial)
        self.assertTrue(all(b.toolTip() for b in w.nav_dock.buttons.values()))
        w.toggle_drawer()
        QTest.qWait(210)
        self.assertTrue(w.inspector_dock.isVisible())
        self.assertGreaterEqual(w.inspector_dock.width(), 240)
        w.toggle_drawer()
        QTest.qWait(210)
        self.assertFalse(w.inspector_dock.isVisible())

    def test_bounded_preferences_restore_without_importing_old_dock_state(self):
        w = self.w
        w.nav_dock.expanded_width = 280
        w.nav_dock.set_expanded(False, False)
        w.navigate("outline")
        w.toggle_theme()
        saved = w.capture_layout()
        restored = Workbench()
        restored.restore_layout(saved)
        self.assertEqual(restored.theme, "dark")
        self.assertFalse(restored.nav_dock.expanded)
        self.assertEqual(restored.nav_dock.expanded_width, 280)
        self.assertEqual(restored.navigation_page, "outline")
        restored.restore_layout({"sidebar_width": 10000, "drawer_width": -1, "page": [], "docks": "untrusted legacy bytes"})
        self.assertEqual(restored.nav_dock.expanded_width, 360)
        self.assertEqual(restored.inspector_dock.expanded_width, 240)

    def test_narrow_layout_keeps_mouse_navigation_available(self):
        self.w.resize(900, 680)
        self.app.processEvents()
        self.assertFalse(self.w.nav_dock.expanded)
        self.assertTrue(self.w.nav_dock.buttons["settings"].isVisible())
        self.w.nav_dock.buttons["outline"].click()
        self.assertEqual(self.w.workflow_panel.pages.currentIndex(), 1)
        self.assertEqual(self.w.drawer_title.text(), "大纲信息")

    def test_role_compositions_remain_independent_in_the_one_chat_view(self):
        self.w.chat_inputs["writer"].setPlainText("主笔尚未发送")
        self.w.switch_role("editor")
        self.assertIs(self.w.tabs.currentWidget(), self.w.chat_container)
        self.assertTrue(self.w.editor.isReadOnly())
        self.assertEqual(self.w.chat_inputs["writer"].toPlainText(), "主笔尚未发送")


if __name__ == "__main__":
    unittest.main()
