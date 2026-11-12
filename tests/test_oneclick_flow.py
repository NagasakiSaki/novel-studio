"""Visible bounded scope and author-click consent, without routine modals."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from PySide6.QtWidgets import QApplication, QMessageBox
from novel_studio.desktop import workflow_controller
from novel_studio.desktop.workflow_ui import StartDialog
from test_product13 import ProductModels
from test_outline_first import macro
from ui2_fixtures import project
import test_workflow_ui as existing_ui


class OneClickUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    setUp=existing_ui.WorkflowUITests.setUp
    tearDown=existing_ui.WorkflowUITests.tearDown
    prepare_outline=existing_ui.WorkflowUITests.prepare_outline
    drain=existing_ui.WorkflowUITests.drain

    def test_full_book_scope_shows_outline_volumes_before_execution_directory_exists(self):
        outline=self.core.workflow.planning.save(macro(8))
        self.core.author_commit(self.core.workflow.planning.preview(outline['version_id'])['handle'],True)
        self.p.refresh_view()
        self.assertIn('大纲 8 卷',self.w.workflow_panel.task_scope_summary.text())
        self.assertIn('全书',self.w.workflow_panel.home_task_scope.text())

    def test_visible_choice_starts_two_chapters_without_scope_or_confirmation_dialog(self):
        self.prepare_outline()
        panel=self.w.workflow_panel
        self.assertEqual(panel.task_scope.currentData(),'book')
        self.assertIn('全书',panel.home_task_scope.text())
        panel.task_scope.setCurrentIndex(panel.task_scope.findData('chapters'))
        panel.task_count.setValue(2)
        model=self.core.models;script=existing_ui.ScriptedModels(self.core)
        self.core.models=model;model.structured_task=script.structured_task
        errors=[]
        with patch.object(StartDialog,'exec',side_effect=AssertionError('No start dialog')),patch.object(QMessageBox,'question',side_effect=AssertionError('No routine confirmation')),patch.object(QMessageBox,'warning',side_effect=lambda *a:errors.append(str(a))):
            panel.run_start.click()
            self.drain(lambda:self.core.workflow.pipeline.active()['status']=='COMPLETED' and self.p.job is None)
        self.assertFalse(errors,errors)
        self.assertEqual(len(script.calls),6)
        run=self.core.workflow.pipeline.active()
        self.assertEqual(run['requested_count'],2)
        self.assertEqual(run['review_policy'],'continue_drafts')
        self.assertIn('后续 2 章',panel.task_scope_summary.text())
        self.assertEqual(self.core.workflow.store.read()['creation_preferences'],{'mode':'chapters','count':2})
        self.assertTrue(all(not r.get('canonical') for r in self.core.workflow.chapters.catalog()))

    def test_pause_resume_stop_have_no_repeated_confirmation(self):
        self.prepare_outline();pipeline=self.core.workflow.pipeline
        run=pipeline.start(pipeline.preview('chapters',1,review_policy='continue_drafts'),True)
        with patch.object(QMessageBox,'question',side_effect=AssertionError('No routine confirmation')):
            self.p.action('workflow_pause',{})
            self.assertEqual(pipeline.get(run['run_id'])['status'],'PAUSED')
            with patch.object(workflow_controller,'activate') as activated:
                self.p.action('workflow_resume',{})
                self.assertEqual(activated.call_args.args[1]['status'],'RUNNING')
            self.p.action('workflow_stop',{})
            self.assertEqual(pipeline.get(run['run_id'])['status'],'STOPPED')

    def test_failed_step_has_explicit_retry_and_scope_and_background_origin_stays_workflow(self):
        self.prepare_outline();pipeline=self.core.workflow.pipeline
        run=pipeline.start(pipeline.preview('chapters',1,review_policy='continue_drafts'),True)
        self.p.workflow_auto=True;self.p.workflow_run=run['run_id'];self.p.last_action='role_selected'
        with patch.object(self.p,'start_job') as started:
            workflow_controller.tick(self.p)
        self.assertEqual(started.call_args.kwargs['diagnostic_action'],'workflow_step')
        self.p.workflow_auto=False
        self.core.workflow.store.mutate('synthetic_failed',lambda s,t:s['runs'][run['run_id']].update(status='FAILED',error={'code':'HOST_RESULT_ERROR','message':'合成宿主失败'}))
        self.w.workflow_panel.bind(self.core)
        self.assertFalse(self.w.workflow_panel.retry_button.isHidden())
        self.assertIn('追加计费',self.w.workflow_panel.retry_button.text())
        self.assertIn('后续 1 章',self.w.workflow_panel.task_scope_summary.text())
        self.assertEqual(self.w.workflow_panel.task_scope.currentData(),'chapters')
        self.assertEqual(self.w.workflow_panel.task_count.value(),1)


class OneClickCoreTests(unittest.TestCase):
    def test_unresolved_review_is_preserved_after_two_repairs_and_next_draft_continues(self):
        with tempfile.TemporaryDirectory(prefix='novel-oneclick-synthetic-') as folder:
            core,_=project(Path(folder),'合成无人干预草稿',approve=False)
            outline=core.workflow.planning.save(macro(1))
            core.author_commit(core.workflow.planning.preview(outline['version_id'])['handle'],True)
            model=ProductModels(core,bad_reviews=99)
            before={r['relative']:r['revision'] for r in core.documents() if r['area']=='canonical'}
            pipeline=core.workflow.pipeline
            run=pipeline.start(pipeline.preview('chapters',1,review_policy='continue_drafts'),True)
            for _ in range(80):
                run=pipeline.advance(run['run_id'])
                if run['status']=='COMPLETED':break
                self.assertEqual(run['status'],'RUNNING')
            self.assertEqual(run['status'],'COMPLETED')
            row=core.workflow.chapters.catalog()[0]
            self.assertEqual(row['repair_rounds'],2)
            self.assertTrue(row['review_pending_author'])
            self.assertFalse(row.get('review_acknowledged',False))
            self.assertEqual(len(row['review_history']),3)
            self.assertEqual(len(model.calls),9)
            self.assertEqual(before,{r['relative']:r['revision'] for r in core.documents() if r['area']=='canonical'})


if __name__=='__main__':unittest.main()
