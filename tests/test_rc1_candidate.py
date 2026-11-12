"""Release identity is tied to a clean Git input set, never a version string alone."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import build_workbench


class CandidateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='novel-rc1-source-proof-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'src/novel_studio/example.py'
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b'VALUE = "synthetic"\n')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic RC1')
        self.git('config', 'user.email', 'synthetic@example.invalid')
        self.git('add', 'src')
        self.git('commit', '-qm', 'synthetic source')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE).decode().strip()

    def test_clean_head_and_exact_inputs_are_bound(self):
        proof = build_workbench.source_provenance(self.root)
        self.assertEqual(proof['commit'], self.git('rev-parse', 'HEAD'))
        self.assertEqual(proof['inputs']['src/novel_studio/example.py'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertNotIn(str(self.root), str(proof))

    def test_dirty_runtime_is_rejected(self):
        self.source.write_bytes(b'VALUE = "changed"\n')
        with self.assertRaises(ValueError):
            build_workbench.source_provenance(self.root)

    def test_ignored_runtime_cannot_sneak_into_package(self):
        (self.root / '.gitignore').write_text('hidden.py\n', encoding='utf-8')
        self.git('add', '.gitignore'); self.git('commit', '-qm', 'synthetic ignore rule')
        (self.source.parent / 'hidden.py').write_bytes(b'UNTRACKED = True\n')
        with self.assertRaises(ValueError):
            build_workbench.source_provenance(self.root)

    def test_local_audit_notes_are_not_runtime_inputs(self):
        notes = self.root / 'docs/audit/local.txt'
        notes.parent.mkdir(parents=True)
        notes.write_bytes(b'synthetic local-only note')
        proof = build_workbench.source_provenance(self.root)
        self.assertNotIn('docs/audit/local.txt', proof['inputs'])
