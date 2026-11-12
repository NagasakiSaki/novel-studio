"""Qt offscreen only; no native Windows/author input claims."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
import test_recovery_phase2 as fixtures
from novel_studio.core import CoreError
try:
    from PySide6.QtWidgets import QApplication, QWidget, QMessageBox
    from novel_studio.desktop.canonical_recovery_ui import PhaseTwoDialog
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, 'Qt is unavailable')
class PhaseTwoUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        fixture = fixtures.PhaseTwoTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.core = fixture.core
        self.cid = fixture.cid
        window = QWidget()
        self.addCleanup(window.deleteLater)
        self.p = SimpleNamespace(core=self.core, window=window, _guarded=lambda f:f(), refresh_view=lambda:None, refresh_recovery=lambda:None)
        self.dialog = PhaseTwoDialog(self.p)
        self.addCleanup(self.dialog.deleteLater)

    def test_real_qt_selection_confirmation_and_cancel(self):
        self.assertIn('正文保持不变', self.dialog.windowTitle())
        self.assertGreater(self.dialog.derived_chapter.count(), 0)
        before = self.core.workflow.store.read()['sequence']
        preview = self.core.recovery.derived_preview(self.cid)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            self.dialog.confirm(preview)
        self.assertEqual(self.core.workflow.store.read()['sequence'], before)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.dialog.confirm(self.core.recovery.derived_preview(self.cid))
        self.assertIn('正文没有改变', self.dialog.summary.toPlainText())

    def test_switched_project_rejected_and_batch_without_selection_is_not_success(self):
        with self.assertRaises(CoreError):
            self.dialog.recover_batches()
        self.p.core = None
        with self.assertRaises(CoreError):
            self.dialog.repair_derived()

    def test_visible_entry_and_bounded_controls(self):
        self.assertLessEqual(self.dialog.chapter.count(), 25)
        self.assertFalse(self.dialog.previous.isEnabled())
        self.assertFalse(self.dialog.following.isEnabled())
        self.dialog.show()
        self.app.processEvents()
        self.assertTrue(self.dialog.isVisible())
        self.dialog.reject()
        self.app.processEvents()
        self.assertFalse(self.dialog.isVisible())
