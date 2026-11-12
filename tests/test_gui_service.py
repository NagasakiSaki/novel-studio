from pathlib import Path
import json
import os
import queue
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'gui'))
import workspace_admin as admin
from safe_project import SafeTree
from service import Controller, RoleSession, friendly_error


class GuiServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='novel-gui-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve()
        (self.base/'bundle').mkdir()
        self.c=Controller(self.base/'bundle',self.base/'State')
        self.c.store.save({'plugin':str(ROOT)})
        self.c.new_project(self.base,'合成图形作品')
        self.root=self.c.project

    def test_defaults_and_protected_gui_roots(self):
        with SafeTree(self.root) as tree:
            data=json.loads(tree.read('.novel-studio/project.json'))
        self.assertIn(str(self.c.store.root),data['private_roots'])
        self.assertIn(str(self.base/'Apps'),data['private_roots'])
        self.assertEqual(self.c.state(),{})
        with self.assertRaises(ValueError):
            self.c.new_project(self.base,'../escape')

    def test_import_uses_exact_reviewed_plan_and_keeps_source(self):
        file=self.base/'人物.txt';file.write_text('林舟和周岚\n',encoding='utf-8')
        plan=self.c.import_preview(file,'Canon-Characters')
        target=self.root/plan['target']
        self.assertFalse(target.exists())
        file.write_text('作者改了来源\n',encoding='utf-8')
        with self.assertRaises(ValueError):self.c.import_approve(file,plan)
        plan=self.c.import_preview(file,'Canon-Characters')
        self.c.import_approve(file,plan)
        self.assertEqual(target.read_bytes(),file.read_bytes())
        with self.assertRaises(ValueError):self.c.import_preview(file,'../wrong')

    def test_handoff_and_author_feedback_are_immutable_copies(self):
        with SafeTree(self.root) as tree:
            tree.create('Canonical/Canon-Worldbuilding/world.md','钟楼已经停摆。\n'.encode())
            tree.create('Writer-Workspace/Drafts/第一章.md','旧草稿\n'.encode())
            tree.create('Editor-Workspace/Review-Reports/报告.md','建议\n'.encode())
        self.c.context()
        result=self.c.handoff('Writer-Workspace/Drafts/第一章.md')
        report='Editor-Workspace/Review-Reports/报告.md'
        feedback=self.c.feedback(report,'仅采纳日期修改')
        self.assertIn('仅采纳日期修改',self.c.read(feedback))
        self.assertEqual(self.c.state()['feedback'],feedback)
        (self.root/'Writer-Workspace/Drafts/第一章.md').write_text('后续改稿',encoding='utf-8')
        self.assertEqual(self.c.read(result['destination']+'/Drafts/第一章.md'),'旧草稿\n')
        with self.assertRaises(ValueError):self.c.handoff('Canonical/Final-Manuscript/not-draft.md')

    def test_ui_approval_and_restore_share_admin_pipeline(self):
        with SafeTree(self.root) as tree:
            tree.create('Writer-Workspace/Drafts/第一章.md','新稿\n'.encode())
            tree.create('Canonical/Final-Manuscript/第一章.md','旧稿\n'.encode())
        plan=self.c.preview('Writer-Workspace/Drafts/第一章.md','第一章.md')
        saved=self.c.approve(plan)
        undo=self.c.restore_preview(saved['transaction_id'])
        self.c.restore_approve(saved['transaction_id'],undo)
        self.assertEqual(self.c.read('Canonical/Final-Manuscript/第一章.md'),'旧稿\n')
        with self.assertRaises(ValueError):self.c.preview('Writer-Workspace/Drafts/第一章.md','../elsewhere.md')

    def test_role_turn_and_session_merge(self):
        one='11111111-1111-1111-1111-111111111111';two='22222222-2222-2222-2222-222222222222'
        admin.ui_state(self.root,{'sessions':{'writer':one}})
        admin.ui_state(self.root,{'sessions':{'editor':two}})
        self.assertEqual(self.c.state()['sessions'],{'writer':one,'editor':two})
        with admin.role_turn(self.root,'writer'):
            with self.assertRaises(ValueError):self.c.context()
            with self.assertRaises(FileExistsError):
                with admin.role_turn(self.root,'writer'):pass
        self.c.require_idle()

    def test_execution_export_is_explicit_local_and_new_file(self):
        self.assertFalse((self.c.store.root/'Exports').exists())
        events=[{'action':'synthetic','event':{'state':'ERROR','message':'example'}}]
        first=self.c.export_details(events)
        second=self.c.export_details(events)
        self.assertNotEqual(first,second)
        self.assertEqual(json.loads(first.read_bytes())['events'],events)
        self.assertEqual(first.parent,self.c.store.root/'Exports')

    def test_legacy_native_send_is_denied_without_inheriting_workbench_permission(self):
        draft='Writer-Workspace/Drafts/确切章节.md'
        report='Editor-Workspace/Review-Reports/意见.md'
        with SafeTree(self.root) as tree:
            tree.create(draft,'合成正文'.encode())
            tree.create(report,'合成意见'.encode())
        self.c.context()
        self.c.handoff(draft)
        note=self.c.feedback(report,'只修日期')
        from novel_studio.core import CoreError
        # Even a qualified unified-workbench report cannot grant the retired
        # native-write feature. Exercise the real require() gate, not its mock.
        report={'model_allowed':True,'features':{'conversation':True},'status':'FULL','cli_version':'1.5.3'}
        session=RoleSession(self.c,'writer',queue.Queue())
        with patch('novel_studio.compatibility.CompatibilityManager.inspect',return_value=report), patch('service.threading.Thread') as worker:
            with self.assertRaises(CoreError) as caught:session.send('按选定意见修订')
            self.assertEqual(caught.exception.code,'CLI_INCOMPATIBLE')
            worker.assert_not_called()
        self.assertFalse(session.busy)
        self.assertFalse(self.c.active)
        self.assertEqual(self.c.state()['last_draft'],draft)
        self.assertEqual(self.c.state()['feedback'],note)
        self.assertEqual(self.c.read(draft),'合成正文')

    def test_supported_writer_context_contains_exact_selected_draft_and_feedback(self):
        from novel_studio.core import Core, digest
        from novel_studio.prompting import assemble
        draft='Writer-Workspace/Drafts/确切章节.md'
        review='Editor-Workspace/Review-Reports/选定意见.md'
        with SafeTree(self.root) as tree:
            tree.create(draft,'合成正文，不会发送模型。'.encode())
            tree.create(review,'合成意见'.encode())
        self.c.context(); self.c.handoff(draft)
        note=self.c.feedback(review,'只修日期')
        core=Core(self.root)
        compiled,receipt=assemble(core,'writer','按选定意见生成新草稿',source_document_id=digest(draft.encode())[:32])
        self.assertIn((self.root/draft).as_posix(),compiled.dynamic_text)
        self.assertIn((self.root/note).as_posix(),compiled.dynamic_text)
        self.assertIn('只修日期',self.c.read(note))
        self.assertEqual(self.c.read(draft),'合成正文，不会发送模型。')

    def test_stream_adapter_uses_scoped_official_cli_and_saves_id(self):
        self.c.context()
        cli=self.base/('agy.exe' if os.name=='nt' else 'agy');cli.write_bytes(b'synthetic never executed')
        self.c.cli=cli
        cid='33333333-3333-3333-3333-333333333333'
        events=[{'event':'init','conversation_id':cid,'init':{'tools':['view_file']}},
                {'event':'step_update','step_update':{'step_type':'tool','state':'ERROR','tool_name':'write_to_file','tool_info':{'error':{'message':'Matches user-configured deny rule'}}}},
                {'event':'result','result':{'status':'SUCCESS','response':'只写草稿'}}]
        class Process:
            stdout=iter(json.dumps(e)+'\n' for e in events)
            def wait(self):return 0
            def poll(self):return 0
        session=RoleSession(self.c,'writer',queue.Queue());session.busy=True;self.c.active.add('writer')
        with patch('service.subprocess.Popen',return_value=Process()) as popen:
            session._run('合成请求',None)
            argv=popen.call_args.args[0]
            self.assertIn('--settings',argv)
            self.assertIn('novel-writer',argv)
            self.assertNotIn('--dangerously-skip-permissions',argv)
            self.assertNotIn('shell',popen.call_args.kwargs)
        self.assertEqual(self.c.state()['sessions']['writer'],cid)
        self.assertFalse(session.busy)
        self.assertFalse((self.root/'.novel-studio/.ns-writer-turn.lock').exists())

    def test_errors_are_plain_language(self):
        self.assertIn('重新预览',friendly_error(ValueError('Target revision changed')))
        self.assertIn('没有更早版本',friendly_error(ValueError('No previous file')))
        with self.assertRaises(ValueError):SafeTree(self.root,limit=1024*1024*257)


if __name__=='__main__':unittest.main()
