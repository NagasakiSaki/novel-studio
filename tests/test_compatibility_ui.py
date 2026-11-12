"""Qt offscreen author diagnostics and explicit cache repair, no model calls."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from PySide6.QtWidgets import QApplication, QMessageBox
from novel_studio.desktop.window import Workbench
from novel_studio.desktop.presenter import Presenter
from novel_studio.desktop.compatibility_ui import CompatibilityDialog
from novel_studio.compatibility import CompatibilityManager
from test_long_term_compatibility import FakeLocalCLI


class CompatibilityUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='novel-compat-ui-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.window = Workbench()
        self.addCleanup(self.window.deleteLater)
        exe = self.root / 'agy.exe'
        exe.write_bytes(b'SYNTHETIC_NOT_EXECUTED')
        self.cli = FakeLocalCLI(exe)
        self.cli._compatibility = CompatibilityManager(self.cli, self.root / 'receipts')
        self.presenter = Presenter(self.window, self.root / 'state', self.cli)

    def wait_job(self):
        until = time.monotonic() + 8
        while self.presenter.job is not None and time.monotonic() < until:
            self.app.processEvents()
            time.sleep(.01)
        self.assertIsNone(self.presenter.job)

    def test_auto_inspection_shows_separate_versions_reasons_and_no_paid_probe(self):
        dialog = CompatibilityDialog(self.presenter)
        self.addCleanup(dialog.deleteLater)
        self.wait_job()
        text = dialog.details.toPlainText()
        from novel_studio.core import VERSION
        self.assertIn('软件版本：' + VERSION, text)
        self.assertIn('1.5.3', text)
        self.assertIn('1.0.0', text)
        self.assertIn('尚无本连接程序', text)
        self.assertIn('本地阅读', dialog.heading.text())
        self.assertEqual(self.cli.probes, 0)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            dialog.probe()
        self.assertEqual(self.cli.probes, 0)

    def test_corrupt_cache_can_be_archived_without_granting_model_permission(self):
        dialog = CompatibilityDialog(self.presenter)
        self.addCleanup(dialog.deleteLater)
        self.wait_job()
        path = self.root / 'receipts/receipts.bin'
        original = b'CORRUPT_SYNTHETIC_CACHE'
        path.write_bytes(original)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            dialog.repair()
        self.wait_job()
        self.assertEqual(next((self.root / 'receipts/Archived').glob('*.bin')).read_bytes(), original)
        self.assertFalse(self.cli.compatibility.inspect()['model_allowed'])
        self.assertEqual(self.cli.probes, 0)


if __name__ == '__main__':
    unittest.main()
