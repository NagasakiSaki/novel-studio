"""Failure evidence is useful without logging arbitrary host messages/content."""
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from novel_studio.core import CoreError
from novel_studio.host import OfficialCLI
from novel_studio.host_diagnostics import HostTrace
from novel_studio.diagnostics import DiagnosticLog, scope


class HostDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='novel-host-result-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        exe=self.root/'agy.exe';exe.write_bytes(b'Synthetic placeholder, never executed')
        self.cli=OfficialCLI(exe)
        self.log=DiagnosticLog(self.root/'state');self.cli.diagnostics=self.log

    def invoke(self, events, code=0):
        if not any(isinstance(e, dict) and e.get('event') == 'init' for e in events):
            events = [{'event':'init', 'init':{}}] + events
        process=Mock()
        process.stdin=io.BytesIO()
        raw=b''.join((json.dumps(e,ensure_ascii=False)+'\n').encode() if not isinstance(e,bytes) else e for e in events)
        process.stdout=io.BytesIO(raw)
        process.wait.return_value=code;process.poll.return_value=code
        with scope(task='workflow_repair_plan',role='editor',attempt_id='a'*32,job_id='b'*12,action='workflow_step'),patch('novel_studio.host.subprocess.Popen',return_value=process) as spawn:
            try:
                return self.cli._stream([],self.root,'PRIVATE-PROMPT-CANARY')
            except CoreError as error:
                first=self.log.last_error_id
                second=self.log.error(error,'host_error',task='workflow_repair_plan')
                third=self.log.error(error,action='workflow_step')
                self.assertEqual(first,second);self.assertEqual(first,third)
                raise
            finally:spawn.assert_called_once()

    def test_exit_zero_failure_is_not_success_and_has_terminal_details_and_one_id(self):
        raw={'status':'ERROR','error':'PRIVATE-NOVEL-ERROR-CANARY','structured_output':{'text':'PRIVATE-NOVEL-CANDIDATE'}}
        with self.assertRaises(CoreError) as caught:self.invoke([{'event':'result','result':raw}])
        self.assertEqual(caught.exception.code,'HOST_RESULT_ERROR')
        report=self.log.report();text=json.dumps(report)
        rows=[r for r in report['events'] if r.get('host_result_status')=='ERROR']
        self.assertTrue(rows)
        self.assertTrue(all(r['structured_output_present'] and r['result_event_count']==1 for r in rows))
        self.assertTrue(all(r['attempt_id']=='a'*32 and r['task']=='workflow_repair_plan' for r in rows))
        self.assertEqual(len({r['error_id'] for r in report['events'] if r.get('error_id')}),1)
        self.assertNotIn('unhandled',[r['event'] for r in report['events']])
        for canary in ('PRIVATE-NOVEL','PRIVATE-PROMPT',str(self.root)):self.assertNotIn(canary,text)

    def test_documented_non_success_states_and_missing_result_remain_rejected(self):
        expected={'CANCELED':'HOST_CANCELED','INTERRUPTED':'HOST_INTERRUPTED','WAITING':'HOST_WAITING','INVALID':'HOST_PROTOCOL_ERROR','RUNNING':'HOST_RESULT_ERROR'}
        for state,code in expected.items():
            with self.subTest(state=state),self.assertRaises(CoreError) as error:self.invoke([{'event':'result','result':{'status':state}}])
            self.assertEqual(error.exception.code,code)
        with self.assertRaises(CoreError) as error:self.invoke([b'PRIVATE-STDERR-CANARY\n'])
        self.assertEqual(error.exception.code,'HOST_RESULT_MISSING')

    def test_known_error_codes_patterns_and_unknowns_have_closed_metadata(self):
        for raw,kind,code in [({'code':'UNAVAILABLE','message':'PRIVATE'},'NETWORK','NETWORK_ERROR'),('rpc error: unexpected EOF','NETWORK','NETWORK_ERROR'),({'code':'RESOURCE_EXHAUSTED'},'QUOTA','QUOTA_EXHAUSTED'),('arbitrary novel text','REPORTED_ERROR','HOST_RESULT_ERROR')]:
            with self.subTest(raw=raw),self.assertRaises(CoreError) as error:self.invoke([{'event':'result','result':{'status':'ERROR','error':raw}}])
            self.assertEqual(error.exception.code,code)
            self.assertEqual(error.exception.diagnostic_details['host_failure'],kind)

    def test_malformed_and_duplicate_events_never_become_approved_outputs(self):
        for events in ([[],{'event':'result','result':3}], [{'event':'result','result':{'status':'SUCCESS'}},{'event':'result','result':{'status':'SUCCESS'}}], [{'event':'result','result':{'status':['PRIVATE']}}]):
            with self.subTest(events=events),self.assertRaises(CoreError):self.invoke(events)
        trace=HostTrace();trace.observe({'event':'result','result':{'status':'ERROR','error':{'code':{},'message':[]}}})
        self.assertEqual(trace.summary()['host_error_code'],'OTHER')

    def test_generic_failure_uses_stderr_clue_but_recovered_success_stays_success(self):
        clue = b'Run: attempt 1 failed (stream reading error: unexpected EOF), retrying in 4s PRIVATE-CANARY\n'
        with self.assertRaises(CoreError) as caught:
            self.invoke([clue, {'event':'result','result':{'status':'ERROR','error':'turn failed'}}])
        self.assertEqual(caught.exception.code, 'NETWORK_ERROR')
        self.assertEqual(caught.exception.diagnostic_details['host_failure_source'], 'STDERR_PATTERN')
        self.assertNotIn('PRIVATE-CANARY', json.dumps(self.log.report()))
        result = self.invoke([clue, {'event':'result','result':{'status':'SUCCESS','response':'synthetic success'}}])
        self.assertEqual(result['result']['status'], 'SUCCESS')
        self.assertEqual(self.log.report()['events'][-1]['host_failure'], 'NONE')
        with self.assertRaises(CoreError) as canceled:
            self.invoke([clue, {'event':'result','result':{'status':'CANCELED'}}])
        self.assertEqual(canceled.exception.code, 'HOST_CANCELED')

    def test_host_metadata_does_not_accept_private_strings_disguised_as_enums(self):
        self.log.event('host_event',host_result_status='PRIVATE-NOVEL',host_failure='PRIVATE-NOVEL',host_error_hint='PRIVATE-NOVEL',host_error_code='PRIVATE-NOVEL',last_step_type='PRIVATE-NOVEL',result_event_count='PRIVATE-NOVEL',structured_output_present='PRIVATE-NOVEL')
        self.assertNotIn('PRIVATE-NOVEL',json.dumps(self.log.report()))

    def test_scopes_are_nested_and_thread_local(self):
        def record(role):
            with scope(role=role,task='workflow_plan'):self.log.event('host_event',status='START')
        threads=[threading.Thread(target=record,args=(r,)) for r in ('writer','editor')]
        [t.start() for t in threads];[t.join() for t in threads]
        self.log.event('host_event',status='RESULT')
        rows=self.log.report()['events']
        self.assertEqual({r.get('role') for r in rows if r.get('status')=='START'},{'writer','editor'})
        self.assertNotIn('role',rows[-1])


if __name__=='__main__':unittest.main()
