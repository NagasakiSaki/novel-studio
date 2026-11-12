"""B01 bounded archive controls, offscreen Qt only; no real model calls."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from novel_studio.core import Core, SafeTree, admin
try:
    from PySide6.QtWidgets import QApplication, QWidget, QDialog, QListWidget, QComboBox, QPlainTextEdit, QPushButton, QMessageBox
    from novel_studio.desktop.object_controller import history, snapshot_versions
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, 'Qt developer environment not installed')
class SnapshotHistoryUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='b01-ui-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.core = Core.create(Path(self.temp.name), 'synthetic', ROOT)
        with SafeTree(self.core.project) as tree:
            tree.create('Writer-Workspace/Drafts/chapter.md', b'old chapter')
        context = self.core.snapshot()
        for unused in range(25):
            admin.review_snapshot(self.core.project, 'Writer-Workspace/Drafts/chapter.md', context['destination'] + '/SNAPSHOT.json', True)
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.loaded = []
        self.p = SimpleNamespace(core=self.core, window=self.window, _guarded=lambda fn:fn(),
                                 refresh_view=lambda:None, load_document=self.loaded.append)

    def test_page_file_selection_restore_and_cursor_cleanup(self):
        def interact(dialog):
            listing = dialog.findChild(QListWidget)
            files = dialog.findChild(QComboBox)
            view = dialog.findChild(QPlainTextEdit)
            buttons = {b.text(): b for b in dialog.findChildren(QPushButton)}
            self.assertEqual(listing.count(), 20)
            listing.setCurrentRow(0)
            files.setCurrentIndex(files.findData('Drafts/chapter.md'))
            self.assertEqual(view.toPlainText(), 'old chapter')
            buttons['下一页'].click()
            self.assertEqual(listing.count(), 5)
            self.assertFalse(buttons['下一页'].isEnabled())
            listing.setCurrentRow(0)
            files.setCurrentIndex(files.findData('Drafts/chapter.md'))
            buttons['恢复为新的待审草稿'].click()
            return 0
        with patch.object(QDialog, 'exec', interact), patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            snapshot_versions(self.p, 'editor')
        self.assertEqual(len(self.loaded), 1)
        self.assertEqual(self.core.document(self.loaded[0])['text'], 'old chapter')
        self.assertEqual(self.core._snapshot_pages, {})

    def test_global_history_exposes_archive_without_formal_versions(self):
        def inspect(dialog):
            titles = {b.text() for b in dialog.findChildren(QPushButton)}
            self.assertIn('全书历史审稿副本', titles)
            self.assertIn('全书历史主笔资料副本', titles)
            return 0
        with patch.object(QDialog, 'exec', inspect), patch.object(self.core, 'history', side_effect=AssertionError('eager formal history')):
            history(self.p, {})
        draft = next(r for r in self.core.documents() if r['area'] == 'drafts')
        preview = self.core.author_preview(draft['document_id'])
        self.core.author_commit(preview['handle'], True)
        def inspect_formal(dialog):
            button = next(b for b in dialog.findChildren(QPushButton) if b.text() == '查看正式作品与资料历史')
            button.click()
            self.assertEqual(dialog.findChild(QListWidget).count(), 1)
            self.assertEqual(dialog.findChild(QPlainTextEdit).toPlainText(), 'old chapter')
            return 0
        with patch.object(QDialog, 'exec', inspect_formal):
            history(self.p, {})
        with patch.object(QDialog, 'exec', return_value=0):
            snapshot_versions(self.p, 'editor')
        self.assertEqual(self.core._snapshot_pages, {})

    def test_presenter_refresh_and_draft_read_do_not_scan_archive(self):
        from novel_studio.desktop.window import Workbench
        from novel_studio.desktop.presenter import Presenter
        from ui2_fixtures import LocalOnlyCLI
        window = Workbench()
        self.addCleanup(window.deleteLater)
        p = Presenter(window, Path(self.temp.name) / 'app-state', LocalOnlyCLI())
        original = os.scandir
        def scan(path):
            self.assertNotIn('Manuscript-Snapshot', str(path))
            self.assertNotIn('Context-Snapshot', str(path))
            return original(path)
        with patch('safe_project.os.scandir', side_effect=scan):
            p.open_project(self.core.project)
            p.refresh_view()
            draft = next(r for r in p.core.documents() if r['area'] == 'drafts')
            p.load_document(draft['document_id'])
        self.assertEqual(window.editor.toPlainText(), 'old chapter')
        p.workflow_timer.stop()
