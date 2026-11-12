"""Native-capacity policy with synthetic text and in-memory official SDK I/O."""
import json
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests'), str(ROOT)]
from novel_studio.core import Core, CoreError, SafeTree, READ, WRITE
from novel_studio.prompting import compile_prompt, MAX_PROMPT_BYTES
from novel_studio.context_budget import prepare_context
from novel_studio.model_limits import apply_limits
from novel_studio.providers import NativeTurn, parameter_config, official_client
from novel_studio.workflow_requests import shared_context, execute, load_result
from novel_studio.api_service import ProviderRegistry
from novel_studio.host import ModelService
from gui.service import LocalStore
from test_official_providers import MemoryVault, SDK, response


class MaximumPolicyTests(unittest.TestCase):
    def test_official_maximum_is_default_and_catalog_can_exceed_old_128k(self):
        self.assertEqual(parameter_config('openai', {'model_id':'gpt-6.1-sol'}, {})['max_output_tokens'],128000)
        self.assertEqual(parameter_config('gemini', {'model_id':'gemini-3.8-flash'}, {})['max_output_tokens'],65536)
        for provider in ('openai','anthropic','gemini'):
            self.assertEqual(parameter_config(provider, {'model_id':'synthetic'}, {'max_output_tokens':262144})['max_output_tokens'],262144)
            self.assertEqual(parameter_config(provider, {'model_id':'synthetic','max_output_tokens':200000}, {'max_output_tokens':262144})['max_output_tokens'],200000)
            with self.assertRaises(CoreError):
                parameter_config(provider, {'model_id':'synthetic','max_output_tokens':262145}, {'max_output_tokens':262144})

    def test_unknown_ids_host_and_invalid_metadata_are_never_given_fake_capacity(self):
        for provider,selected in (('antigravity','gemini-3.8-flash-high'),('gemini','gemini-3.8-flash-unverified'),('openai','unknown')):
            self.assertIsNone(apply_limits(provider,selected,{})['max_output_tokens'])
        for value in (True, -1, '65536', 0):
            self.assertIsNone(apply_limits('anthropic','synthetic',{'max_output_tokens':value})['max_output_tokens'])
        with self.assertRaises(CoreError) as error:parameter_config('anthropic',{'model_id':'synthetic'}, {})
        self.assertEqual(error.exception.code,'CAPABILITY_UNKNOWN')
        self.assertNotIn('max_output_tokens', parameter_config('openai',{'model_id':'unknown'}, {}))
        self.assertEqual(apply_limits('openai','gpt-6.1-sol',{'max_output_tokens':262144})['max_output_tokens'],262144)

    @unittest.skipUnless(SDK, 'Audited SDK environment unavailable')
    def test_large_requests_and_maximum_output_reach_all_three_official_sdk_transports(self):
        import httpx
        import httpx2
        text='合成中文上下文。'*75000
        compiled=compile_prompt(role='writer',project_id='synthetic',current={'full_text':text},request='短篇幅即可，不要求写满输出上限。')
        self.assertGreater(len(compiled.cli_text().encode()),1_000_000)
        for provider in ('openai','anthropic','gemini'):
            with self.subTest(provider=provider):
                module=httpx if provider=='gemini' else httpx2
                seen=[]
                def transport(request):
                    seen.append(json.loads(request.content))
                    return module.Response(200,json=response(provider))
                with official_client(provider,'SYNTHETIC-NEVER-A-REAL-KEY',transport=module.MockTransport(transport)) as client:
                    turn=NativeTurn(provider,client,compiled,{'model_id':'synthetic'},{'max_output_tokens':262144})
                    turn.request()
                    self.assertEqual(turn.attempts,1)
                body=seen[0]
                maximum=body['max_output_tokens'] if provider=='openai' else body['max_tokens'] if provider=='anthropic' else body['generation_config']['max_output_tokens']
                self.assertEqual(maximum,262144)
                self.assertIn(text,json.dumps(body,ensure_ascii=False))
                self.assertEqual(len(seen),1)


