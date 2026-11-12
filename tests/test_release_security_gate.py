"""B05: isolated offline gate fixtures; no model, desktop or user state."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import security_check as gate

ARCHIVE = 'docs/versions/1.3/testing/long-term-compatibility/package.json'


class ReleaseSecurityGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='b05-gate-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('vendor', 'scripts', 'agents'):
            (self.root / name).mkdir()
        self.write('plugin.json', b'{"name":"synthetic-test"}')
        self.write('integrations.lock.json', b'{"files":[]}')
        self.write('agents/novel-editor.md', (ROOT / 'agents/novel-editor.md').read_bytes())
        self.has_archive = (ROOT / ARCHIVE).is_file()
        self.raw = ((ROOT / ARCHIVE).read_bytes() if self.has_archive
                    else b'{"status": "PASS", "fixture": "synthetic archived metadata only"}')

    def write(self, name, raw):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def assert_surface(self, name):
        self.assertIn(name + ': unreviewed executable/service surface', gate.check_repository(self.root))

    def test_exact_archived_package_evidence_is_readonly(self):
        if not self.has_archive:
            self.skipTest('Original internal archive is omitted from the source distribution')
        path = self.write(ARCHIVE, self.raw)
        before = {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(gate.check_repository(self.root), [])
        self.assertEqual(path.read_bytes(), self.raw)
        self.assertEqual(before, {p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_archive_still_matches_historical_manifest(self):
        if not self.has_archive:
            self.skipTest('Original internal archive is omitted from the source distribution')
        manifest = json.loads((ROOT / ARCHIVE).with_name('manifest.json').read_bytes())
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), manifest['files']['package.json'])

    def test_real_packages_and_same_bytes_elsewhere_remain_blocked(self):
        for name in ('package.json', 'docs/package.json', 'docs/other/package.json',
                     'docs/versions/1.3/testing/long-term-compatibility/other/package.json'):
            for raw in (self.raw, b'{"scripts":{"preinstall":"node service.js"},"dependencies":{"test":"1"}}'):
                with self.subTest(name=name, raw=raw[:20]):
                    self.write(name, raw)
                    self.assert_surface(name)

    def test_archive_tampering_cannot_be_authorized_by_local_manifest(self):
        value = json.loads(self.raw)
        value['scripts'] = {'start': 'node hidden-service.js'}
        payload = json.dumps(value).encode()
        self.write(ARCHIVE, payload)
        self.write(str(Path(ARCHIVE).with_name('manifest.json')),
                   json.dumps({'files':{'package.json':hashlib.sha256(payload).hexdigest()}}).encode())
        self.assert_surface(ARCHIVE)

    def test_archive_byte_changes_invalid_json_and_wrong_shapes_are_blocked(self):
        same_size_changed = self.raw.replace(b'"status": "PASS"', b'"status": "FAIL"', 1)
        self.assertEqual(len(same_size_changed), len(self.raw))
        self.assertNotEqual(same_size_changed, self.raw)
        padded_package = b'{"scripts":{"preinstall":"node hidden.js"}}'.ljust(len(self.raw), b' ')
        for payload in (same_size_changed, padded_package, self.raw + b'\n', b'{', b'[]', b'null', b'{}',
                        json.dumps(json.loads(self.raw), sort_keys=True).encode()):
            with self.subTest(payload=payload[:20]):
                self.write(ARCHIVE, payload)
                self.assert_surface(ARCHIVE)

    def test_sibling_scripts_and_services_are_not_exempted(self):
        self.write(ARCHIVE, self.raw)
        parent = Path(ARCHIVE).parent.as_posix()
        for name in ('relay.js', 'worker.mjs', 'service.cjs', 'task.ts', 'run.sh', 'run.ps1',
                     'run.bat', 'run.cmd', 'helper.exe', 'helper.dll', 'Dockerfile', 'compose.yaml', 'docker-compose.yml'):
            relative = parent + '/' + name
            self.write(relative, b'synthetic, never executed')
            with self.subTest(name=name):
                self.assert_surface(relative)

    def test_linked_archive_is_not_accepted_even_with_matching_bytes(self):
        target = self.write('synthetic-outside/archive.json', self.raw)
        path = self.root / ARCHIVE
        path.parent.mkdir(parents=True)
        try:
            path.symlink_to(target)
        except OSError as error:
            self.skipTest('OS does not allow creating test symlink: ' + type(error).__name__)
        self.assert_surface(ARCHIVE)

    def test_accepted_fix_modules_have_reviewed_imports_and_calls(self):
        for name in ('gui/service.py', 'src/novel_studio/__main__.py', 'src/novel_studio/core.py',
                     'src/novel_studio/snapshot_history.py', 'src/novel_studio/canonical_recovery.py',
                     'src/novel_studio/desktop/canonical_recovery_ui.py', 'src/novel_studio/desktop/local_config_ui.py',
                     'src/novel_studio/desktop/presenter.py', 'src/novel_studio/desktop/object_controller.py'):
            with self.subTest(name=name):
                self.assertEqual(gate.scan_python((ROOT / name).read_text(encoding='utf-8'), name), [])

    def test_config_store_open_does_not_exempt_real_writes_or_nested_execution(self):
        for label in ('gui/service.py', 'src/novel_studio/desktop/presenter.py'):
            with self.subTest(label=label):
                self.assertEqual(gate.scan_python('LocalStore.open(root, callback)', label), [])
                for source in ("open('file', 'w')", "Path.open(path, 'w')", 'other.open(root, callback)',
                               'LocalStore.open(root, callback, extra)', 'LocalStore.open(root, os.system(command))'):
                    self.assertTrue(gate.scan_python(source, label), source)
        self.assertTrue(gate.scan_python('LocalStore.open(root, callback)', 'candidate.py'))

    def test_new_ui_call_reviews_do_not_authorize_execution_or_socket_connections(self):
        for label in ('src/novel_studio/desktop/local_config_ui.py',
                      'src/novel_studio/desktop/canonical_recovery_ui.py'):
            with self.subTest(label=label):
                self.assertEqual(gate.scan_python('button.clicked.connect(handler)', label), [])
                if label.endswith('local_config_ui.py'):
                    self.assertEqual(gate.scan_python('buttons.accepted.connect(handler)', label), [])
                    self.assertEqual(gate.scan_python('buttons.rejected.connect(handler)', label), [])
                for source in ('exec(source)', 'eval(source)', 'socket.connect(address)',
                               'transport.connect(address)', 'dialog.exec(source)'):
                    self.assertTrue(gate.scan_python(source, label), source)
        self.assertEqual(gate.scan_python('dialog.exec()', 'src/novel_studio/desktop/local_config_ui.py'), [])

    def test_recovery_safe_tree_review_does_not_authorize_arbitrary_mutations(self):
        label = 'src/novel_studio/canonical_recovery.py'
        self.assertEqual(gate.scan_python('tree.mkdir(folder)', label), [])
        for source in ('os.mkdir(folder)', 'p.mkdir(folder)', 'shutil.rmtree(folder)',
                       'p.unlink()', 'p.write_text(source)', "open('file', 'wb')", 'os.system(command)'):
            self.assertTrue(gate.scan_python(source, label), source)

    def test_unregistered_python_surface_is_still_rejected(self):
        self.write('src/unregistered_service.py', b'print("synthetic")')
        self.assertIn('src/unregistered_service.py: unreviewed Python surface', gate.check_repository(self.root))

    def test_clean_runtime_without_optional_historical_evidence_passes(self):
        self.assertEqual(gate.check_repository(self.root), [])

    def test_current_checkout_gate_passes(self):
        self.assertEqual(gate.check_repository(ROOT), [])


if __name__ == '__main__':
    unittest.main()
