"""Qt settings and compiler integration with synthetic manuscript data only."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from novel_studio.core import Core, READ, WRITE
    from novel_studio.desktop.creative_ui import WriterSettingsDialog, EditorSettingsDialog
    from novel_studio.desktop.presenter import Presenter
    from novel_studio.desktop.window import Workbench
    from novel_studio.prompting import assemble
    from novel_studio.creative import template
    from novel_studio.lengths import DEFAULT_GOAL
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt developer environment not installed")
class CreativeUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.core = Core.create(self.base, "风格界面合成工程", ROOT)
        self.core.snapshot()
        cap = self.core.bind("writer", READ | WRITE)
        self.draft = self.core.dispatch(cap, "create_draft", {"text": "# 标题\n林舟握住钥匙。"}, "synthetic-creative")

    def test_style_save_apply_compare_and_unsaved_guard(self):
        dialog = WriterSettingsDialog(self.core, self.draft["document_id"])
        panel = dialog.styles
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(len(panel.fields), 16)
        panel.title.setText("作者的合成风格")
        panel.fields["pace"].setText("允许安静场景，不强制钩子")
        panel.save_profile()
        panel.bind_profile()
        self.assertEqual(self.core.creative.context("writer")[0]["version"], 1)
        panel.fields["distance"].setText("本书新的距离约定")
        panel.save_profile()
        self.assertEqual(self.core.creative.context("writer")[0]["version"], 1)
        panel.bind_profile()
        self.assertEqual(self.core.creative.context("writer")[0]["version"], 2)
        panel.fields["custom"].setText("未保存文字")
        with patch("novel_studio.desktop.creative_ui.QMessageBox.question", return_value=QMessageBox.No):
            self.assertFalse(panel.may_leave())
        self.assertIn("未保存", panel.fields["custom"].text())
        self.assertEqual(self.core.creative.context("editor"), ({}, {}))
        panel.populate(panel.loaded["value"], panel.loaded)

    def test_theme_changes_after_creative_dialog_is_created(self):
        from novel_studio.desktop.theme import apply_theme
        dialog = WriterSettingsDialog(self.core)
        self.addCleanup(dialog.deleteLater)
        apply_theme(self.app, "dark")
        apply_theme(self.app, "light")
        self.assertEqual(dialog.lengths.metric_selector.count(), 3)

    def test_editor_criteria_weights_and_options_are_independent(self):
        dialog = EditorSettingsDialog(self.core)
        self.addCleanup(dialog.deleteLater)
        panel = dialog.aesthetics
        self.assertEqual(len(panel.fields), 15)
        self.assertEqual(len(panel.weights), 15)
        panel.title.setText("合成人物审美")
        panel.weights["character"].setValue(9)
        panel.options["tolerance"].setPlainText("允许慢热和不同审美选择")
        panel.save_profile()
        panel.bind_profile()
        self.assertIn("责编", panel.status.text())
        self.assertEqual(self.core.creative.context("editor")[0]["weights"]["character"], 9)
        self.assertEqual(self.core.creative.context("writer"), ({}, {}))

    def test_goals_local_ui_counts_and_no_source_changes(self):
        chapter = self.core.lengths.add("chapter", "第一章")
        self.core.lengths.link(chapter, self.draft["document_id"])
        self.core.lengths.goal(chapter, {**DEFAULT_GOAL, "target": 10})
        self.core.lengths.activate(chapter)
        window = Workbench()
        self.addCleanup(window.deleteLater)
        presenter = Presenter(window, self.base / "state")
        presenter.open_project(self.core.project)
        presenter.load_document(self.draft["document_id"])
        window.measure_current()
        self.assertTrue(window.count_label.text().startswith("7"))
        self.assertIn("不足", window.word_goal_label.text())
        self.assertEqual(window.word_progress.value(), 700)
        window.editor.insertPlainText("新的未保存文字。")
        window.measure_current()
        self.assertIn("超过", window.word_goal_label.text())
        self.assertEqual(self.core.document(self.draft["document_id"])["text"], "# 标题\n林舟握住钥匙。")
        window.editor.document().setModified(False)

    def test_profile_stable_override_dynamic_and_book_goal_not_single_draft(self):
        profile = self.core.creative.save(template("人物驱动型"))
        self.core.creative.bind(profile["profile_id"])
        first, _ = assemble(self.core, "writer", "当前明确要求", source_document_id=self.draft["document_id"])
        self.core.creative.override(self.draft["document_id"], {"pace": "本章加快"})
        second, _ = assemble(self.core, "writer", "当前明确要求", source_document_id=self.draft["document_id"])
        self.assertEqual(first.stable_text, second.stable_text)
        self.assertIn("本章加快", second.dynamic_text)
        self.assertTrue(second.dynamic_text.endswith("当前明确要求"))
        self.core.lengths.goal("book", {**DEFAULT_GOAL, "target": 100000})
        self.assertIsNone(self.core.lengths.check_artifact("只有一段。", self.draft["document_id"])["target"])
        self.assertEqual(self.core.document(self.draft["document_id"])["text"], "# 标题\n林舟握住钥匙。")

    def test_unbound_new_draft_does_not_show_old_version_progress(self):
        chapter = self.core.lengths.add("chapter", "第一章")
        self.core.lengths.link(chapter, self.draft["document_id"])
        self.core.lengths.goal(chapter, {**DEFAULT_GOAL, "target": 100})
        self.core.lengths.activate(chapter)
        text = "无 Markdown 标题的新草稿，正文首句不应被当作长标题。" * 6
        fresh = self.core.dispatch(self.core.bind("writer", READ | WRITE), "create_draft", {"text": text}, "unbound-title-synthetic")
        window = Workbench()
        self.addCleanup(window.deleteLater)
        presenter = Presenter(window, self.base / "unbound-state")
        presenter.open_project(self.core.project)
        presenter.load_document(fresh["document_id"])
        window.measure_current()
        self.assertIn("未关联", window.word_goal_label.text())
        self.assertEqual(window.word_progress.value(), 0)
        self.assertNotIn("正文首句", window.chapter_title.text())
        self.assertEqual(self.core.document(fresh["document_id"])["text"], text)
        self.assertEqual(self.core.lengths.plan()["nodes"][chapter]["source"]["document_id"], self.draft["document_id"])


if __name__ == "__main__":
    unittest.main()
