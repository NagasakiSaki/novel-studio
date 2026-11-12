"""Object -> visible action -> actual service result, entirely synthetic."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import json
import zipfile
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests')]
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from novel_studio.core import CoreError, SafeTree, READ, WRITE
from novel_studio.desktop.window import Workbench
from novel_studio.desktop.presenter import Presenter
from novel_studio.desktop.export_ui import ExportDialog
from novel_studio.desktop.dialogs import DiffDialog
from novel_studio.desktop.workflow_ui import NumberingDialog
from novel_studio.desktop.object_actions import chapter_actions
from novel_studio.document_titles import numbered_title
from novel_studio.project_backup import backup
from test_product13 import ProductModels
from ui2_fixtures import project, complete, LocalOnlyCLI
import test_workflow_ui as workflow_ui_tests


def actions(menu):
    return [child for item in menu.actions() for child in (actions(item.menu()) if item.menu() else [item])]


class ObjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='novel-object-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.w = Workbench()
        self.p = Presenter(self.w, self.base/'app-state', LocalOnlyCLI())
        self.core, self.script = project(self.base, '对象操作合成验收', presenter=self.p)
        complete(self.core)
        self.p.refresh_view()
        self.rows = self.core.workflow.chapters.catalog()
        self.a, self.b = self.rows[:2]
        self.p.show_error = lambda error: self.fail('Unexpected UI error: ' + str(error))

    def tearDown(self):
        self.p.workflow_auto = False
        self.p.workflow_timer.stop()
        if self.p.job:
            self.p.job.wait(30000)
            self.app.processEvents()
        self.w.deleteLater()
        self.app.processEvents()

    def data(self, **values):
        return {'_project_id':self.core.project_id, **values}

    def formal_hashes(self):
        return {r['relative']:r['revision'] for r in self.core.documents() if r['area']=='canonical'}

    def test_chapter_has_all_eight_actions_and_right_click_binds_hit_not_later_selection(self):
        table = self.w.workflow_panel.table
        table.selectRow(0)
        menu = table.object_menu(table.leaves[1])
        self.addCleanup(menu.deleteLater)
        keys = {item.data():item for item in actions(menu)}
        expected = {'object_chapter_read','object_chapter_edit','object_writer_revise','object_editor_review',
                    'object_chapter_review','object_chapter_versions','object_chapter_export','workflow_accept'}
        self.assertTrue(expected <= keys.keys())
        self.assertEqual(self.w.workflow_panel.selected_chapter(), self.b['chapter_id'])
        table.selectRow(0)
        before = self.formal_hashes()
        keys['object_chapter_review'].trigger()
        self.assertEqual(self.w.workflow_panel.selected_chapter(), self.b['chapter_id'])
        self.assertEqual(self.w.workflow_panel.chapter_tabs.currentIndex(), 2)
        self.assertTrue(self.w.workflow_panel.chapter_views['review'].toPlainText())
        self.assertEqual(before, self.formal_hashes())
        self.assertIsNone(table.object_menu(None))
        self.assertTrue(self.w.workflow_panel.chapter_object_bar.buttons)

    def test_revise_and_review_actions_prepare_correct_chapter_then_save_real_mock_results(self):
        models = self.core.models
        script = ProductModels(self.core, bad_reviews=0)
        self.core.models = models
        models.structured_task = script.structured_task
        before = self.formal_hashes()
        self.p.action('object_writer_revise', self.data(chapter_id=self.b['chapter_id']))
        self.assertEqual(self.w.role, 'writer')
        self.assertEqual(self.w.chat_operation.currentData(), 'revise')
        self.assertEqual(self.w.chat_chapter_id, self.b['chapter_id'])
        self.assertEqual(len(script.calls), 0)
        self.w.send_buttons['writer'].click()
        workflow_ui_tests.WorkflowUITests.drain(self, lambda:self.p.job is None)
        revised = self.core.workflow.store.read()['chapters'][self.b['chapter_id']]
        self.assertNotEqual(revised['draft']['document_id'], self.b['draft']['document_id'])
        self.p.action('object_editor_review', self.data(chapter_id=self.b['chapter_id']))
        self.assertEqual(self.w.role, 'editor')
        self.assertEqual(self.w.chat_operation.currentData(), 'review')
        self.w.send_buttons['editor'].click()
        workflow_ui_tests.WorkflowUITests.drain(self, lambda:self.p.job is None)
        reviewed = self.core.workflow.store.read()['chapters'][self.b['chapter_id']]
        self.assertTrue(reviewed['review'])
        self.assertEqual(before, self.formal_hashes())

    def test_exact_text_selection_keeps_qt_menu_and_only_quotes_selected_fragment(self):
        self.p.load_document(self.a['draft']['document_id'])
        self.w.switch_role('writer')
        text = '整章开头\n仅此选段😀\n整章末尾'
        self.w.editor.setPlainText(text)
        cursor = self.w.editor.textCursor()
        start = len('整章开头\n'.encode('utf-16-le'))//2
        cursor.setPosition(start)
        cursor.setPosition(start+len('仅此选段😀'.encode('utf-16-le'))//2, QTextCursor.KeepAnchor)
        self.w.editor.setTextCursor(cursor)
        menu = self.w.editor.standard_object_menu(QPoint(10, 10))
        self.addCleanup(menu.deleteLater)
        values = actions(menu)
        self.assertGreater(len(values), 6)
        selected = next(a for a in values if a.data() == 'object_quote_rewrite')
        selected.trigger()
        prompt = self.w.chat_inputs['writer'].toPlainText()
        self.assertIn('仅此选段😀', prompt)
        self.assertNotIn('整章开头', prompt)
        self.assertNotIn('整章末尾', prompt)
        self.assertEqual(self.w.chat_operation.currentData(), 'discuss')
        self.assertEqual(self.w.editor.toPlainText(), text)
        self.assertEqual(self.w.chat_chapter_id, self.a['chapter_id'])
        self.w.editor.document().setModified(False)
        self.w.chat_inputs['writer'].clear()

    def test_chapter_export_selects_only_that_version_and_source_mutation_is_detected(self):
        before = self.formal_hashes()
        self.p.action('object_chapter_export', self.data(chapter_id=self.b['chapter_id']))
        dialog = self.w.export_widget
        self.assertEqual([r['source_id'] for r in dialog.selected()], [self.b['draft']['document_id']])
        dialog.format.setCurrentIndex(dialog.format.findData('txt'))
        dialog.build_preview()
        self.assertEqual(len(dialog.preview_value['sources']), 1)
        handle = dialog.preview_value['handle']
        draft = self.core.document(self.b['draft']['document_id'])
        self.core.dispatch(self.core.bind('writer', READ|WRITE), 'update_draft', {'document_id':draft['document_id'],
            'text':draft['text']+'\n合成变更', 'expected_revision':draft['revision']}, 'object-export-changed')
        with self.assertRaises(CoreError) as error:
            dialog.service.export(handle, self.base/'不能发布.txt', True)
        self.assertEqual(error.exception.code, 'EXPORT_CHANGED')
        self.assertFalse((self.base/'不能发布.txt').exists())
        self.assertEqual(before, self.formal_hashes())

    def test_approval_still_needs_author_dialogs_and_formal_actions_are_distinct(self):
        cid = self.a['chapter_id']
        with patch.object(NumberingDialog, 'exec', return_value=QDialog.Rejected):
            self.p.action('workflow_accept', self.data(chapter_ids=[cid]))
        self.assertIsNone(self.core.workflow.store.read()['chapters'][cid]['canonical'])
        with patch.object(NumberingDialog, 'exec', return_value=QDialog.Accepted), patch.object(DiffDialog, 'exec', return_value=QDialog.Accepted):
            self.p.action('workflow_accept', self.data(chapter_ids=[cid]))
        formal = self.core.workflow.store.read()['chapters'][cid]
        self.assertTrue(formal['canonical'])
        keys = [a['key'] for a in chapter_actions(formal, self.core.project_id)]
        self.assertNotIn('object_chapter_edit', keys)
        self.assertNotIn('workflow_accept', keys)
        self.assertIn('object_writer_revise', keys)

    def test_filter_excludes_hidden_checked_chapters_from_approval(self):
        panel = self.w.workflow_panel
        self.w.navigate('chapters')
        panel.table.item(0, 0).setCheckState(Qt.Checked)
        panel.table.selectRow(0)
        self.assertEqual(panel.acceptance_ids(), [self.a['chapter_id']])
        panel.chapter_search.setText("没有任何章节包含的合成词")
        self.assertEqual(panel.acceptance_ids(), [])
        self.assertIsNone(panel.selected_chapter())
        self.assertTrue(panel.accept_button.isHidden())
        self.assertFalse(panel.chapter_empty.isHidden())
        self.assertFalse(panel.chapter_object_bar.buttons)
        panel.chapter_search.clear()
        self.assertEqual(panel.acceptance_ids(), [self.a['chapter_id']])

    def test_document_return_preserves_filter_selection_and_unsaved_draft(self):
        w = self.w
        panel = w.workflow_panel
        w.navigate('chapters')
        panel.chapter_filter.setCurrentIndex(panel.chapter_filter.findData('pending'))
        panel.table.selectRow(0)
        self.p.action('object_chapter_edit', self.data(chapter_id=self.a['chapter_id']))
        self.assertIs(w.tabs.currentWidget(), w.document_page)
        self.assertEqual(w.document_back_buttons[0].text(), '返回章节')
        w.editor.insertPlainText('尚未保存的合成文字')
        w.document_back_buttons[0].click()
        self.assertEqual(panel.chapter_filter.currentData(), 'pending')
        self.assertEqual(panel.selected_chapter(), self.a['chapter_id'])
        w.navigate('files')
        w.resume_document.click()
        self.assertIs(w.tabs.currentWidget(), w.document_page)
        self.assertTrue(w.editor.document().isModified())
        self.assertIn('尚未保存的合成文字', w.editor.toPlainText())
        w.editor.document().setModified(False)
        w.document_back_buttons[0].click()
        self.assertIs(w.tabs.currentWidget(), w.file_explorer)
        report_id = self.a['review']['document_id']
        report_group = next(w.tree.topLevelItem(i) for i in range(w.tree.topLevelItemCount())
                            if w.tree.topLevelItem(i).text(0) == '独立审稿报告')
        report_item = next(report_group.child(i) for i in range(report_group.childCount())
                           if report_group.child(i).data(0, Qt.UserRole) == report_id)
        w.tree.setCurrentItem(report_item)
        self.p.load_document(report_id)
        self.assertIs(w.tabs.currentWidget(), w.review_page)
        self.assertEqual(w.breadcrumb.text(), '审稿与意见')
        self.p.refresh_view()
        w.document_back_buttons[1].click()
        self.assertIs(w.tabs.currentWidget(), w.file_explorer)
        self.assertEqual(w.tree.currentItem().data(0, Qt.UserRole), report_id)

    def test_outline_and_material_and_settings_have_selection_feedback_without_model_calls(self):
        panel = self.w.workflow_panel
        menu = panel.version_list.object_menu(panel.version_list.item(0))
        self.addCleanup(menu.deleteLater)
        self.assertIn('object_outline_read', [a.data() for a in actions(menu)])
        self.assertNotIn('outline_approve', [a.data() for a in actions(menu)])
        self.assertTrue(panel.outline_object_bar.buttons)
        self.w.navigate('settings')
        self.w.settings_categories.setCurrentRow(1)
        self.assertEqual(self.w.settings_content.currentIndex(), 1)
        before = self.formal_hashes()
        self.core.rename_title('合成作品新显示名')
        self.p.refresh_view()
        self.assertEqual(self.w.project_title.text(), '合成作品新显示名')
        self.assertEqual(before, self.formal_hashes())

    def test_message_utf16_ranges_quote_only_hit_message_and_bind_its_chapter(self):
        state = self.core.workflow.store.read()
        entries = [{'user':'作者甲😀', 'assistant':'只给甲的答复', 'scope':'chapter','chapter_id':self.a['chapter_id']},
                   {'user':'作者乙', 'assistant':'只给乙的答复😀', 'scope':'chapter','chapter_id':self.b['chapter_id']}]
        self.w.interactions.bind_history('editor', entries, state)
        spans = self.w.interactions.ranges['editor']
        selected = spans[-1]
        values = self.w.interactions.message_actions('editor', selected['start']+2)
        self.w.interactions.dispatch('object_quote_writer', next(a['data'] for a in values if a['key']=='object_quote_writer'))
        self.assertEqual(self.w.chat_chapter_id, self.b['chapter_id'])
        self.assertIn('只给乙的答复😀', self.w.chat_inputs['writer'].toPlainText())
        self.assertNotIn('只给甲的答复', self.w.chat_inputs['writer'].toPlainText())
        self.w.chat_inputs['writer'].clear()

    def test_old_project_menu_is_rejected_after_project_switch(self):
        data = self.data(chapter_id=self.a['chapter_id'])
        other, unused = project(self.base, '另一部合成作品', approve=False)
        self.p.open_project(other.project)
        with self.assertRaises(CoreError) as error:
            self.p.action('object_writer_revise', data)
        self.assertEqual(error.exception.code, 'OBJECT_CHANGED')

    def test_removing_project_clears_open_documents_and_material_context(self):
        before = self.formal_hashes()
        self.p.load_document(self.a["draft"]["document_id"])
        self.w.chat_inputs["writer"].setPlainText("尚未发送的合成输入")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.p.action("object_project_remove", self.data())
        self.assertIsNone(self.p.core)
        self.assertIsNone(self.w.document_id)
        self.assertEqual(self.w.tree.topLevelItemCount(), 0)
        self.assertEqual(self.w.material_panel.tree.topLevelItemCount(), 0)
        self.assertFalse(self.w.chat_inputs["writer"].toPlainText())
        self.assertEqual(self.w.navigation_page, "home")
        self.assertEqual(before, self.formal_hashes())

    def test_backup_copy_is_complete_no_clobber_and_leaves_sources_unchanged(self):
        before = self.formal_hashes()
        target = self.base/'合成备份.zip'
        result = backup(self.core, target)
        with zipfile.ZipFile(target) as archive:
            manifest = json.loads(archive.read('NOVEL-STUDIO-BACKUP.json'))
            self.assertTrue(manifest['files'])
            for relative in before:
                self.assertIn(relative, archive.namelist())
        self.assertEqual(before, self.formal_hashes())
        with self.assertRaises(CoreError) as conflict:
            backup(self.core, target)
        self.assertEqual(conflict.exception.code,'BACKUP_EXISTS')
        self.assertTrue(result['files'] > 1)

    def test_material_exclusion_and_recycle_require_approval_and_preserve_original(self):
        source=self.base/'合成资料.md'
        source.write_text('# 全局规则\n保留合成作者约定。\n\n# 背景\n蓝信锁在柜中。',encoding='utf-8')
        item=self.core.materials.prepare(source,'global_preset')
        preview=self.core.materials.preview(item,[], 'full',scope_confirmed=True)
        self.core.author_commit(preview['handle'],True)
        mid=preview['material_id']
        self.p.refresh_view()
        self.w.resize(1200,880)
        self.w.navigate('materials')
        self.w.show()
        self.app.processEvents()
        tree=self.w.material_panel.tree
        before_y=tree.mapTo(self.w,QPoint(0,0)).y()
        tree.setCurrentItem(tree.topLevelItem(0).child(0))
        self.app.processEvents()
        self.assertEqual(before_y,tree.mapTo(self.w,QPoint(0,0)).y(),'selection must not move the native right-click target')
        self.assertTrue(self.w.interactions.material_bar.buttons)
        material = self.w.material_panel
        tree.setCurrentItem(tree.currentItem().child(0))
        section = tree.currentItem().data(0, Qt.UserRole)
        self.p.refresh_view()
        self.assertEqual(tree.currentItem().data(0, Qt.UserRole), section)
        material.query.setText('蓝信')
        material.search()
        self.assertIsNone(material.current)
        self.assertFalse(material.more_button.isEnabled())
        self.assertFalse(self.w.interactions.material_bar.buttons)
        self.assertIn('蓝信锁在柜中', material.content.toPlainText())
        self.p.action('object_material_preview', self.data(material_id=mid))
        self.assertEqual(tree.currentItem().data(0, Qt.UserRole), (mid, None))
        self.assertEqual(material.current, mid)
        self.assertIn('原始来源', material.info.text())
        self.core.snapshot()
        self.core.handoff(self.a['draft']['document_id'])
        before=self.core.materials.get(mid)
        for role in ['writer','editor']:
            self.assertIn('蓝信锁在柜中',self.core.materials.context(role,'蓝信')[0])
        with patch.object(DiffDialog,'exec',return_value=QDialog.Rejected):
            self.p.action('object_material_toggle',self.data(material_id=mid))
        self.assertTrue(self.core.materials.get(mid)['context_enabled'])
        with patch.object(DiffDialog,'exec',return_value=QDialog.Accepted):
            self.p.action('object_material_toggle',self.data(material_id=mid))
        for role in ['writer','editor']:
            self.assertNotIn('蓝信锁在柜中',self.core.materials.context(role,'蓝信')[0])
        with patch.object(DiffDialog,'exec',return_value=QDialog.Accepted):
            self.p.action('object_material_retire',self.data(material_id=mid))
        self.assertEqual(self.core.materials.records(),[])
        self.assertEqual(self.core.materials.get(mid)['text'],before['text'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before['source']['sha256'])
        with patch.object(DiffDialog,'exec',return_value=QDialog.Accepted):
            self.p.action('object_material_restore',self.data(material_id=mid))
        self.assertFalse(self.core.materials.get(mid)['retired'])
        self.assertEqual(self.core.materials.get(mid)['text'],before['text'])

    def test_author_added_chapter_uses_existing_execution_service_and_keeps_outline(self):
        before=self.formal_hashes()
        with patch('novel_studio.desktop.object_controller.QInputDialog.getText',return_value=('作者新增的章节',True)):
            self.p.action('object_volume_new_draft',self.data(volume=1))
        cid=next(r['chapter_id'] for r in self.core.workflow.chapters.catalog() if r.get('author_added'))
        row=self.core.workflow.store.read()['chapters'][cid]
        self.assertEqual(row['number'],3)
        self.assertTrue(row['author_added'])
        self.assertEqual(row['title'],'作者新增的章节')
        self.assertTrue(row['draft'])
        self.assertIn(cid,self.core.lengths.plan()['nodes'])
        self.assertFalse(self.core.workflow.execution.current()['story_complete'])
        self.assertEqual(self.w.document_id,row['draft']['document_id'])
        self.assertEqual(self.w.workflow_panel.selected_chapter(),cid)
        self.assertEqual(before,self.formal_hashes())

    def test_volume_open_rename_and_reorder_only_change_reading_directory(self):
        before=self.formal_hashes()
        with patch('novel_studio.desktop.object_controller.QInputDialog.getText',return_value=('作者调整的卷名',True)):
            self.p.action('object_volume_rename',self.data(volume=1))
        self.p.action('object_volume_open',self.data(volume=1))
        self.assertEqual(self.w.workflow_panel.selected_chapter(),self.a['chapter_id'])
        self.assertTrue(all(r['directory']['volume_title']=='作者调整的卷名' for r in self.core.workflow.chapters.catalog()))
        self.core.workflow.chapters.move_volume(1,1)
        self.assertEqual(before,self.formal_hashes())

    def test_job_setup_failure_does_not_leave_interface_permanently_busy(self):
        from unittest.mock import Mock
        operation=Mock()
        with patch.object(self.w.workflow_panel,'bind',side_effect=CoreError('SOURCE_CHANGED','合成失效来源')):
            with self.assertRaises(CoreError):
                self.p.start_job(operation,lambda value:None,'合成任务')
        self.assertIsNone(self.p.job)
        operation.assert_not_called()

    def test_formal_review_uses_formal_bytes_and_preserves_archive_status_and_draft(self):
        cid=self.a['chapter_id']
        self.core.author_commit(self.core.workflow.chapters.preview([cid])['handle'],True)
        before=self.formal_hashes()
        models=self.core.models
        script=ProductModels(self.core,bad_reviews=0)
        self.core.models=models
        models.structured_task=script.structured_task
        old=self.core.workflow.store.read()['chapters'][cid]
        result=self.core.workflow.author.send('editor','仅审查正式版本',operation='review',chapter_id=cid,source_kind='canonical')
        current=self.core.workflow.store.read()['chapters'][cid]
        self.assertEqual(current['status'],'ARCHIVED')
        self.assertEqual(current['draft'],old['draft'])
        self.assertTrue(current['formal_review'])
        self.assertEqual(before,self.formal_hashes())
        self.assertIn('独立报告',result['message'])


if __name__ == '__main__':
    unittest.main()
