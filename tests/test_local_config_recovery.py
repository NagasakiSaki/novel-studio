"""B07 synthetic application state only; no real profile or model requests."""
from pathlib import Path
import json
import hashlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from gui.service import LocalStore, LocalConfigIssue, ConfigStartupCancelled, SafeTree, admin, Controller


class LocalConfigRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='b07-synthetic-state-')
        self.addCleanup(self.temp.cleanup)
        self.store = LocalStore(Path(self.temp.name) / 'state')
        self.path = self.store.root / 'center.json'

    def test_truncated_json_is_classified_without_losing_original(self):
        raw = b'{"layout":'
        self.path.write_bytes(raw)
        try:
            self.store.load()
        except ValueError as error:
            self.assertEqual(getattr(error, 'kind', None), 'syntax')
        else:
            self.fail('invalid configuration was silently accepted')
        self.assertEqual(self.path.read_bytes(), raw)

    def inventory(self):
        return {p.relative_to(self.store.root).as_posix():p.read_bytes()
                for p in self.store.root.rglob('*') if p.is_file()}

    def test_corrupt_shapes_are_classified(self):
        cases = [(b'', 'empty'), (b'[]', 'root'), (b'"text"', 'root'),
                 (b'{"layout":[]}', 'layout'), (b'{"layout":{"width":900}}', 'layout'),
                 (b'{"layout":{"ui_version":2}}', 'layout'), (b'{"layout":{"page":[]}}', 'layout'),
                 (b'{"project":[]}', 'fields'), (b'{"recent_projects":[{}]}', 'fields'),
                 (b'{"api_credentials":{"writer":[]}}', 'fields'), (b'{"a":1,"a":2}', 'fields')]
        for raw, kind in cases:
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(LocalConfigIssue) as caught:
                    self.store.load()
                self.assertEqual(caught.exception.kind, kind)
                self.assertFalse(caught.exception.can_reset)
                self.assertEqual(self.path.read_bytes(), raw)

    def test_old_valid_state_is_compatible_and_not_rewritten_on_read(self):
        states = [{}, {'project': None}, {'layout': {}}, {'layout': {'theme': 'dark'}},
                  {'layout': {'theme': 'light', 'width': 1100, 'height': 750, 'docks': '', 'tab': 1}},
                  {'project': 'synthetic-book', 'recent_projects': ['one'], 'project_titles': {'one': 'title'}, 'extension': {'keep': True}},
                  {'api_consents': {'project:writer:official': 'synthetic-key-reference'},
                   'api_credentials': {'writer': {'official': 'synthetic-key-reference'}}}]
        for state in states:
            with self.subTest(state=state):
                raw = admin.encode(state)
                self.path.write_bytes(raw)
                self.assertEqual(self.store.load(), state)
                self.assertEqual(self.path.read_bytes(), raw)

    def test_future_versions_are_not_downgraded_or_backed_up_as_corrupt(self):
        for state in ({'format': 'future-center-v9'}, {'version': 999}, {'schema_version': 2}, {'layout': {'ui_version': 99}}):
            with self.subTest(state=state):
                raw = admin.encode(state)
                self.path.write_bytes(raw)
                with self.assertRaises(ConfigStartupCancelled):
                    self.store.prepare(lambda issue:'defaults')
                self.assertEqual(self.path.read_bytes(), raw)
                self.assertFalse((self.store.root / 'Config-Recovery').exists())
                with self.assertRaises(LocalConfigIssue):
                    self.store.save({'layout': {}})

    def test_permission_io_and_unsafe_reads_cannot_reset(self):
        raw = b'{"layout":'
        self.path.write_bytes(raw)
        original = SafeTree.read
        for error, kind in [(PermissionError('denied'), 'permission'), (OSError('disk'), 'io'), (ValueError('link'), 'unsafe')]:
            with self.subTest(kind=kind):
                def denied(tree, name):
                    if name == 'center.json':
                        raise error
                    return original(tree, name)
                with patch.object(SafeTree, 'read', denied):
                    with self.assertRaises(LocalConfigIssue) as caught:
                        self.store.load()
                    self.assertEqual(caught.exception.kind, kind)
                    self.assertIsNone(caught.exception.sha256)
                    with self.assertRaises(ConfigStartupCancelled):
                        self.store.prepare(lambda issue:'defaults')
                self.assertEqual(self.path.read_bytes(), raw)
                self.assertFalse((self.store.root / 'Config-Recovery').exists())

    def test_verified_backup_idempotence_reset_and_restart(self):
        raw = b'{"layout":'
        self.path.write_bytes(raw)
        for attempt in range(2):
            with self.assertRaises(ConfigStartupCancelled):
                self.store.prepare(lambda issue:'exit')
            current = self.inventory()
            if attempt:
                self.assertEqual(previous, current)
            previous = current
        backups = list((self.store.root / 'Config-Recovery').glob('*.bin'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), raw)
        self.assertTrue(backups[0].name.startswith(hashlib.sha256(raw).hexdigest()))
        self.store.prepare(lambda issue:'defaults')
        self.assertEqual(LocalStore.open(self.store.root).load(), {'layout': {}})
        self.assertEqual(backups[0].read_bytes(), raw)
        self.assertTrue(any(r['phase'] == 'done' for r in self.store.recovery_records()))

    def test_backup_failure_never_resets_and_retry_preserves_partial_backup(self):
        raw = b'{"layout":'
        self.path.write_bytes(raw)
        original = SafeTree.create
        def partial(tree, name, payload):
            if name.endswith('.bin'):
                original(tree, name, b'partial')
                raise OSError('ENOSPC')
            return original(tree, name, payload)
        issues = []
        def choose(issue):
            issues.append(issue)
            return 'defaults'
        with patch.object(SafeTree, 'create', partial), self.assertRaises(ConfigStartupCancelled):
            self.store.prepare(choose)
        self.assertFalse(issues[0].can_reset)
        self.assertTrue(issues[0].backup_failed)
        self.assertEqual(self.path.read_bytes(), raw)
        self.store.prepare(lambda issue:'defaults')
        contents = [p.read_bytes() for p in (self.store.root / 'Config-Recovery').glob('*.bin')]
        self.assertIn(b'partial', contents)
        self.assertIn(raw, contents)

    def test_default_generation_failure_preserves_source_and_backup(self):
        raw = b'{"layout":'
        self.path.write_bytes(raw)
        original = admin.encode
        def invalid(value):
            if value == {'layout': {}}:
                raise ValueError('generation failed')
            return original(value)
        choices = iter(['defaults', 'exit'])
        with patch.object(admin, 'encode', invalid), self.assertRaises(ConfigStartupCancelled):
            self.store.prepare(lambda issue:next(choices))
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(next((self.store.root / 'Config-Recovery').glob('*.bin')).read_bytes(), raw)

    def test_atomic_default_publication_failure_keeps_original(self):
        raw = b'{"layout":'
        self.path.write_bytes(raw)
        original = SafeTree.publish
        def fail(tree, name, payload, expected):
            if name == 'center.json':
                raise OSError('before replace')
            return original(tree, name, payload, expected)
        choices = iter(['defaults', 'exit'])
        with patch.object(SafeTree, 'publish', fail), self.assertRaises(ConfigStartupCancelled):
            self.store.prepare(lambda issue:next(choices))
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(next((self.store.root / 'Config-Recovery').glob('*.bin')).read_bytes(), raw)
        self.store.prepare(lambda issue:'defaults')
        self.assertEqual(self.store.load(), {'layout': {}})

    def test_backup_to_reset_race_preserves_other_writers_configuration(self):
        self.path.write_bytes(b'{"layout":')
        other = b'{"layout":{},"extension":"other writer"}'
        def choose(issue):
            if issue.kind == 'syntax':
                self.path.write_bytes(other)
                return 'defaults'
            return 'exit'
        with self.assertRaises(ConfigStartupCancelled):
            self.store.prepare(choose)
        self.assertEqual(self.path.read_bytes(), other)

    def test_retry_uses_fresh_file_and_never_auto_opens_project_after_issue(self):
        self.path.write_bytes(b'{"layout":')
        project = str(Path(self.temp.name) / 'must-not-be-opened')
        def choose(issue):
            self.path.write_bytes(admin.encode({'project': project}))
            return 'retry'
        with patch.object(Controller, 'open_project', side_effect=AssertionError('private project opened')):
            controller = Controller(Path(self.temp.name), self.store.root, config_recovery=choose)
        self.assertIsNone(controller.project)
        self.assertTrue(controller.store.safe_start)

    def test_state_root_permission_retry_also_disables_automatic_project_open(self):
        self.path.write_bytes(admin.encode({'project': str(Path(self.temp.name) / 'must-not-open')}))
        original = LocalStore.__init__
        attempts = []
        def opening(store, root):
            attempts.append(root)
            if len(attempts) == 1:
                raise PermissionError('synthetic root denied')
            original(store, root)
        with patch.object(LocalStore, '__init__', opening), patch.object(Controller, 'open_project', side_effect=AssertionError('project opened')):
            controller = Controller(Path(self.temp.name), self.store.root, config_recovery=lambda issue:'retry')
        self.assertTrue(controller.store.safe_start)
        self.assertIsNone(controller.project)

    def test_diagnostics_do_not_include_configuration_values(self):
        secret = 'SYNTHETIC-API-KEY-DO-NOT-PRINT'
        raw = ('{"api_key":"' + secret + '","project":"D:/private/fiction", "layout":').encode()
        self.path.write_bytes(raw)
        seen = []
        with self.assertRaises(ConfigStartupCancelled):
            self.store.prepare(lambda issue:seen.append(issue.diagnostic()) or 'exit')
        text = json.dumps(seen)
        self.assertNotIn(secret, text)
        self.assertNotIn('D:/private/fiction', text)
        self.assertEqual(next((self.store.root / 'Config-Recovery').glob('*.bin')).read_bytes(), raw)

    def test_every_reset_preserves_prior_verified_backup(self):
        for raw in (b'{"layout":', b'{"project":'):
            self.path.write_bytes(raw)
            self.store.prepare(lambda issue:'defaults')
        backups = list((self.store.root / 'Config-Recovery').glob('*.bin'))
        self.assertEqual({p.read_bytes() for p in backups}, {b'{"layout":', b'{"project":'})

    def test_torn_backup_receipt_is_preserved_and_retry_reuses_verified_bytes(self):
        raw = b'{"layout":'
        self.path.write_bytes(raw)
        original = SafeTree.create
        def partial(tree, name, payload):
            if name.startswith('Config-Recovery/') and name.endswith('.json'):
                original(tree, name, b'{"format":')
                raise OSError('receipt interrupted')
            return original(tree, name, payload)
        with patch.object(SafeTree, 'create', partial), self.assertRaises(ConfigStartupCancelled):
            self.store.prepare(lambda issue:'defaults')
        self.assertEqual(self.path.read_bytes(), raw)
        torn = next((self.store.root / 'Config-Recovery').glob('*.json'))
        self.store.prepare(lambda issue:'defaults')
        self.assertEqual(torn.read_bytes(), b'{"format":')
        self.assertEqual(len(list((self.store.root / 'Config-Recovery').glob('*.bin'))), 1)
        self.assertEqual(self.store.load(), {'layout': {}})

    def test_actual_atomic_stage_write_failure_preserves_original(self):
        raw = b'{"layout":'
        self.path.write_bytes(raw)
        original = SafeTree.create
        def fail(tree, name, payload):
            if name.startswith('.ns-') and name.endswith('.tmp'):
                original(tree, name, b'partial stage')
                raise OSError('disk full during stage')
            return original(tree, name, payload)
        choices = iter(['defaults', 'exit'])
        with patch.object(SafeTree, 'create', fail), self.assertRaises(ConfigStartupCancelled):
            self.store.prepare(lambda issue:next(choices))
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(next((self.store.root / 'Config-Recovery').glob('*.bin')).read_bytes(), raw)
        self.assertEqual(next(self.store.root.glob('.ns-*.tmp')).read_bytes(), b'partial stage')
        self.store.prepare(lambda issue:'defaults')
        self.assertEqual(self.store.load(), {'layout': {}})

    def test_post_publication_receipt_failure_keeps_valid_defaults_and_backup(self):
        raw = b'{"layout":'
        self.path.write_bytes(raw)
        original = SafeTree.create
        def fail(tree, name, payload):
            if name.endswith('-done.json'):
                raise OSError('audit interrupted after publication')
            return original(tree, name, payload)
        choices = iter(['defaults', 'exit'])
        with patch.object(SafeTree, 'create', fail), self.assertRaises(ConfigStartupCancelled):
            self.store.prepare(lambda issue:next(choices))
        self.assertEqual(self.store.load(), {'layout': {}})
        self.assertEqual(next((self.store.root / 'Config-Recovery').glob('*.bin')).read_bytes(), raw)
        self.assertEqual(LocalStore.open(self.store.root).startup_state, {'layout': {}})
        records = self.store.recovery_records()
        self.assertTrue(any(record['phase'] == 'pending' for record in records))
        self.assertFalse(any(record['phase'] == 'done' for record in records))

    def test_backup_tampering_cannot_authorize_defaults(self):
        raw = b'{"layout":'
        self.path.write_bytes(raw)
        def choose(issue):
            if issue.kind == 'syntax':
                Path(issue.backup).write_bytes(b'changed by another operation')
                return 'defaults'
            return 'exit'
        with self.assertRaises(ConfigStartupCancelled):
            self.store.prepare(choose)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_recovery_diagnostics_do_not_echo_untrusted_receipt_fields(self):
        self.path.write_bytes(b'{"layout":')
        with self.assertRaises(ConfigStartupCancelled):
            self.store.prepare(lambda issue:'exit')
        receipt = next((self.store.root / 'Config-Recovery').glob('*.json'))
        receipt.write_text(json.dumps({'format':'novel-studio-config-backup-v1',
                                      'sha256':'SYNTHETIC-SECRET', 'backup':'../../private/novel'}))
        records = json.dumps(self.store.recovery_records())
        self.assertNotIn('SYNTHETIC-SECRET', records)
        self.assertNotIn('../../private/novel', records)
        self.assertIn('invalid', records)

    def test_parser_resource_rejection_is_conservative(self):
        raw = b'{"integer":' + b'1' * 5000 + b'}'
        self.path.write_bytes(raw)
        with self.assertRaises(LocalConfigIssue) as caught:
            self.store.load()
        self.assertEqual(caught.exception.kind, 'unsafe')
        self.assertFalse(caught.exception.can_reset)
        self.assertEqual(self.path.read_bytes(), raw)
        with patch.object(json, 'loads', side_effect=RecursionError), self.assertRaises(LocalConfigIssue) as caught:
            self.store.load()
        self.assertEqual(caught.exception.kind, 'unsafe')

    def test_full_recovery_directory_refuses_new_records_and_keeps_original(self):
        raw = b'{"layout":'
        self.path.write_bytes(raw)
        with self.assertRaises(ConfigStartupCancelled):
            self.store.prepare(lambda issue:'exit')
        directory = self.store.root / 'Config-Recovery'
        receipt = next(directory.glob('*.json'))
        receipt.write_bytes(b'torn receipt')
        for index in range(998):
            (directory / ('synthetic-' + str(index))).write_bytes(b'x')
        before = self.inventory()
        with self.assertRaises(ConfigStartupCancelled):
            self.store.prepare(lambda issue:'defaults')
        self.assertEqual(self.inventory(), before)
        self.assertEqual(self.path.read_bytes(), raw)
