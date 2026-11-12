"""Real parser with an in-memory process: no CLI/network/model execution."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from novel_studio.core import CoreError
from novel_studio.host import OfficialCLI
from novel_studio.compatibility import ALL_DENY
from novel_studio.compatibility_probe import read_path
import test_long_term_compatibility as fixture_tests


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='novel-compat-transport-synthetic-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.exe = self.root / 'agy.exe'
        self.exe.write_bytes(b'SYNTHETIC NEVER EXECUTED')

    def invoke(self, events):
        process = Mock()
        process.stdin = io.BytesIO()
        process.stdout = io.BytesIO(b''.join(json.dumps(row).encode() + b'\n' for row in events))
        process.wait.return_value = process.poll.return_value = 0
        with patch('novel_studio.host.subprocess.Popen', return_value=process):
            return OfficialCLI(self.exe)._stream(['--settings', json.dumps(ALL_DENY), '--json-schema', '{"type":"object"}'], self.root, 'SYNTHETIC')

    def test_missing_duplicate_init_or_changed_structured_shape_is_rejected(self):
        init = {'event': 'init', 'init': {}}
        result = {'event': 'result', 'result': {'status': 'SUCCESS', 'structured_output': {}}}
        for events in ([result], [init, init, result], [init, {'event': 'result', 'result': {'status': 'SUCCESS'}}],
                       [init, {'event': 'result', 'result': {'status': 'SUCCESS', 'structured_output': []}}],
                       [init, {'event': 'result', 'result': {'status': 'SUCCESS', 'structured_output': {}, 'json_schema': {'type': 'string'}}}]):
            with self.subTest(events=events), self.assertRaises(CoreError):
                self.invoke(events)

    def test_actual_forbidden_tool_done_is_permission_anomaly(self):
        for name in ('write_file', 'write_to_file', 'run_command', 'read_file', 'mcp_unexpected'):
            events = [{'event': 'init', 'init': {}}, {'event': 'step_update', 'step_update': {'state': 'DONE', 'step_type': 'tool', 'tool_info': {'name': name}}}]
            with self.subTest(name=name), self.assertRaises(CoreError) as caught:
                self.invoke(events)
            self.assertEqual(caught.exception.code, 'CLI_PERMISSION_ANOMALY')

    def test_nonbreaking_extra_fields_are_tolerated(self):
        value = self.invoke([{'event': 'init', 'init': {'future_metadata': 1}},
            {'event': 'result', 'future_metadata': True, 'result': {'status': 'SUCCESS', 'structured_output': {}, 'future_field': 'ignored'}}])
        self.assertEqual(value['result']['structured_output'], {})

    def test_canonical_and_legacy_read_path_fields_are_explicitly_supported(self):
        for key in ('Path', 'AbsolutePath'):
            self.assertEqual(read_path({'parameters': {key: 'SYNTHETIC.md'}}), 'SYNTHETIC.md')
        self.assertIsNone(read_path({'parameters': 'INVALID'}))

    def test_binary_changed_with_preserved_stat_metadata_still_invalidates_proof(self):
        fixture = fixture_tests.CapabilityTests()
        fixture.setUp()
        try:
            fixture.prove()
            before = fixture.cli.path.stat()
            raw = fixture.cli.path.read_bytes()
            fixture.cli.path.write_bytes(b'X' + raw[1:])
            os.utime(fixture.cli.path, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertFalse(fixture.manager.inspect()['model_allowed'])
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