class LargeAuthorContextTests(unittest.TestCase):
    def test_large_plain_outline_and_task_result_survive_save_approval_and_replay(self):
        with tempfile.TemporaryDirectory(prefix='novel-max-outline-synthetic-') as folder:
            core=Core.create(Path(folder),'大型大纲合成作品',ROOT)
            text='# 作品大纲\n'+'人物寻找钟楼档案，结局由作者裁决。'*18000
            plan=core.workflow.planning.save(text)
            core.author_commit(core.workflow.planning.preview(plan['version_id'])['handle'],True)
            self.assertEqual(core.workflow.planning.approved()['text'],text)
            class Synthetic:
                calls=0
                def selected(self,role):return {'provider':'antigravity','model_id':'synthetic'}
                def structured_task(self,*args,**kwargs):
                    self.calls+=1
                    return {'output':{'text':text},'model':{'provider':'antigravity','requested_model':'synthetic','actual_model':'synthetic'}}
            core.models=Synthetic()
            attempt=uuid.uuid4().hex
            result=execute(core,'workflow_write',{}, {},'合成保存测试',attempt)
            self.assertGreater(len(json.dumps(result,ensure_ascii=False).encode()),800000)
            self.assertEqual(load_result(core,attempt,'workflow_write'),result)
            self.assertEqual(execute(core,'workflow_write',{}, {},'合成保存测试',attempt),result)
            self.assertEqual(core.models.calls,1)

    def test_large_style_source_has_no_separate_100kb_model_gate(self):
        from novel_studio.style_analysis import StyleAnalysisService
        with tempfile.TemporaryDirectory(prefix='novel-max-style-synthetic-') as folder:
            core=Core.create(Path(folder),'大型文风合成作品',ROOT)
            draft=core.dispatch(core.bind('writer',READ|WRITE),'create_draft',{'text':'# 合成文风参考\n'+'雨水落上窗沿。他合上档案，没有说话。\n'*7000},'synthetic-style')
            service=StyleAnalysisService(core)
            selection=service.select('d_'+draft['document_id'])
            analysis=service.local_analysis([selection])
            class Synthetic:
                def structured_task(self,*args,**kwargs):return {'output':analysis,'model':{'provider':'synthetic'}}
            core.models=Synthetic()
            self.assertGreater(len(selection['text'].encode()),300000)
            service.analyze([selection],model=True)

    def test_full_presets_and_always_rules_above_old_caps_reach_both_roles_unchanged(self):
        with tempfile.TemporaryDirectory(prefix='novel-max-synthetic-') as folder:
            base=Path(folder);core=Core.create(base,'大型资料合成作品',ROOT)
            full='# 完整预设\n'+'背景原文保留。'*12000
            rules='# 全局规则\n'+'不得改写作者批准的大纲。'*6000
            for name,text,mode in (('完整预设.md',full,'full'),('常驻规则.md',rules,'retrieval')):
                source=base/name;source.write_bytes(text.encode())
                prepared=core.materials.prepare(source,'global_preset')
                ids=[s['id'] for s in prepared.parsed['sections']] if mode=='retrieval' else []
                plan=core.materials.preview(prepared,ids,mode,True)
                core.author_commit(plan['handle'],True)
            with SafeTree(core.project) as tree:tree.create('Canonical/Canon-Worldbuilding/背景.md',('基础设定。'*7000).encode())
            core.snapshot()
            draft=core.dispatch(core.bind('writer',READ|WRITE),'create_draft',{'text':'中文草稿。'*50000},'synthetic-large-draft')
            self.assertGreater(len(core.document(draft['document_id'])['text'].encode()),600000)
            core.handoff(draft['document_id'])
            for role in ('writer','editor'):
                context,_=core.materials.context(role,'背景')
                self.assertIn(full,context)
                self.assertIn(rules,context)
            current=shared_context(core,'背景')
            current['approved_outline_text']='正式合成大纲。'*60000
            ready,metrics=prepare_context(current)
            self.assertGreater(metrics['context_bytes'],1_000_000)
            self.assertEqual(ready['approved_outline_text'],current['approved_outline_text'])
            self.assertIn(rules,ready['approved_always_rules'])
            self.assertLess(len(compile_prompt(role='writer',project_id=core.project_id,current=ready).cli_text().encode()),MAX_PROMPT_BYTES)


class MaximumUITests(unittest.TestCase):
    def test_gui_automatic_maximum_manual_reset_and_host_limitation_are_explicit(self):
        from PySide6.QtWidgets import QApplication
        from novel_studio.desktop.provider_ui import ProviderDialog
        from novel_studio.desktop.lifecycle import dispose_windows
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(prefix='novel-max-ui-synthetic-') as folder:
            base=Path(folder);core=Core.create(base,'容量界面合成作品',ROOT)
            model=ModelService(core)
            store=LocalStore(base/'state');registry=ProviderRegistry(store,MemoryVault())
            store.save({'api_catalogs':{'anthropic':[{'model_id':'synthetic','max_output_tokens':262144}]}})
            model.attach_api(registry)
            registry.save_key('writer','anthropic','SYNTHETIC')
            dialog=ProviderDialog(registry,core)
            try:
                self.assertFalse(dialog.maximum.isEnabled())
                self.assertIn('没有公开',dialog.capability.text())
                dialog.provider.setCurrentIndex(dialog.provider.findData('anthropic'))
                dialog.model.setCurrentText('synthetic')
                self.assertEqual(dialog.maximum.value(),0)
                self.assertIn('262144',dialog.capability.text())
                dialog.maximum.setValue(200000)
                self.assertEqual(dialog.maximum.value(),200000)
                dialog.use_maximum.click()
                self.assertIsNone(dialog.config()['max_output_tokens'])
                dialog.consent.setChecked(True);dialog.apply_config()
                self.assertEqual(model.selected('writer')['max_output_tokens'],262144)
                self.assertEqual(model.selected('editor')['provider'],'antigravity')
            finally:
                dialog.reject();dialog.deleteLater();app.processEvents()
                dispose_windows(app)


if __name__=='__main__':unittest.main()
