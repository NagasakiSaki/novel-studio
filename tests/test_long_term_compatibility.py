"""Capability decisions, cached receipts, safe degradation and data rollback."""
import json
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from novel_studio.core import Core, CoreError, SafeTree, admin, digest, READ, WRITE
from novel_studio.host import OfficialCLI
from novel_studio.compatibility import CompatibilityManager, REQUIRED_FLAGS, CORE_CHECKS, evaluate
from novel_studio.compatibility_store import ReceiptStore, protect
from novel_studio.compatibility_probe import run
from novel_studio.workflow_store import BASE
from novel_studio import schema_migration
from ui2_fixtures import project


class FakeLocalCLI(OfficialCLI):
    def __init__(self, path):
        super().__init__(path)
        self.observed = '1.5.3'
        self.flags = list(REQUIRED_FLAGS) + ['--effort', '--conversation']
        self.helps = self.probes = 0

    def command(self, args, cwd=None):
        if args == ['--version']:
            return self.observed
        raise AssertionError('No unexpected command/model request')

    def help_text(self):
        self.helps += 1
        return '\n'.join('  ' + flag + '   documented flag' for flag in self.flags)

    def installed_plugin(self):
        return ROOT

    def _stream(self, *args, **kwargs):
        self.probes += 1
        raise AssertionError('A compatibility inspection cannot call a model')


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='novel-compat-capability-synthetic-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        exe = self.root / 'agy.exe'
        exe.write_bytes(b'SYNTHETIC, NEVER EXECUTED')
        self.cli = FakeLocalCLI(exe)
        self.manager = CompatibilityManager(self.cli, self.root / 'receipts')
        self.cli._compatibility = self.manager

    def prove(self):
        report = self.manager.inspect()
        return self.manager.certify(report['binding'], {k: True for k in (*CORE_CHECKS, 'session')}, 'synthetic-proof')

    def test_known_version_without_actual_permissions_is_not_automatically_approved(self):
        report = self.manager.inspect()
        self.assertEqual(report['status'], 'INCOMPATIBLE')
        with self.assertRaises(CoreError):
            self.cli.stream([], self.root, 'DO_NOT_SEND')
        self.assertEqual(self.cli.probes, 0)

    def test_unknown_patch_can_pass_by_evidence_and_is_cached_across_managers(self):
        self.cli.observed = '1.5.99'
        self.assertEqual(self.prove()['status'], 'FULL')
        helps = self.cli.helps
        for _ in range(3):
            self.manager.require()
        self.assertEqual(self.cli.helps, helps)
        reopened = CompatibilityManager(self.cli, self.root / 'receipts')
        self.assertTrue(reopened.require()['model_allowed'])
        self.assertEqual(self.cli.helps, helps)

    def test_unknown_major_requires_additional_review_even_with_old_shape_proof(self):
        self.cli.observed = '2.0.0'
        report = self.prove()
        self.assertIn('MAJOR_REVIEW_REQUIRED', report['reasons'])
        self.assertFalse(report['model_allowed'])

    def test_missing_required_flag_cannot_be_repaired_by_a_pass_receipt(self):
        self.cli.flags.remove('--settings')
        report = self.prove()
        self.assertIn('--settings', report['missing_flags'])
        self.assertFalse(report['model_allowed'])

    def test_missing_optional_flags_gives_partial_compatibility(self):
        self.cli.flags = list(REQUIRED_FLAGS)
        report = self.prove()
        self.assertEqual(report['status'], 'PARTIAL')
        self.assertTrue(report['model_allowed'])
        self.assertFalse(report['features']['conversation'])
        with self.assertRaises(CoreError):
            self.manager.require('conversation')

    def test_version_binary_contract_and_help_change_invalidate_old_proof(self):
        self.prove()
        self.cli.observed = '1.5.4'
        self.assertFalse(self.manager.inspect()['model_allowed'])
        self.prove()
        self.cli.path.write_bytes(b'CHANGED SYNTHETIC EXECUTABLE')
        self.assertFalse(self.manager.inspect()['model_allowed'])
        self.prove()
        with patch('novel_studio.compatibility.contract', return_value='new-contract'):
            self.assertFalse(self.manager.inspect()['model_allowed'])
        self.cli.flags.remove('--effort')
        self.assertFalse(self.manager.inspect(True)['model_allowed'])

    def test_runtime_protocol_failure_is_sticky_but_network_failure_is_not(self):
        self.prove()
        self.manager.invalidate('NETWORK_ERROR')
        self.assertTrue(self.manager.inspect()['model_allowed'])
        self.manager.invalidate('INVALID_ARTIFACT')
        self.assertIn('RUNTIME_CONTRACT_FAILED', self.manager.inspect()['reasons'])
        self.assertFalse(self.manager.inspect(True)['model_allowed'])

    def test_permissions_failure_and_confirmed_problem_version_are_blocked(self):
        report = self.manager.inspect()
        self.manager.certify(report['binding'], {k: k != 'deny_workspace' for k in (*CORE_CHECKS, 'session')}, 'synthetic-failed-proof')
        self.assertFalse(self.manager.inspect()['model_allowed'])
        record = {'flags': self.cli.flags, 'proof': {k: True for k in (*CORE_CHECKS, 'session')}}
        self.assertIn('KNOWN_ISSUE', evaluate(record, '1.5.3', {'1.5.3': 'SYNTHETIC ISSUE ONLY'})['reasons'])

    def test_start_failure_missing_file_and_invalid_version_never_use_old_pass(self):
        self.prove()
        with patch.object(self.cli, 'command', side_effect=OSError('synthetic start failure')):
            self.assertFalse(self.manager.inspect()['model_allowed'])
        self.cli.observed = 'PRIVATE_OUTPUT_CANARY'
        report = self.manager.inspect()
        self.assertFalse(report['model_allowed'])
        self.assertNotIn('PRIVATE_OUTPUT_CANARY', json.dumps(report))
        self.cli.path.unlink()
        self.assertFalse(self.manager.inspect()['model_allowed'])

    def test_corrupt_receipt_never_grants_permission_and_keeps_original_bytes(self):
        self.prove()
        path = self.root / 'receipts/receipts.bin'
        path.write_bytes(b'UNPROTECTED_PASS_RECEIPT')
        self.assertFalse(self.manager.inspect()['model_allowed'])
        self.assertEqual(path.read_bytes(), b'UNPROTECTED_PASS_RECEIPT')

    def test_approval_required_before_any_model_probe(self):
        with self.assertRaises(CoreError) as caught:
            run(self.cli, self.root)
        self.assertEqual(caught.exception.code, 'COMPAT_APPROVAL_REQUIRED')
        self.assertEqual(self.cli.probes, 0)

    def test_fixed_probe_harness_four_synthetic_calls_and_independent_sessions(self):
        self.manager.inspect()
        markers, calls = {}, []
        def raw(args, cwd, prompt, notify=None, evidence=None):
            role = args[args.index('--agent') + 1].removeprefix('novel-')
            resumed = '--conversation' in args
            cid = ('a' if role == 'writer' else 'b') * 32
            if not resumed:
                markers[role] = re.search(r'Remember and return marker: ([a-f0-9]+)', prompt)[1]
            events = [{'event': 'init'}]
            for name in ('write_to_file', 'view_file'):
                events.append({'event': 'step_update', 'step_update': {'state': 'ERROR', 'step_type': 'tool', 'tool_info': {'name': name, 'error': 'permission check failed: user-configured deny'}}})
            if resumed:
                snapshot = re.search(r'allowed synthetic snapshot using view_file: (.*?)\. Return', prompt)[1]
                events.append({'event': 'step_update', 'step_update': {'state': 'DONE', 'tool_info': {'name': 'view_file', 'parameters': {'AbsolutePath': snapshot}}}})
            events.append({'event': 'result'})
            for event in events:
                evidence(event)
            calls.append((role, resumed))
            return {'init': {'agent': 'novel-' + role}, 'result': {'conversation_id': cid, 'num_turns': 2 if resumed else 1, 'structured_output': {'role': role, 'marker': markers[role]}}}
        with patch.object(self.cli, '_stream', side_effect=raw):
            result = run(self.cli, self.root, confirmed=True)
        self.assertEqual(result['status'], 'FULL')
        self.assertEqual(calls, [('writer', False), ('editor', False), ('writer', True), ('editor', True)])


class DataCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='novel-compat-data-synthetic-')
        self.addCleanup(self.tmp.cleanup)
        self.core, _ = project(Path(self.tmp.name), '合成旧小说兼容')
        with SafeTree(self.core.project) as tree:
            name = BASE + '/state.json'
            raw = tree.read(name)
            legacy = json.loads(raw)
            legacy.pop('product_schema', None)
            self.old = admin.encode(legacy)
            tree.publish(name, self.old, digest(raw))

    def test_ordinary_write_does_not_silently_migrate_old_schema(self):
        self.core.workflow.store.read()
        with SafeTree(self.core.project) as tree:
            self.assertEqual(tree.read(BASE + '/state.json'), self.old)
        self.core.workflow.store.mutate('synthetic_ordinary_operation', lambda s, t: s.update(synthetic=True))
        self.assertNotIn('product_schema', self.core.workflow.store.read())

    def test_explicit_migration_approval_backup_and_exact_byte_restore(self):
        preview = schema_migration.preview(self.core)
        with self.assertRaises(CoreError):
            self.core.author_commit(preview['handle'], False)
        preview = schema_migration.preview(self.core)
        value = self.core.author_commit(preview['handle'], True)
        self.assertEqual(self.core.workflow.store.read()['product_schema'], 2)
        with SafeTree(self.core.project) as tree:
            self.assertEqual(tree.read(schema_migration.BACKUPS + '/' + value['transaction_id'] + '/before.json'), self.old)
        restore = schema_migration.preview(self.core, value['transaction_id'])
        self.core.author_commit(restore['handle'], True)
        with SafeTree(self.core.project) as tree:
            self.assertEqual(tree.read(BASE + '/state.json'), self.old)

    def test_migration_and_restore_cannot_overwrite_progress_after_preview(self):
        preview = schema_migration.preview(self.core)
        self.core.workflow.store.mutate('synthetic_change', lambda s, t: s.update(synthetic=True))
        with self.assertRaises(CoreError):
            self.core.author_commit(preview['handle'], True)
        value = self.core.author_commit(schema_migration.preview(self.core)['handle'], True)
        self.core.workflow.store.mutate('synthetic_new_progress', lambda s, t: s.update(new_progress=True))
        with self.assertRaises(CoreError):
            schema_migration.preview(self.core, value['transaction_id'])

    def test_unavailable_cli_does_not_disable_local_draft_material_or_export(self):
        cli = OfficialCLI(Path(self.tmp.name) / 'missing/agy.exe')
        self.assertFalse(cli.compatibility.inspect()['model_allowed'])
        core = self.core
        cap = core.bind('writer', READ | WRITE)
        created = core.dispatch(cap, 'create_draft', {'text': '# 合成本地草稿\n没有模型连接也能保存。'}, 'synthetic-local-create')
        text = core.document(created['document_id'])
        self.assertIn('合成本地草稿', text['text'])
        core.dispatch(cap, 'update_draft', {'document_id': created['document_id'], 'expected_revision': text['revision'], 'text': text['text'] + '\n本地修订。'}, 'synthetic-local-update')
        self.assertTrue(core.documents())
        from novel_studio.exporting import ExportService, DEFAULTS
        exports = ExportService(core)
        selected = [r for r in exports.catalog() if r['document_id'] == created['document_id']]
        preview = exports.preview(selected, {**DEFAULTS, 'title': '合成本地作品'}, 'txt')
        target = Path(self.tmp.name) / '合成本地导出.txt'
        exports.export(preview['handle'], target, True)
        self.assertIn('本地修订', target.read_text(encoding='utf-8-sig'))
        source = Path(self.tmp.name) / '合成本地资料.md'
        source.write_text('# 世界观\n无模型也能导入。', encoding='utf-8')
        prepared = core.materials.prepare(source, 'world')
        material = core.materials.preview(prepared, [], 'retrieval', True)
        core.author_commit(material['handle'], True)


if __name__ == '__main__':
    unittest.main()
