"""Headless Qt contract tests, not a substitute for native Windows/IME UAT."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase
    from novel_studio.desktop.window import Workbench
    from novel_studio.desktop.dialogs import DiffDialog
    from novel_studio.desktop.presenter import Presenter
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Desktop Qt environment not installed")
class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = Workbench()
        self.addCleanup(self.window.deleteLater)

    def test_role_text_isolation_and_readonly(self):
        w = self.window
        w.chat_inputs["writer"].setPlainText("主笔未发送的想法")
        w.chat_inputs["editor"].setPlainText("责编独立问题")
        w.set_document("draft", "合成中文章节", "正文……——：“铜钥匙”", "drafts", "r1")
        self.assertFalse(w.editor.isReadOnly())
        w.switch_role("editor")
        self.assertTrue(w.editor.isReadOnly())
        self.assertEqual(w.chat_inputs["writer"].toPlainText(), "主笔未发送的想法")
        w.switch_role("writer")
        w.set_document("canon", "正式作品", "不应直接修改", "canonical", "r2")
        self.assertTrue(w.editor.isReadOnly())
        self.assertFalse(w.save_button.isEnabled())

    def test_full_diff_explicit_approval(self):
        text = "".join(f"+第{i}行：中文差异\n" for i in range(2000))
        dialog = DiffDialog({"target": "Canonical/Final-Manuscript/第一章.md", "diff": text})
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.approve.isEnabled())
        self.assertEqual(dialog.diff_view.toPlainText().splitlines(), text.splitlines())
        dialog.confirm.setChecked(True)
        self.assertTrue(dialog.approve.isEnabled())

    def test_layout_dark_theme_and_long_chinese(self):
        w = self.window
        w.toggle_theme()
        text = "第一章：中文长文……——\n" * 15000
        w.set_document("long", "长文测试", text)
        self.assertEqual(w.editor.toPlainText(), text)
        state = w.capture_layout()
        restored = Workbench()
        self.addCleanup(restored.deleteLater)
        restored.restore_layout(state)
        self.assertEqual(restored.theme, "dark")
        if sys.platform == "win32":
            self.assertTrue(QFontDatabase.hasFamily("Microsoft YaHei"))

    def test_same_core_drives_ui_synthetic_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            presenter = Presenter(self.window, root / "state")
            from novel_studio.core import Core
            core = Core.create(root, "界面合成工程", ROOT)
            presenter.open_project(core.project)
            presenter.action("new_draft", {})
            w = self.window
            w.editor.setPlainText("# 第一章\n林舟将钥匙放在钟楼下面。")
            presenter.action("save_draft", {"document_id": w.document_id, "revision": w.document_revision, "text": w.editor.toPlainText()})
            draft_id = w.document_id
            presenter.action("handoff", {"document_id": draft_id})
            self.assertEqual(w.role, "editor")
            self.assertTrue(w.editor.isReadOnly())
            self.assertFalse(w.new_draft_button.isEnabled())
            report = presenter.call("create_review_report", {"text": "证据：铜钥匙尚未回收。作者决定是否保留。"})
            presenter.load_document(report["document_id"])
            self.assertIn("铜钥匙", w.review_output.toPlainText())
            presenter.action("feedback", {"text": "保留铜钥匙伏笔"})
            self.assertEqual(w.role, "writer")
            self.assertEqual(len(presenter.core.history()), 0)
            self.assertFalse(presenter.core._active)

    def test_busy_editor_and_unsaved_completion_guard(self):
        from PySide6.QtTest import QTest
        from novel_studio.core import Core
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            p = Presenter(self.window, root / "state")
            c = Core.create(root, "输入保护合成工程", ROOT)
            p.open_project(c.project)
            p.action("new_draft", {})
            w = self.window
            original = w.editor.toPlainText()
            gate = threading.Event()
            p.start_job(lambda notify: (gate.wait(3), {"artifact": {"document_id": "must-not-open"}})[1],
                        lambda result: p.run_finished("writer", result), "模拟等待")
            self.assertTrue(w.editor.isReadOnly())
            QTest.keyClicks(w.editor, "ignored keyboard text")
            self.assertEqual(w.editor.toPlainText(), original)
            w.editor.insertPlainText("程序化未保存输入")  # Regression guard even if another signal changes text.
            w.editor.document().setModified(True)
            unsaved = w.editor.toPlainText()
            gate.set()
            deadline = time.monotonic() + 5
            while p.job is not None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertIsNone(p.job)
            self.assertEqual(w.editor.toPlainText(), unsaved)
            self.assertTrue(w.editor.document().isModified())
            self.assertFalse(w.editor.isReadOnly())
            w.editor.document().setModified(False)

    def test_failed_request_does_not_replace_new_composition(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Presenter(self.window, Path(directory) / "state")
            p.pending_prompt = ("writer", "上次请求")
            self.window.chat_inputs["writer"].setPlainText("新写的想法")
            with patch("novel_studio.desktop.presenter.QMessageBox.warning"):
                p.show_error(ValueError("synthetic"))
            self.assertIn("新写的想法", self.window.chat_inputs["writer"].toPlainText())
            self.assertIn("上次请求", self.window.chat_inputs["writer"].toPlainText())


if __name__ == "__main__":
    unittest.main()
