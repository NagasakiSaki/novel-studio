"""1.3 Qt integration, synthetic transport; separate from native GUI evidence."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from PySide6.QtWidgets import QApplication, QTreeWidget, QMessageBox
from novel_studio.desktop.window import Workbench
from novel_studio.desktop.presenter import Presenter
from novel_studio.desktop.lifecycle import dispose_windows
from ui2_fixtures import project, LocalOnlyCLI, complete


class Product13UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-product13-qt-")
        self.base = Path(self.temp.name)
        self.w = Workbench()
        self.p = Presenter(self.w, self.base / "state", LocalOnlyCLI())
        self.core, self.script = project(self.base, presenter=self.p)

    def tearDown(self):
        self.p.workflow_auto = False
        self.p.workflow_timer.stop()
        if self.p.job:
            self.p.job.wait(30000)
            self.app.processEvents()
        dispose_windows(self.app)
        self.temp.cleanup()

    def test_actual_tree_and_chapter_state_actions_plan_and_body_search(self):
        complete(self.core)
        self.p.refresh_view()
        self.w.navigate("chapters")
        panel = self.w.workflow_panel
        self.assertIsInstance(panel.table, QTreeWidget)
        root = panel.table.topLevelItem(0)
        self.assertEqual(root.childCount(), 1)
        self.assertEqual(root.child(0).childCount(), 2)
        panel.table.selectRow(0)
        self.assertIn("剧情目标", panel.chapter_views["plan"].toPlainText())
        self.assertIn("林舟", panel.chapter_views["text"].toPlainText())
        self.assertTrue(panel.chapter_action_buttons["workflow_document"].isEnabled())
        self.assertTrue(panel.chapter_action_buttons["workflow_chapter_review"].isEnabled())
        panel.chapter_search.setText("蓝信")
        self.assertFalse(panel.table.isRowHidden(0))
        panel.chapter_search.setText("没有此内容的合成搜索")
        self.assertTrue(all(panel.table.isRowHidden(i) for i in range(2)))
        self.assertIn("选择", panel.chapter_views["text"].toPlainText())

    def test_chapter_role_entry_has_explicit_scope_and_separate_composers(self):
        panel = self.w.workflow_panel
        cid = panel.rows[0]["chapter_id"]
        self.p.action("workflow_writer_chat", {"chapter_id": cid})
        self.w.show()
        self.app.processEvents()
        self.assertEqual(self.w.navigation_page, "chat")
        self.assertTrue(self.w.chat_stack.isVisible())
        self.assertTrue(self.w.chat_inputs["writer"].isVisible())
        self.assertTrue(self.w.send_buttons["writer"].isVisible())
        self.assertEqual(self.w.chat_scope.currentData(), "chapter")
        self.assertIn("第1章", self.w.chat_scope_hint.text())
        self.w.chat_inputs["writer"].setPlainText("主笔未发送内容")
        self.w.switch_role("editor")
        self.assertEqual(self.w.chat_inputs["writer"].toPlainText(), "主笔未发送内容")
        self.assertEqual(self.w.chat_operation.findData("revise"), -1)
        self.assertGreaterEqual(self.w.chat_operation.findData("plan"), 0)

    def test_import_buttons_remain_inside_current_window_and_type_notice_updates(self):
        from novel_studio.desktop.material_ui import ImportWizard
        self.w.resize(1000, 680)
        dialog = ImportWizard(self.core, self.w)
        self.assertLessEqual(dialog.height(), 620)
        dialog.types.setCurrentRow(2)
        self.assertIn("世界观", dialog.notice.text())
        self.assertTrue(dialog.next_button.isEnabled())
        dialog.deleteLater()

    def test_author_send_executes_current_chapter_revision_and_displays_feedback(self):
        complete(self.core)
        self.p.refresh_view()
        panel = self.w.workflow_panel
        cid = panel.rows[0]["chapter_id"]
        old = self.core.workflow.store.read()["chapters"][cid]["draft"]
        self.p.action("workflow_writer_chat", {"chapter_id": cid})
        self.w.chat_operation.setCurrentIndex(self.w.chat_operation.findData("revise"))
        self.w.chat_inputs["writer"].setPlainText("保留原情节，修改结尾动作。")
        errors = []
        with patch.object(QMessageBox, "warning", side_effect=lambda *a: errors.append(str(a))):
            self.w.send_buttons["writer"].click()
            deadline = time.monotonic() + 40
            while self.p.job and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
        self.assertFalse(errors)
        self.assertIsNone(self.p.job)
        row = self.core.workflow.store.read()["chapters"][cid]
        self.assertNotEqual(row["draft"], old)
        self.assertEqual(self.core.models.transcript_page("writer")["entries"][0]["chapter_id"], cid)
        self.assertIn("修订", self.w.status_text.text())

    def test_outline_formal_version_hierarchy_active_usage_and_old_plan_marker(self):
        complete(self.core)
        self.p.refresh_view()
        panel = self.w.workflow_panel
        self.assertGreater(panel.outline_tree.topLevelItemCount(), 0)
        self.assertIn("当前正式", panel.versions.currentText())
        self.assertIn("本次任务使用第", panel.outline_status.text())
        previous = self.core.workflow.planning.approved()
        candidate = self.core.workflow.planning.save(previous["value"])
        preview = self.core.workflow.planning.preview(candidate["version_id"])
        self.core.author_commit(preview["handle"], True)
        self.p.refresh_view()
        self.assertIn("需按新大纲重新规划", panel.outline_status.text())

    def test_discussion_history_shows_original_words_and_its_own_chapter_scope(self):
        from novel_studio.desktop.ui_state import transcript_text
        cid = self.core.workflow.chapters.catalog()[0]["chapter_id"]
        sent = []
        def conversation(role, prompt, notify=None):
            sent.append(prompt)
            self.core.models.append_history(role, {"user": prompt, "assistant": "合成讨论答复"})
            return {"message": "合成讨论答复", "artifact": None}
        with patch.object(self.core.models, "run", side_effect=conversation):
            self.core.workflow.author.send("editor", "检查林舟的知情边界，先讨论。", operation="discuss", chapter_id=cid)
        self.p.refresh_view()
        visible = self.w.chat_outputs["editor"].toPlainText()
        self.assertIn("第1卷第1章", visible)
        self.assertIn("检查林舟的知情边界，先讨论。", visible)
        self.assertNotIn("【作者指令 ", visible)
        self.assertNotIn("current_manuscript", visible)
        self.assertTrue(sent[0].startswith("【作者指令 "))
        legacy = transcript_text({"user": "旧对话", "assistant": "旧答复"}, "writer", self.core.workflow.store.read())
        self.assertIn("当时范围未记录", legacy)


if __name__ == "__main__":
    unittest.main()
