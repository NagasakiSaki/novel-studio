"""B02 offscreen author controls; no native desktop or real models."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from novel_studio.core import Core
import test_canonical_recovery as fixtures
try:
    from PySide6.QtWidgets import QApplication, QWidget, QMessageBox
    from novel_studio.desktop.canonical_recovery_ui import RecoveryDialog, export_diagnosis
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, 'Qt developer environment not installed')
class CanonicalRecoveryUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = fixtures.CanonicalRecoveryTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fixture.crash_before_index()
        self.core = self.fixture.core
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.p = SimpleNamespace(core=self.core, window=self.window, _guarded=lambda f:f(),
                                 refresh_view=lambda:None, refresh_recovery=lambda:None)

    def test_plain_language_confirm_only_repairs_directory(self):
        dialog = RecoveryDialog(self.p)
        self.addCleanup(dialog.deleteLater)
        text = dialog.summary.toPlainText()
        self.assertIn('作者批准：已核实', text)
        self.assertIn('正文已经保存', text)
        self.assertIn('不会修改任何正式正文', text)
        self.assertNotIn('INDEX_PENDING', text)
        self.assertTrue(dialog.confirm.isEnabled())
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            dialog.confirm.click()
        self.assertTrue(self.core.recovery.diagnose()['operations'])
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            dialog.confirm.click()
        self.assertEqual(self.core.recovery.diagnose()['operations'], [])

    def test_conflict_has_no_quick_recovery_and_export_contains_no_prose(self):
        chapter = self.core.workflow.store.read()['chapters'][self.fixture.cid]
        source = self.core.document(chapter['canonical']['document_id'])
        (self.core.project / source['relative']).write_text('external author prose', encoding='utf-8')
        dialog = RecoveryDialog(self.p)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.confirm.isEnabled())
        self.assertFalse(dialog.cancel_intent.isEnabled())
        self.assertIn('不能用旧记录覆盖', dialog.summary.toPlainText())
        target = self.core.project.parent / 'diagnosis.json'
        export_diagnosis(self.core, target)
        raw = target.read_bytes()
        self.assertNotIn(b'external author prose', raw)
        self.assertEqual(json.loads(raw)['project_id'], self.core.project_id)
        with self.assertRaises(FileExistsError):
            export_diagnosis(self.core, target)

    def test_unregistered_pending_is_explained_without_recovery_permission(self):
        self.fixture.fresh()
        self.fixture.point_crash('pending')
        self.core = self.p.core = self.fixture.core
        dialog = RecoveryDialog(self.p)
        self.addCleanup(dialog.deleteLater)
        self.assertIn('发布许可尚未完成', dialog.summary.toPlainText())
        self.assertFalse(dialog.confirm.isEnabled())
        self.assertTrue(dialog.cancel_intent.isEnabled())

    def test_startup_only_diagnoses_then_owned_timer_finishes_authorized_audit(self):
        preview = self.core.recovery.preview(self.fixture.row()['transaction_id'])
        def crash(point):
            if point == 'recovery_index':
                raise OSError('audit window')
        with patch('novel_studio.canonical_recovery.checkpoint', side_effect=crash), self.assertRaises(OSError):
            self.core.author_commit(preview['handle'], True)
        from novel_studio.desktop.window import Workbench
        from novel_studio.desktop.presenter import Presenter
        from ui2_fixtures import LocalOnlyCLI
        window = Workbench()
        self.addCleanup(window.deleteLater)
        p = Presenter(window, self.core.project.parent / 'app-state', LocalOnlyCLI())
        before = self.core.workflow.store.read()
        p.open_project(self.core.project)
        self.assertEqual(before, p.core.workflow.store.read())
        self.assertTrue(p.recovery_timer.isActive())
        self.assertIn('1 项待处理', p.recovery_button.text())
        p.retry_recovery_audits()
        self.assertFalse(p.recovery_timer.isActive())
        self.assertEqual(before, p.core.workflow.store.read())
        self.assertEqual(p.core.recovery.diagnose()['operations'], [])
        p.workflow_timer.stop()
