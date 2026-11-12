"""Large synthetic outlines; no model/network calls and no private books."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from novel_studio.core import Core, CoreError, SafeTree, admin, digest
from novel_studio.context_budget import prepare_context
from novel_studio.prompting import canonical, compile_prompt, MAX_PROMPT_BYTES
from novel_studio.host import OfficialCLI
from novel_studio.workflow_planning import BOOK_TEXT, OUTLINE_TARGET
from novel_studio.diagnostics import DiagnosticLog
from test_outline_first import macro, AdaptiveModels


def large_fixture():
    value=macro(8)
    for key in BOOK_TEXT:
        value[key]=(f'合成{key}全书规则：保留人物知情边界与作者结局。\n'*100)
    for i,v in enumerate(value['volumes'],1):
        v['summary']=(f'第{i}卷的合成规划：钟楼档案与人物关系逐步改变。\n'*60)
    return value


class ContextBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='novel-context-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.core=Core.create(self.root,'八卷合成作品',ROOT)
        with SafeTree(self.core.project) as tree:
            tree.create('Canonical/Canon-Worldbuilding/合成背景.md',('甲'*23000).encode())
        self.outline=self.core.workflow.planning.save(large_fixture())
        self.core.author_commit(self.core.workflow.planning.preview(self.outline['version_id'])['handle'],True)
        self.models=AdaptiveModels(self.core)
        self.pipeline=self.core.workflow.pipeline

    def start(self):return self.pipeline.start(self.pipeline.preview('chapters',1),True)

    def test_original_redundant_context_exceeds_old_limit_but_full_sources_fit(self):
        run=self.start()
        chapter=self.core.workflow.chapters.catalog()[0]
        current=self.pipeline.current_context(run,chapter)
        original=copy.deepcopy(current)
        original.pop('context_layout')
        original['outline_overview']={k:self.outline['value'][k] for k in BOOK_TEXT}
        original['volume_plan']=self.core.workflow.execution.volume(chapter)['plan']
        self.assertGreater(len(admin.encode(original)),120000)
        self.assertEqual(current['approved_outline_text'],self.outline['text'])
        self.assertEqual(current['approved_context'],original['approved_context'])
        for field in ('outline_overview','volume_plan'):
            for key,value in current[field].items():
                if isinstance(value,dict) and 'source_ref' in value:
                    ref=value['source_ref'];raw=current['approved_outline_text'][ref['start']:ref['end']]
                    self.assertEqual(raw,original[field][key])
                    self.assertEqual(digest(raw.encode()),ref['sha256'])
        self.assertTrue(current['context_layout']['deduplicated_fields'])
        compiled=compile_prompt(role='editor',project_id=self.core.project_id,current=current,request='合成大纲规划')
        self.assertGreater(len(compiled.cli_text().encode()),150000)
        self.assertLess(len(compiled.cli_text().encode()),MAX_PROMPT_BYTES)
        with SafeTree(self.core.project) as tree:self.assertEqual(tree.read(OUTLINE_TARGET),self.outline['text'].encode())

    def test_large_macro_workflow_completes_and_canonical_unchanged(self):
        before={r['relative']:r['revision'] for r in self.core.documents() if r['area']=='canonical'}
        run=self.start()
        for _ in range(20):
            result=self.pipeline.advance(run['run_id'])
            if result['status']=='COMPLETED':break
            self.assertEqual(result['status'],'RUNNING')
        self.assertEqual(result['status'],'COMPLETED')
        self.assertEqual([r['task'] for r in self.models.calls],['workflow_plan','workflow_write','workflow_review'])
        self.assertTrue(all(r['current']['approved_outline_text']==self.outline['text'] for r in self.models.calls))
        self.assertEqual(before,{r['relative']:r['revision'] for r in self.core.documents() if r['area']=='canonical'})

    def test_old_preflight_failure_resumes_without_unknown_result_retry_or_duplicate(self):
        run=self.start()
        with patch.object(type(self.pipeline),'current_context',side_effect=CoreError('CONTEXT_LIMIT','合成本机旧容量错误')):
            with self.assertRaises(CoreError):self.pipeline.advance(run['run_id'])
        failed=self.pipeline.get(run['run_id'])
        self.assertEqual(failed['status'],'FAILED')
        self.assertIsNone(failed['active_attempt'])
        self.assertFalse(self.models.calls)
        before=[r['chapter_id'] for r in self.core.workflow.chapters.catalog()]
        resumed=self.pipeline.resume(run['run_id'])
        self.assertEqual(resumed['max_model_tasks'],failed['max_model_tasks'])
        self.pipeline.advance(run['run_id'])
        self.assertEqual(before,[r['chapter_id'] for r in self.core.workflow.chapters.catalog()])
        self.assertEqual(len(self.models.calls),1)

    def test_real_local_ceiling_still_blocks_without_truncation_and_logs_sizes(self):
        original={'approved_outline_text':'中'*(MAX_PROMPT_BYTES//3+1),'approved_always_rules':'始终保留规则'}
        expected=copy.deepcopy(original)
        with self.assertRaises(CoreError) as caught:prepare_context(original)
        self.assertEqual(caught.exception.code,'CONTEXT_LIMIT')
        self.assertEqual(original['approved_outline_text'],expected['approved_outline_text'])
        self.assertEqual(original['approved_always_rules'],expected['approved_always_rules'])
        log=DiagnosticLog(self.root/'local-state')
        log.error(caught.exception)
        row=next(r for r in log.report()['events'] if r.get('error_code')=='CONTEXT_LIMIT')
        self.assertGreater(row['context_bytes'],row['limit_bytes'])
        self.assertNotIn(expected['approved_always_rules'],json.dumps(row,ensure_ascii=False))

    def test_cli_transport_accepts_same_compiler_budget_and_rejects_before_process(self):
        exe=self.root/'agy.exe';exe.write_bytes(b'synthetic executable placeholder; never run')
        cli=OfficialCLI(exe)
        process=Mock()
        process.stdin=io.BytesIO()
        process.stdout=io.BytesIO(b'{"event":"init","init":{"tools":[]}}\n{"event":"result","result":{"status":"SUCCESS"}}\n')
        process.wait.return_value=process.poll.return_value=0
        # This test exercises the real stream parser/budget after a separately
        # tested compatibility boundary. It never fabricates a live receipt.
        with patch.object(cli,'require_compatible',return_value={'model_allowed':True}) as compatible, patch('novel_studio.host.subprocess.Popen',return_value=process) as spawn:
            result=cli.stream([],self.root,'中'*60000)
            self.assertEqual(result['result']['status'],'SUCCESS')
            spawn.assert_called_once()
            compatible.assert_called_once_with(None)
        with patch.object(cli,'require_compatible',return_value={'model_allowed':True}), patch('novel_studio.host.subprocess.Popen') as spawn:
            with self.assertRaises(CoreError) as caught:cli.stream([],self.root,'a'*(MAX_PROMPT_BYTES+1))
            self.assertEqual(caught.exception.code,'CONTEXT_LIMIT')
            spawn.assert_not_called()
        with patch.object(cli,'require_compatible',side_effect=CoreError('CLI_INCOMPATIBLE','synthetic unqualified host')), patch('novel_studio.host.subprocess.Popen') as spawn:
            with self.assertRaises(CoreError) as caught:cli.stream([],self.root,'short synthetic request')
            self.assertEqual(caught.exception.code,'CLI_INCOMPATIBLE')
            spawn.assert_not_called()


if __name__=='__main__':unittest.main()
