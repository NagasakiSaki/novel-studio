"""Exact version gate and old-workflow recovery; synthetic transport only."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from novel_studio.core import Core, CoreError, SafeTree, admin, digest
from novel_studio.diagnostics import DiagnosticLog, sanitized
from novel_studio.host import OfficialCLI, ModelService
from novel_studio.workflow_store import BASE
from test_product13 import ProductModels
from ui2_fixtures import project


class VersionCLI(OfficialCLI):
    observed = '1.5.3'

    def command(self, args, cwd=None):
        assert args == ['--version'], 'No CLI or model calls in this fixture'
        return self.observed

    def require_compatible(self, feature=None):
        # Explicit fake transport fixture, never a live permission assertion.
        self.version()
        return {'features': {'effort': True, 'conversation': True}}


class VersionTests(unittest.TestCase):
    def test_malformed_diagnostic_version_fields_are_ignored(self):
        for value in ([], {}, True, 'PRIVATE_CANARY'):
            clean = sanitized({'format': 'novel-studio-diagnostic-v1', 'event': 'host_event', 'time': 1,
                'cli_compatibility': value, 'cli_version': value})
            self.assertNotIn('cli_version', clean)
            self.assertNotIn('cli_compatibility', clean)

    def test_numeric_versions_are_metadata_and_invalid_outputs_fail_closed(self):
        cli = VersionCLI()
        for version in ('1.5.0', '1.5.3', '1.5.4', '2.0.0'):
            cli.observed = version + '\r\n'
            self.assertEqual(cli.version(), version)
        for version in ('1.5.3-preview', 'v1.5.3', 'PRIVATE'):
            cli.observed = version
            with self.assertRaises(CoreError) as caught:
                cli.version()
            self.assertEqual(caught.exception.code, 'CLI_VERSION_INVALID')
            self.assertIsNone(cli.last_version)

    def test_observed_version_in_error_and_safe_logs_without_raw_stdout(self):
        with tempfile.TemporaryDirectory(prefix='novel-cli-diagnostic-synthetic-') as root:
            log = DiagnosticLog(Path(root) / 'local')
            cli = VersionCLI()
            cli.diagnostics = log
            self.assertEqual(cli.version(), '1.5.3')
            cli.observed = '1.5.4'
            self.assertEqual(cli.version(), '1.5.4')
            cli.observed = 'INVALID'
            with self.assertRaises(CoreError) as caught:
                cli.version()
            log.error(caught.exception, 'host_error')
            errors = [r for r in log.report()['events'] if r['event'] == 'host_error']
            self.assertEqual(errors[-1]['cli_compatibility'], 'INVALID')
            cli.observed = 'PRIVATE_PROMPT_CANARY sk-secret\nC:/private/novel.md'
            with self.assertRaises(CoreError) as caught:
                cli.version()
            log.error(caught.exception, 'host_error')
            report = json.dumps(log.report(), ensure_ascii=False)
            self.assertNotIn('PRIVATE_PROMPT_CANARY', report + str(caught.exception))
            self.assertNotIn('sk-secret', report + str(caught.exception))
            self.assertEqual(log.report()['events'][-1]['cli_compatibility'], 'INVALID')


class OldProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='novel-cli-legacy-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.core, _ = project(Path(self.temp.name), '合成旧版兼容')

    def hashes(self):
        with SafeTree(self.core.project) as tree:
            return {name: digest(tree.read(name)) for folder in ('Canonical', 'Writer-Workspace', 'Editor-Workspace', 'Backups', '.novel-studio') for name in tree.files(folder)}

    def test_old_product_schema_read_is_byte_preserving_then_history_before_write(self):
        path = BASE + '/state.json'
        with SafeTree(self.core.project) as tree:
            raw = tree.read(path)
            state = json.loads(raw)
            state.pop('execution_plans', None)
            state.pop('product_schema', None)
            legacy = admin.encode(state)
            tree.publish(path, legacy, digest(raw))
            tree.create('Writer-Workspace/Drafts/旧章_初稿.md', '# 旧章\n必须原样保留。'.encode())
            tree.create('Canonical/Final-Manuscript/作者旧定稿.md', '# 旧定稿\n不能因更新覆盖。'.encode())
        before = self.hashes()
        reopened = Core(self.core.project, self.core.plugin)
        self.assertEqual(reopened.workflow.store.read()['execution_plans'], {})
        reopened.workflow.chapters.catalog()
        self.assertEqual(before, self.hashes(), 'Reading an old project cannot migrate files')
        reopened.workflow.store.mutate('synthetic_legacy_write', lambda s, t: s.update(synthetic_flag=True))
        with SafeTree(self.core.project) as tree:
            self.assertEqual(tree.read(BASE + '/History/' + digest(legacy) + '.json'), legacy)
        after = self.hashes()
        self.assertTrue(all(after[name] == sha for name, sha in before.items() if name != path))

    def test_unknown_workflow_format_is_rejected_without_any_rewrite(self):
        with SafeTree(self.core.project) as tree:
            name = BASE + '/state.json'
            raw = tree.read(name)
            state = json.loads(raw)
            state['format'] = 'novel-studio-workflow-future'
            tree.publish(name, admin.encode(state), digest(raw))
        before = self.hashes()
        with self.assertRaises(CoreError) as caught:
            Core(self.core.project, self.core.plugin).workflow.store.read()
        self.assertEqual(caught.exception.code, 'WORKFLOW_INVALID')
        self.assertEqual(before, self.hashes())

    def test_future_product_schema_cannot_be_silently_downgraded(self):
        with SafeTree(self.core.project) as tree:
            name = BASE + '/state.json'
            raw = tree.read(name)
            state = json.loads(raw)
            state['product_schema'] = 3
            tree.publish(name, admin.encode(state), digest(raw))
        before = self.hashes()
        for operation in (lambda: self.core.workflow.store.read(), lambda: self.core.workflow.store.mutate('synthetic_future_write', lambda s, t: None)):
            with self.assertRaises(CoreError) as caught:
                operation()
            self.assertEqual(caught.exception.code, 'WORKFLOW_VERSION_UNSUPPORTED')
        self.assertEqual(before, self.hashes())

    def test_saved_revision_failure_reopens_and_resumes_only_failed_step(self):
        core = self.core
        script = ProductModels(core)
        cli = VersionCLI()
        models = ModelService(core, cli)
        actual_task = models.structured_task
        transmitted = []

        def stream(args, cwd, prompt, notify=None, evidence=None):
            settings = json.loads(args[args.index('--settings') + 1])
            self.assertFalse(settings['allowNonWorkspaceAccess'])
            self.assertEqual(set(settings['permissions']['deny']), {'read_file(*)', 'write_file(*)', 'command(*)', 'read_url(*)', 'execute_url(*)', 'mcp(*)'})
            name, instructions, current, schema = pending
            transmitted.append(name)
            result = script.structured_task(name, instructions, current, schema)
            role = 'writer' if name in {'workflow_write', 'workflow_revise'} else 'editor'
            return {'init': {'model': models.selected(role)['model_id']}, 'result': {'status': 'SUCCESS', 'structured_output': result['output'], 'usage': {}}, 'latency_seconds': .001}

        def task(name, instructions, current, schema, **kwargs):
            nonlocal pending
            pending = name, instructions, current, schema
            # Reproduce the old application's exact gate at the failed step.
            if name == 'workflow_revise' and reproduce[0]:
                with patch.object(cli, 'version', side_effect=CoreError('CLI_VERSION_UNSUPPORTED', '合成复现旧门禁')):
                    return actual_task(name, instructions, current, schema, **kwargs)
            return actual_task(name, instructions, current, schema, **kwargs)

        pending, reproduce = None, [True]
        cli.stream = stream
        models.structured_task = task
        pipeline = core.workflow.pipeline
        run = pipeline.start(pipeline.preview('chapters', 1), True)
        with self.assertRaises(CoreError) as caught:
            for _ in range(35):
                pipeline.advance(run['run_id'])
        self.assertEqual(caught.exception.code, 'CLI_VERSION_UNSUPPORTED')
        failed = pipeline.get(run['run_id'])
        self.assertEqual(failed['status'], 'FAILED')
        self.assertNotIn('workflow_revise', transmitted)
        chapter = core.workflow.chapters.catalog()[0]
        self.assertTrue(chapter['revision_plan_current'])
        before = self.hashes()
        core = self.core = Core(core.project, core.plugin)
        core.models = models
        models.core = core
        self.assertEqual(core.workflow.pipeline.get(run['run_id'])['status'], 'FAILED')
        self.assertEqual(before, self.hashes())
        with self.assertRaises(CoreError) as caught:
            core.workflow.pipeline.resume(run['run_id'])
        self.assertEqual(caught.exception.code, 'RESULT_UNKNOWN')
        self.assertEqual(before, self.hashes())
        reproduce[0] = False
        core.workflow.pipeline.resume(run['run_id'], retry_confirmed=True)
        for _ in range(35):
            result = core.workflow.pipeline.advance(run['run_id'])
            if result['status'] == 'COMPLETED':
                break
            self.assertEqual(result['status'], 'RUNNING')
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(transmitted, ['workflow_plan', 'workflow_write', 'workflow_review', 'workflow_repair_plan', 'workflow_revise', 'workflow_review'])
        updated = core.workflow.chapters.catalog()[0]
        self.assertEqual((updated['chapter_id'], updated['number']), (chapter['chapter_id'], chapter['number']))
        self.assertEqual(updated['revision_plans'], chapter['revision_plans'])
        self.assertIsNone(updated['revision_plan_current'])
        after = self.hashes()
        # Only current-state pointers change during an authorized new step;
        # manuscripts, outlines, receipts and historical results stay immutable.
        mutable = {BASE + '/state.json', '.novel-studio/Desktop.json', '.novel-studio/GUI-State.json', '.novel-studio/Creative/settings.json'}
        changed = [name for name, sha in before.items() if after.get(name) != sha and name not in mutable]
        self.assertEqual(changed, [])
        self.assertFalse(updated.get('canonical'))
        preview = core.workflow.chapters.preview([updated['chapter_id']])
        with self.assertRaises(CoreError):
            core.author_commit(preview['handle'], False)
        self.assertFalse(core.workflow.chapters.catalog()[0].get('canonical'))


if __name__ == '__main__':
    unittest.main()
