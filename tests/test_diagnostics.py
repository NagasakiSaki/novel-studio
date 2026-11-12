"""Logging protects creative text and helps locate real UI/model failures."""
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests')]
from novel_studio.diagnostics import DiagnosticLog, MAX_BYTES, SLOTS
from novel_studio.core import CoreError, SafeTree, digest


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='novel-diagnostic-synthetic-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'应用状态'
        self.log = DiagnosticLog(self.root)

    def test_error_stack_and_id_without_exception_message_locals_paths_or_keys(self):
        secret = 'sk-' + 'secret-synthetic-1234567890'
        try:
            raise CoreError('PLAN_INVALID', '私人正文和提示词 '+secret)
        except CoreError as error:
            identity = self.log.error(error, action='workflow_start', prompt=secret, path=str(self.root), text=secret)
        report = self.log.report()
        text = json.dumps(report, ensure_ascii=False)
        self.assertIn(identity,text)
        self.assertIn('PLAN_INVALID',text)
        self.assertIn('frames',text)
        for private in (secret, '私人正文', str(self.root), 'prompt', 'traceback.format_exc'):
            self.assertNotIn(private,text)

    def test_threaded_rotation_is_bounded_and_ignores_generic_logging_payload(self):
        record=logging.LogRecord('unsafe',logging.ERROR,'',0,'私人正文',(),None)
        self.log.handle(record)
        def append():
            for i in range(90): self.log.event('ui_action', action='workflow_start', result=i)
        with patch('novel_studio.diagnostics.MAX_BYTES', 5000):
            threads=[threading.Thread(target=append) for _ in range(3)]
            [t.start() for t in threads]
            [t.join() for t in threads]
        self.assertTrue(self.log.available)
        files=list(self.log.root.glob('events-*.jsonl'))
        self.assertLessEqual(len(files),SLOTS)
        self.assertTrue(all(f.stat().st_size <= 5000 for f in files))
        self.assertNotIn('私人正文', json.dumps(self.log.report(),ensure_ascii=False))

    def test_write_failure_falls_back_without_masking_original_error(self):
        with patch.object(SafeTree,'publish',side_effect=PermissionError('secret raw failure')):
            identity=self.log.error(ValueError('secret original'))
        report=self.log.report()
        self.assertFalse(report['persisted'])
        self.assertIn(identity,json.dumps(report))
        self.assertNotIn('secret',json.dumps(report))

    def test_disable_persists_and_export_survives_partial_record(self):
        self.log.event('ui_action',action='workflow_start')
        self.log.set_enabled(False)
        self.assertIsNone(self.log.event('ui_error',error_code='PRIVATE'))
        reopened=DiagnosticLog(self.root)
        self.assertFalse(reopened.enabled)
        reopened.set_enabled(True)
        with SafeTree(self.log.root) as tree:
            data=tree.read('events-0.jsonl')
            tree.publish('events-0.jsonl',data+b'{bad partial',digest(data))
        self.assertTrue(reopened.report()['events'])

    def test_qt_dialog_and_presenter_error_are_recorded_with_safe_metadata(self):
        from PySide6.QtWidgets import QApplication, QMessageBox
        from PySide6.QtCore import QTimer
        from novel_studio.desktop.window import Workbench
        from novel_studio.desktop.presenter import Presenter
        from novel_studio.desktop.diagnostic_ui import DiagnosticObserver
        from ui2_fixtures import LocalOnlyCLI
        app=QApplication.instance() or QApplication([])
        w=Workbench()
        p=Presenter(w,self.root,LocalOnlyCLI(),diagnostic_log=self.log)
        observer=DiagnosticObserver(app,self.log)
        try:
            QTimer.singleShot(30,lambda: [d.accept() for d in app.topLevelWidgets() if isinstance(d,QMessageBox)])
            p.show_error(CoreError('PLAN_INVALID','合成错误提示，不记录正文'))
            events=self.log.report()['events']
            self.assertTrue(any(r['event']=='ui_error' and r['error_code']=='PLAN_INVALID' for r in events))
            self.assertTrue(any(r['event']=='window_show' and r['dialog_class']=='QMessageBox' for r in events))
            self.assertNotIn('合成错误提示',json.dumps(events,ensure_ascii=False))
        finally:
            observer.close()
            w.close()
            w.deleteLater()
            app.processEvents()

    def test_log_window_exports_bounded_metadata_through_existing_safe_io(self):
        from PySide6.QtWidgets import QApplication, QDialog, QPushButton
        from PySide6.QtCore import QTimer
        from novel_studio.desktop.window import Workbench
        from novel_studio.desktop.presenter import Presenter
        from novel_studio.desktop.diagnostic_ui import show_logs
        from ui2_fixtures import LocalOnlyCLI
        app=QApplication.instance() or QApplication([])
        w=Workbench()
        p=Presenter(w,self.root,LocalOnlyCLI(),diagnostic_log=self.log)
        self.log.event('ui_error',error_code='PLAN_INVALID',error_id='123456789abc',prompt='private-synthetic-prompt')
        target=Path(self.tmp.name)/'诊断导出.json'
        def export_and_close():
            dialog=next(d for d in app.topLevelWidgets() if isinstance(d,QDialog) and d.windowTitle().startswith('诊断日志'))
            button=next(b for b in dialog.findChildren(QPushButton) if b.text()=='导出诊断日志')
            button.click()
            dialog.accept()
        try:
            with patch('novel_studio.desktop.diagnostic_ui.QFileDialog.getSaveFileName',return_value=(str(target),'JSON')):
                QTimer.singleShot(30,export_and_close)
                show_logs(p)
            text=target.read_text(encoding='utf-8')
            self.assertIn('PLAN_INVALID',text)
            self.assertNotIn('private-synthetic-prompt',text)
            with self.assertRaises(FileExistsError):
                with SafeTree(target.parent) as tree:tree.create(target.name,b'cannot overwrite')
        finally:
            w.close()
            w.deleteLater()
            app.processEvents()

    def test_startup_failure_records_id_without_exposing_exception_text(self):
        import builtins
        import ctypes
        import runpy
        original=builtins.__import__
        def importing(name,*args,**kwargs):
            if name=='novel_studio.__main__':raise OSError('synthetic-private-startup-message')
            return original(name,*args,**kwargs)
        with patch.dict('os.environ',{'LOCALAPPDATA':str(Path(self.tmp.name))}), patch.object(builtins,'__import__',side_effect=importing), patch.object(ctypes.windll.user32,'MessageBoxW',return_value=1) as popup:
            with self.assertRaises(SystemExit) as result:runpy.run_path(str(ROOT/'desktop/desktop-entry.pyw'))
        self.assertEqual(result.exception.code,1)
        self.assertNotIn('synthetic-private-startup-message',str(popup.call_args))
        self.assertIn('诊断编号',str(popup.call_args))
        log=DiagnosticLog(Path(self.tmp.name)/'NovelStudioDesktop')
        self.assertTrue(any(row.get('error_class')=='OSError' for row in log.report()['events']))

    def test_disabled_start_dialog_records_its_actual_blocking_error(self):
        from PySide6.QtWidgets import QApplication
        from novel_studio.core import Core
        from novel_studio.desktop.window import Workbench
        from novel_studio.desktop.presenter import Presenter
        from novel_studio.desktop.workflow_ui import StartDialog
        from ui2_fixtures import LocalOnlyCLI
        app=QApplication.instance() or QApplication([])
        w=Workbench()
        p=Presenter(w,self.root,LocalOnlyCLI(),diagnostic_log=self.log)
        c=Core.create(Path(self.tmp.name),'合成阻断诊断',ROOT)
        p.open_project(c.project)
        try:
            dialog=StartDialog(p.core,w)
            self.assertFalse(dialog.start.isEnabled())
            self.assertIn('诊断编号',dialog.summary.toPlainText())
            self.assertTrue(any(row.get('dialog_class')=='StartDialog' and row.get('error_id') in dialog.summary.toPlainText() for row in self.log.report()['events'] if row.get('error_id')))
            dialog.reject()
            dialog.deleteLater()
        finally:
            w.close()
            w.deleteLater()
            app.processEvents()


if __name__=='__main__': unittest.main()
