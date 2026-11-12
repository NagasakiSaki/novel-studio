"""State guidance and real Qt author interactions on new synthetic projects."""
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
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QTabWidget
from PySide6.QtCore import Qt
from novel_studio.core import CoreError, SafeTree, admin, digest
from novel_studio.desktop.window import Workbench
from novel_studio.desktop.presenter import Presenter
from novel_studio.desktop.lifecycle import dispose_windows
from novel_studio.desktop.ui_state import outline_scope, next_step
from ui2_fixtures import LocalOnlyCLI, project, complete


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-ui2-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.w = Workbench()
        self.p = Presenter(self.w, self.base / "isolated-ui-state", LocalOnlyCLI())

    def tearDown(self):
        if self.p.job:
            self.p.job.wait(30000)
        self.p.workflow_auto = False
        self.p.workflow_timer.stop()
        dispose_windows(self.app)

    def test_home_guides_from_manual_writing_to_approved_outline_and_pending_chapters(self):
        panel = self.w.workflow_panel
        self.assertEqual(panel.start_button.text(), "新建作品")
        core, script = project(self.base, presenter=self.p, approve=False)
        self.assertEqual(panel.start_button.text(), "查看并确认大纲")
        panel.start_button.click()
        self.assertEqual(self.w.navigation_page, "outline")
        state = core.workflow.store.read()
        no_outline = {**state, "outlines": {}, "outline_order": []}
        self.assertEqual(next_step(no_outline)["data"], {"page": "files", "role": "writer"})
        preview = core.workflow.planning.preview(state["outline_order"][-1])
        core.author_commit(preview["handle"], True)
        core.workflow.chapters.ensure()
        self.p.refresh_view()
        self.assertTrue(panel.start_button.text().startswith('一键开始创作'))
        self.assertIn('全书',panel.home_task_scope.text())
        complete(core)
        self.p.refresh_view()
        self.assertIn("待确认", panel.start_button.text())
        panel.start_button.click()
        self.assertEqual(self.w.navigation_page, "chapters")
        self.assertEqual(panel.chapter_filter.currentData(), "pending")
        self.assertEqual(panel.book_progress.value(), 0)
        self.assertTrue(all(r["canonical"] is None for r in core.workflow.chapters.catalog()))

    def test_packaged_first_draft_needs_no_host_and_reopens_without_component(self):
        bundle = self.base / "synthetic-bundle"
        component = bundle / "payload" / "novel-studio"
        component.mkdir(parents=True)
        manifest = component / "plugin.json"
        manifest.write_text('{"name":"novel-studio"}', encoding="utf-8")
        (bundle / "BUNDLE.json").write_text("{}", encoding="utf-8")
        panel = self.w.workflow_panel
        project_title = "合成新手作品"

        def fill_form(dialog, location=self.base):
            dialog.title.setText("../越界")
            with patch("novel_studio.desktop.project_ui.QFileDialog.getExistingDirectory", return_value=str(location)):
                dialog.choose_folder()
            self.assertFalse(dialog.create_button.isEnabled())
            dialog.title.setText(project_title)
            self.assertTrue(dialog.create_button.isEnabled())
            self.assertIn(str(location / project_title), dialog.destination.text())
            dialog.create_button.click()
            return dialog.result()

        errors = []
        with patch("novel_studio.desktop.presenter.ROOT", bundle), \
                patch.object(self.p.cli, "installed_plugin", side_effect=AssertionError("Unexpected host query")), \
                patch("novel_studio.desktop.project_ui.NewProjectDialog.exec", new=fill_form), \
                patch.object(self.p, "show_error", side_effect=errors.append):
            with patch("novel_studio.desktop.project_ui.NewProjectDialog.exec", new=lambda dialog: fill_form(dialog, bundle)):
                panel.start_button.click()
            self.assertEqual([error.code for error in errors], ["PROJECT_LOCATION"])
            self.assertFalse((bundle / project_title).exists())
            self.assertIsNone(self.p.core)
            errors.clear()
            panel.start_button.click()
            self.assertEqual(errors, [])
            core = self.p.core
            self.assertEqual(core.project, self.base / project_title)
            self.assertEqual(Path(core.metadata["plugin"]), component)
            self.assertTrue(core.validate_permissions()["legacy_profiles_match"])
            self.assertEqual(panel.start_button.text(), "写草稿")
            self.assertFalse(panel.home_ai.isHidden())
            self.assertTrue(panel.home_task_scope.isHidden())
            self.w.nav_dock.buttons["outline"].click()
            self.assertEqual(self.w.navigation_page, "outline")
            self.w.navigate("home")
            panel.start_button.click()
            self.assertIs(self.w.tabs.currentWidget(), self.w.file_explorer)
            self.w.new_draft_button.click()
            document = self.w.document_id
            text = "# 合成第一章\n\n她在灯下拆开信。"
            self.w.editor.setPlainText(text)
            self.w.editor.document().setModified(True)
            self.w.save_button.click()
            self.assertEqual(errors, [])
            self.assertEqual(core.document(document)["text"], text)
            self.assertFalse(self.w.editor.document().isModified())
            self.assertIn("草稿 1 份", panel.book_summary.text())

        manifest.unlink()
        self.p.open_project(core.project)
        self.assertEqual(self.p.core.document(document)["text"], text)
        from novel_studio.desktop.dialogs import InformationDialog
        reports = []
        with patch.object(InformationDialog, "exec", new=lambda dialog: reports.append(dialog.text.toPlainText())):
            self.p.diagnostics()
        self.assertEqual(len(reports), 1)
        self.assertIn("作品绑定的组件不可用", reports[0])
        self.assertNotIn(str(component), reports[0])
        with self.assertRaises((OSError, ValueError)):
            self.p.core.validate_permissions()
        profile = ".novel-studio/Sessions/writer.json"
        with SafeTree(core.project) as tree:
            before = tree.read(profile)
            tree.publish(profile, admin.encode({"modified": True}), digest(before))
        with self.assertRaises(CoreError) as failure:
            self.p.open_project(core.project)
        self.assertEqual(failure.exception.code, "PROFILE_CHANGED")

    def test_sample_shows_read_only_draft_and_review_without_opening_project(self):
        examples = []

        def inspect_example(dialog):
            examples.append(dialog)
            return 0

        with patch("novel_studio.desktop.project_ui.WritingExampleDialog.exec", new=inspect_example):
            self.w.workflow_panel.findChild(QPushButton, "sampleButton").click()
        self.assertEqual(len(examples), 1)
        tabs = examples[0].findChild(QTabWidget)
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())], ["草稿", "责编意见"])
        self.assertTrue(all(tabs.widget(i).isReadOnly() for i in range(tabs.count())))
        self.assertIn("铜钥匙", tabs.widget(0).toPlainText())
        self.assertIn("作者裁决", tabs.widget(1).toPlainText())
        self.assertIsNone(self.p.core)

    def test_chapter_content_switch_is_in_place_and_keeps_formal_bytes_unchanged(self):
        core, script = project(self.base, presenter=self.p)
        complete(core)
        self.p.refresh_view()
        self.w.navigate("chapters")
        panel = self.w.workflow_panel
        panel.table.selectRow(0)
        state = core.workflow.store.read()
        calls = len(script.calls)
        for index, key in enumerate(("text", "review", "direction", "history")):
            panel.chapter_tabs.setCurrentIndex(index)
            self.assertTrue(panel.chapter_views[key].toPlainText())
            self.assertIs(self.w.tabs.currentWidget(), panel)
        self.assertEqual(len(script.calls), calls)
        self.assertEqual(core.workflow.store.read(), state)
        self.assertTrue(panel.accept_button.isEnabled())

    def test_natural_language_scope_is_visible_and_does_not_mutate_other_outline_fields(self):
        core, script = project(self.base, presenter=self.p)
        panel = self.w.workflow_panel
        panel.instruction.setPlainText("重写第一卷第二章，保留蓝信的知情边界。")
        self.assertEqual(panel.resolved_scope(), {"kind": "chapter", "volume": 1, "chapter": 2})
        self.assertIn("第2章", panel.scope_button.text())
        self.assertEqual(outline_scope("修改第二卷"), {"kind": "volume", "volume": 2})
        self.assertEqual(outline_scope("调整故事的结局"), {"kind": "book"})
        value = core.workflow.planning.approved()["value"]
        ambiguous = {**value, "volumes": value["volumes"] * 2}
        with self.assertRaises(CoreError):
            outline_scope("重写第二章", ambiguous)
        with self.assertRaises(CoreError):
            outline_scope("不要修改第二卷", value)
        with self.assertRaises(CoreError):
            outline_scope("修改第一卷和第二卷", value)

    def test_stop_is_direct_and_preserves_saved_content(self):
        core, script = project(self.base, presenter=self.p)
        run = core.workflow.pipeline.start(core.workflow.pipeline.preview("chapters", 1), True)
        self.p.workflow_auto = True
        self.p.workflow_run = run["run_id"]
        before={r['relative']:r['revision'] for r in core.documents() if r['area'] in {'canonical','drafts'}}
        with patch("novel_studio.desktop.workflow_controller.QMessageBox.question", side_effect=AssertionError('No repeated stop confirmation')):
            self.p.action("workflow_stop", {})
        self.assertEqual(core.workflow.pipeline.get(run["run_id"])["status"], "STOPPED")
        self.assertFalse(self.p.workflow_auto)
        self.assertEqual(before,{r['relative']:r['revision'] for r in core.documents() if r['area'] in {'canonical','drafts'}})

    def test_recent_projects_only_use_explicit_saved_entries(self):
        first, script = project(self.base, "第一部合成作品", self.p)
        second, script = project(self.base, "第二部合成作品", self.p)
        panel = self.w.workflow_panel
        self.assertEqual(panel.recent_projects.count(), 1)
        panel.recent_projects.setCurrentRow(0)
        self.assertTrue(panel.recent_open.isEnabled())
        panel.recent_open.click()
        self.assertEqual(self.p.core.project, first.project)
        with self.assertRaises(CoreError):
            self.p.action("open_recent", {"path": str(self.base / "unlisted")})

    def test_embedded_export_uses_shared_job_and_finishes_fast_txt_safely(self):
        core, script = project(self.base, presenter=self.p)
        complete(core)
        rows = core.workflow.chapters.catalog()
        preview = core.workflow.chapters.preview([r["chapter_id"] for r in rows])
        core.author_commit(preview["handle"], True)
        before = {r["relative"]: r["revision"] for r in core.documents() if r["area"] == "canonical"}
        self.w.navigate("export")
        dialog = self.w.export_widget
        self.assertIsNotNone(dialog)
        self.assertFalse(dialog.isWindow())
        dialog.format.setCurrentIndex(dialog.format.findData("txt"))
        dialog.initial_preview_button.click()
        self.assertIsNotNone(dialog.preview_value)
        self.assertFalse(dialog.export_button.isEnabled())
        dialog.confirm.setChecked(True)
        target = self.base / "合成作者导出.txt"
        with patch("novel_studio.desktop.export_ui.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            dialog.export_button.click()
        deadline = time.monotonic() + 10
        while (self.p.job is not None or dialog.job is not None) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.p.job)
        self.assertIsNone(dialog.job)
        self.assertTrue(dialog.tabs.isEnabled())
        self.assertIn("林舟", target.read_text(encoding="utf-8-sig"))
        self.assertEqual(before, {r["relative"]: r["revision"] for r in core.documents() if r["area"] == "canonical"})


if __name__ == "__main__":
    unittest.main()
