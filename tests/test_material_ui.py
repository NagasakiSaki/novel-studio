"""Real Qt event-loop and parser tests with synthetic files; no desktop control."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import importlib.util
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
AVAILABLE = all(importlib.util.find_spec(m) for m in ("PySide6", "markdown_it", "docx", "pypdf", "striprtf", "lxml"))
if AVAILABLE:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from novel_studio.core import Core
    from novel_studio.desktop.material_ui import ImportWizard
    from novel_studio.desktop.presenter import Presenter
    from novel_studio.desktop.window import Workbench
    from novel_studio.desktop.theme import apply_theme
from material_fixture_factory import preset


@unittest.skipUnless(AVAILABLE, "Audited Qt/import environment required")
class MaterialWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app, "light")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-import-ui-synthetic-")
        self.base = Path(self.temp.name)
        self.core = Core.create(self.base, "合成导入界面工程", ROOT)
        self.source = self.base / "综合预设.md"
        self.source.write_bytes(preset().encode("utf-8"))
        self.dialog = ImportWizard(self.core)

    def tearDown(self):
        if self.dialog.job:
            self.dialog.job.requestInterruption()
            self.wait_parse()
        self.dialog.reject()
        self.dialog.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_parse(self):
        deadline = time.monotonic() + 28
        while self.dialog.job is not None and time.monotonic() < deadline:
            QTest.qWait(25)
        self.assertIsNone(self.dialog.job, "Parser must finish within its bounded lifetime")
        self.app.processEvents()

    def select_files(self, files, kind_index=0):
        self.dialog.types.setCurrentRow(kind_index)
        with patch("novel_studio.desktop.material_ui.QFileDialog.getOpenFileNames", return_value=([str(p) for p in files], "")) as chooser:
            QTest.mouseClick(self.dialog.next_button, Qt.LeftButton)
        self.wait_parse()
        return chooser

    def test_first_type_then_file_and_cancel_has_no_record(self):
        self.dialog.show()
        self.app.processEvents()
        self.assertEqual(self.dialog.stack.currentIndex(), 0)
        self.assertEqual(self.dialog.types.count(), 12)
        self.assertEqual(self.dialog.types.currentRow(), -1)
        self.assertFalse(self.dialog.next_button.isEnabled())
        with patch("novel_studio.desktop.material_ui.QFileDialog.getOpenFileNames", return_value=([], "")) as chooser:
            self.dialog.next()
            chooser.assert_not_called()
            self.dialog.types.setCurrentRow(0)
            QTest.mouseClick(self.dialog.next_button, Qt.LeftButton)
            chooser.assert_called_once()
        self.assertEqual(self.dialog.stack.currentIndex(), 0)
        self.assertEqual(self.dialog.rows, [])
        self.assertEqual(self.core.materials.records(), [])
        self.assertEqual(self.core.history(), [])

    def test_back_changes_type_without_picking_or_reparsing(self):
        self.select_files([self.source])
        prepared = self.dialog.rows[0]["prepared"]
        self.assertEqual(prepared.parsed["text"], preset())
        self.dialog.back()
        self.dialog.types.setCurrentRow(1)
        with patch("novel_studio.desktop.material_ui.QFileDialog.getOpenFileNames") as chooser:
            self.dialog.next()
            chooser.assert_not_called()
        self.assertIs(self.dialog.rows[0]["prepared"], prepared)
        self.assertEqual(prepared.material_type, "characters")
        self.assertFalse(self.dialog.rows[0]["scope"])
        self.assertIn("人物设定", self.dialog.details.text())

    def test_batch_failure_and_individual_retry_keep_success(self):
        broken = self.base / "损坏文本.txt"
        broken.write_bytes(b"\xffbroken")
        self.select_files([self.source, broken], 1)
        self.assertIsNotNone(self.dialog.rows[0]["prepared"])
        self.assertIsNotNone(self.dialog.rows[1]["error"])
        self.dialog.approve_preview = lambda preview: True  # Explicit synthetic author authorization.
        self.dialog.commit_selected()
        self.assertTrue(self.dialog.rows[0]["done"])
        self.assertEqual(len(self.core.materials.records()), 1)
        broken.write_bytes("林舟与周岚的补充资料。".encode())
        self.dialog.table.selectRow(1)
        self.dialog.retry()
        self.wait_parse()
        self.assertIsNone(self.dialog.rows[1]["error"])
        self.dialog.commit_selected()
        self.assertEqual(len(self.core.materials.records()), 2)
        self.assertTrue(all(r["done"] for r in self.dialog.rows))

    def test_reparse_resets_old_rule_ids_and_author_ack(self):
        self.select_files([self.source])
        node = self.dialog.toc.topLevelItem(0).child(0)
        node.setCheckState(0, Qt.Checked)
        self.dialog.scope_ack.setChecked(True)
        self.dialog.save_scope()
        self.assertTrue(self.dialog.rows[0]["always"])
        self.assertTrue(self.dialog.rows[0]["scope"])
        self.source.write_bytes(("# 已重排\n## 背景资料\n不应继承旧全局规则标记。\n## 全局规则\n新规则须重新确认。").encode())
        self.dialog.approve_preview = lambda preview: True
        self.dialog.commit_selected()
        self.assertIsNotNone(self.dialog.rows[0]["error"])
        self.dialog.retry()
        self.wait_parse()
        self.assertEqual(self.dialog.rows[0]["always"], [])
        self.assertFalse(self.dialog.rows[0]["scope"])
        self.assertFalse(self.dialog.scope_ack.isChecked())
        self.assertEqual(self.core.materials.records(), [])

    def test_rule_confirmation_and_duplicate_default_skip(self):
        self.select_files([self.source])
        self.dialog.approve_preview = lambda preview: True
        self.dialog.commit_selected()
        self.assertEqual(self.core.materials.records(), [])
        self.assertEqual(self.dialog.rows[0]["error"].code, "SCOPE_CONFIRMATION_REQUIRED")
        self.dialog.scope_ack.setChecked(True)
        self.dialog.commit_selected()
        self.assertEqual(len(self.core.materials.records()), 1)
        other = ImportWizard(self.core)
        try:
            other.types.setCurrentRow(0)
            with patch("novel_studio.desktop.material_ui.QFileDialog.getOpenFileNames", return_value=([str(self.source)], "")):
                other.next()
            deadline = time.monotonic() + 20
            while other.job and time.monotonic() < deadline:
                QTest.qWait(25)
            self.assertIsNone(other.job)
            self.assertEqual(other.table.item(0, 0).checkState(), Qt.Unchecked)
            self.assertIn("重复", other.warning.text())
        finally:
            if other.job:
                other.job.wait(26000)
                self.app.processEvents()
            other.reject()
            other.deleteLater()

    def test_close_during_parse_waits_and_writes_nothing(self):
        original = self.dialog.service.prepare
        def delayed(*args, **kwargs):
            time.sleep(0.25)
            return original(*args, **kwargs)
        self.dialog.show()
        self.dialog.types.setCurrentRow(0)
        with patch.object(self.dialog.service, "prepare", side_effect=delayed):
            with patch("novel_studio.desktop.material_ui.QFileDialog.getOpenFileNames", return_value=([str(self.source)], "")):
                self.dialog.next()
            self.assertIsNotNone(self.dialog.job)
            self.dialog.close()
            self.assertTrue(self.dialog.cancel_pending)
            self.wait_parse()
        self.assertFalse(self.dialog.isVisible())
        self.assertEqual(self.core.materials.records(), [])
        self.assertEqual(self.core.history(), [])

    def test_author_restore_refreshes_writer_snapshot(self):
        def approve():
            prepared = self.core.materials.prepare(self.source, "global_preset", record_id[0])
            plan = self.core.materials.preview(prepared, [], "retrieval", True)
            record_id[0] = plan["material_id"]
            return self.core.author_commit(plan["handle"], True)
        record_id = [None]
        approve()
        self.core.snapshot()
        self.source.write_bytes(preset().encode() + "\n## 新版\n新增设定".encode())
        receipt = approve()
        self.core.snapshot()
        self.assertEqual(self.core.materials.context("writer", "林舟")[1][0]["version"], 2)
        window = Workbench()
        try:
            presenter = Presenter(window, self.base / "synthetic-state")
            presenter.open_project(self.core.project)
            preview = presenter.core.author_preview(transaction=receipt["transaction_id"])
            from PySide6.QtWidgets import QDialog
            with patch("novel_studio.desktop.presenter.DiffDialog.exec", return_value=QDialog.Accepted):
                presenter.author_dialog(preview)
            self.assertEqual(presenter.core.materials.context("writer", "林舟")[1][0]["version"], 1)
            self.assertIn("主笔副本已更新", window.status_text.text())
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
