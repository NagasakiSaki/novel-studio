"""Synthetic collisions and GUI export must preserve prior files without prompts."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests')]
from novel_studio.core import CoreError, SafeTree
from novel_studio.diagnostics import DiagnosticLog


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='novel-export-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = DiagnosticLog(self.root/'state')
        self.target = self.root/'Novel-Studio-诊断日志.json'

    def test_existing_export_and_multiple_same_second_saves_are_preserved(self):
        self.target.write_bytes(b'SYNTHETIC-OLD-EXPORT')
        with patch('novel_studio.diagnostics.time.strftime', return_value='20261107-160000'):
            first = self.log.export(self.target)
            second = self.log.export(self.target)
        self.assertEqual(self.target.read_bytes(), b'SYNTHETIC-OLD-EXPORT')
        self.assertEqual(first.name, 'Novel-Studio-诊断日志-20261107-160000.json')
        self.assertEqual(second.name, 'Novel-Studio-诊断日志-20261107-160000-2.json')
        self.assertIn(b'novel-studio-diagnostic-export-v1', first.read_bytes())

    def test_file_created_after_selection_is_not_overwritten(self):
        create = SafeTree.create
        def competing(tree, name, payload):
            if tree.root == self.root and name == self.target.name:
                self.target.write_bytes(b'SYNTHETIC-CONCURRENT-FILE')
            return create(tree, name, payload)
        with patch.object(SafeTree, 'create', new=competing):
            saved = self.log.export(self.target)
        self.assertNotEqual(saved, self.target)
        self.assertEqual(self.target.read_bytes(), b'SYNTHETIC-CONCURRENT-FILE')

    def test_permission_error_is_clear_and_collision_budget_is_bounded(self):
        with patch.object(SafeTree, 'create', side_effect=PermissionError(13, 'PRIVATE-CANARY')):
            with self.assertRaises(CoreError) as caught:
                self.log.export(self.target)
        self.assertEqual(caught.exception.code, 'DIAGNOSTIC_EXPORT_FAILED')
        self.assertEqual(caught.exception.errno, 13)
        self.assertNotIn('PRIVATE-CANARY', str(caught.exception))
        with patch.object(SafeTree, 'create', side_effect=FileExistsError()) as create:
            with self.assertRaises(CoreError) as caught:
                self.log.export(self.target)
        self.assertEqual(caught.exception.code, 'DIAGNOSTIC_EXPORT_CONFLICT')
        self.assertEqual(create.call_count, 32)

    def test_gui_export_collision_and_cancel_do_not_raise_another_error(self):
        from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QFileDialog
        from PySide6.QtCore import QTimer
        from novel_studio.desktop.window import Workbench
        from novel_studio.desktop.presenter import Presenter
        from novel_studio.desktop.diagnostic_ui import show_logs
        from ui2_fixtures import LocalOnlyCLI
        app = QApplication.instance() or QApplication([])
        window = Workbench()
        presenter = Presenter(window, self.root/'state', LocalOnlyCLI(), diagnostic_log=self.log)
        self.target.write_bytes(b'SYNTHETIC-OLD-EXPORT')
        observed = []
        def operate():
            try:
                dialog = next(d for d in app.topLevelWidgets() if isinstance(d, QDialog) and d.windowTitle().startswith('诊断日志'))
                button = next(b for b in dialog.findChildren(QPushButton) if b.text() == '导出诊断日志')
                button.click()
                button.click()  # second selection is Cancel
                observed.append(window.status_text.text())
                dialog.accept()
            except BaseException as error:
                observed.append(error)
                for d in app.topLevelWidgets():
                    if isinstance(d, QDialog):
                        d.reject()
        try:
            with patch('novel_studio.desktop.diagnostic_ui.QFileDialog.getSaveFileName', side_effect=[(str(self.target),'JSON'), ('','')]) as picker, patch.object(presenter, 'show_error') as error:
                QTimer.singleShot(30, operate)
                show_logs(presenter)
                error.assert_not_called()
            self.assertEqual(len(observed), 1)
            self.assertIsInstance(observed[0], str)
            files = list(self.root.glob('Novel-Studio-诊断日志*.json'))
            self.assertEqual(len(files), 2)
            saved = next(f for f in files if f != self.target)
            self.assertIn(str(saved), observed[0])
            self.assertEqual(self.target.read_bytes(), b'SYNTHETIC-OLD-EXPORT')
            self.assertEqual(picker.call_args.kwargs['options'], QFileDialog.Option.DontConfirmOverwrite)
        finally:
            window.close()
            window.deleteLater()
            app.processEvents()


if __name__ == '__main__':
    unittest.main()
