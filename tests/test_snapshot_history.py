"""B01: real synthetic snapshots, bounded access and immutable old bytes."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from novel_studio.core import Core, SafeTree, admin, digest
from novel_studio.exporting import ExportService, DEFAULTS


class SnapshotHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='b01-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.core = Core.create(Path(self.temp.name), 'synthetic', ROOT)
        self.draft = 'Writer-Workspace/Drafts/chapter.md'
        self.setting = 'Canonical/Canon-Worldbuilding/world.md'
        with SafeTree(self.core.project) as tree:
            tree.create(self.draft, b'# Chapter one\noriginal\n')
            tree.create(self.setting, b'# World\nsetting\n')
        self.context = self.core.snapshot()

    def snapshots(self, count):
        return [admin.review_snapshot(self.core.project, self.draft,
                self.context['destination'] + '/SNAPSHOT.json', True) for _ in range(count)]

    def test_100_334_500_snapshots_current_read_paging_restore_approval_export(self):
        snapshots = []
        for count in (100, 334, 500):
            snapshots.extend(self.snapshots(count - len(snapshots)))
            with self.subTest(count=count):
                # Preserve the original reproduction: no Desktop review pointer.
                original_scan = os.scandir
                def active_scan(path):
                    self.assertNotIn('Manuscript-Snapshot', str(path))
                    self.assertNotIn('Context-Snapshot', str(path))
                    return original_scan(path)
                with patch('safe_project.os.scandir', side_effect=active_scan):
                    reopened = Core(self.core.project)
                    rows = reopened.documents()
                    draft = next(r for r in rows if r['relative'] == self.draft)
                    setting = next(r for r in rows if r['relative'] == self.setting)
                    self.assertEqual(reopened.document(draft['document_id'])['text'], '# Chapter one\noriginal\n')
                    self.assertEqual(reopened.document(setting['document_id'])['revision'], setting['revision'])
                    admin.ui_state(self.core.project, {'review': snapshots[-1]['destination'] + '/SNAPSHOT.json'})
                    self.assertEqual(reopened.document(draft['document_id'])['revision'], draft['revision'])
        cursor, entries, scans = None, [], []
        original = os.scandir
        def counted(path):
            scans.append(str(path))
            return original(path)
        with patch('safe_project.os.scandir', side_effect=counted):
            while True:
                page = self.core.snapshot_history(limit=17, cursor=cursor)
                self.assertLessEqual(len(page['entries']), 17)
                entries.extend(page['entries'])
                cursor = page['cursor']
                if cursor is None:
                    break
        self.assertEqual(len(entries), 500)
        self.assertEqual({r['snapshot_id'] for r in entries}, {r['snapshot_id'] for r in snapshots})
        root = str(self.core.project / 'Editor-Workspace/Manuscript-Snapshot')
        self.assertEqual(scans.count(root), 1, 'pagination must not restart archive enumeration')
        chosen = entries[77]
        old = self.core.snapshot_document(chosen['snapshot_id'], 'Drafts/chapter.md', chosen['revision'])
        self.assertEqual(old['revision'], digest(b'# Chapter one\noriginal\n'))
        with self.assertRaises(ValueError):
            self.core.snapshot_document(chosen['snapshot_id'], 'Drafts/other.md', chosen['revision'])
        restored = self.core.restore_snapshot(chosen['snapshot_id'], 'Drafts/chapter.md', chosen['revision'])
        self.assertEqual(self.core.document(restored['document_id'])['text'], old['text'])
        self.assertEqual(restored['source_snapshot']['snapshot_id'], chosen['snapshot_id'])
        plan = self.core.author_preview(draft['document_id'])
        self.core.author_commit(plan['handle'], True)
        service = ExportService(self.core)
        preview = service.preview(service.select(), {**DEFAULTS, 'title': 'Synthetic'}, 'txt')
        output = Path(self.temp.name) / 'export.txt'
        service.export(preview['handle'], output, True)
        self.assertIn('original', output.read_text(encoding='utf-8'))
        for snapshot in snapshots:
            folder = self.core.project / snapshot['destination']
            self.assertEqual(json.loads((folder / 'SNAPSHOT.json').read_bytes())['files'], snapshot['files'])
            for name, sha in snapshot['files'].items():
                self.assertEqual(digest((folder / name).read_bytes()), sha)

    def test_open_and_active_access_never_enumerate_archive(self):
        snapshots = self.snapshots(3)
        admin.ui_state(self.core.project, {'review': snapshots[-1]['destination'] + '/SNAPSHOT.json'})
        original_scan, original_read = os.scandir, SafeTree.read
        reads = []
        def scan(path):
            if 'Manuscript-Snapshot' in str(path) or 'Context-Snapshot' in str(path):
                self.fail('active access enumerated a snapshot archive')
            return original_scan(path)
        def read(tree, name):
            reads.append(str(name).replace('\\', '/'))
            return original_read(tree, name)
        with patch('safe_project.os.scandir', side_effect=scan), patch.object(SafeTree, 'read', read):
            reopened = Core(self.core.project)
            rows = reopened.documents()
            draft = next(r for r in rows if r['relative'] == self.draft)
            reopened.document(draft['document_id'])
        self.assertFalse(any(s['snapshot_id'] in name for s in snapshots[:-1] for name in reads))
        self.assertTrue(any(snapshots[-1]['snapshot_id'] in name for name in reads))

    def test_page_work_is_bounded_and_writer_archives_are_compatible(self):
        for unused in range(4):
            self.core.snapshot()
        original = SafeTree.read
        reads = []
        def read(tree, name):
            reads.append(str(name))
            return original(tree, name)
        with patch.object(SafeTree, 'read', read), patch.object(SafeTree, 'files', side_effect=AssertionError('recursive inventory')):
            page = self.core.snapshot_history('writer', limit=2)
        self.assertEqual(len(page['entries']), 2)
        self.assertEqual(len(reads), 2)
        self.assertTrue(all(name.endswith('/SNAPSHOT.json') for name in reads))
        entry = page['entries'][0]
        selected = self.core.snapshot_document(entry['snapshot_id'], self.setting, entry['revision'], 'writer')
        self.assertEqual(selected['text'], '# World\nsetting\n')
        self.core.close_snapshot_history(page['cursor'])
        self.assertEqual(self.core._snapshot_pages, {})

    def test_untrusted_manifests_and_paths_rejected(self):
        snapshot = self.snapshots(1)[0]
        path = snapshot['destination'] + '/SNAPSHOT.json'
        with SafeTree(self.core.project) as tree:
            original = tree.read(path)
        base = json.loads(original)
        invalid = [
            {**base, 'snapshot_id': '0' * 32},
            {**base, 'files': {'../escape.md': '0' * 64}},
            {**base, 'files': {'/absolute.md': '0' * 64}},
            {**base, 'files': {'Drafts/file.exe': '0' * 64}},
            {**base, 'files': {'Drafts/a.md': 'bad'}},
            {**base, 'files': {'Drafts/a.md': '0' * 64, 'Drafts/A.md': '0' * 64}},
            {**base, 'files': {'Drafts/a/../b.md': '0' * 64}},
            {**base, 'files': {'Drafts/a.md:stream': '0' * 64}},
            {**base, 'files': {'Drafts/x%d.md' % i: '0' * 64 for i in range(1000)}},
            {**base, 'files': {'Writer-Workspace/Drafts/chapter.md': '0' * 64}},
        ]
        for value in invalid:
            with self.subTest(value=str(value)[:90]):
                (self.core.project / path).write_bytes(admin.encode(value))
                with SafeTree(self.core.project) as tree, self.assertRaises(ValueError):
                    admin.snapshot_manifest(tree, path)
        duplicate = original.decode().replace('"files": {', '"files": {}, "files": {', 1).encode()
        (self.core.project / path).write_bytes(duplicate)
        with SafeTree(self.core.project) as tree, self.assertRaises(ValueError):
            admin.snapshot_manifest(tree, path)
        (self.core.project / path).write_bytes(original)
        for identifier, name in [('../escape', 'Drafts/chapter.md'),
                                 (snapshot['snapshot_id'], '../chapter.md')]:
            with self.assertRaises(ValueError):
                self.core.snapshot_document(identifier, name, digest(original))
        with SafeTree(self.core.project) as tree, self.assertRaisesRegex(ValueError, 'file limit'):
            admin._snapshot(tree, 'Editor-Workspace/Manuscript-Snapshot',
                            {'Drafts/%d.md' % i: b'x' for i in range(1000)}, True)

    def test_stale_manifest_payload_and_cursor_never_cached(self):
        snapshot = self.snapshots(3)[0]
        first = self.core.snapshot_history(limit=1)
        self.assertIsNotNone(first['cursor'])
        self.core.snapshot_history(cursor=first['cursor'], limit=1)
        with self.assertRaises(ValueError):
            self.core.snapshot_history(cursor=first['cursor'], limit=1)
        page = self.core.snapshot_history(limit=1)
        with self.assertRaises(ValueError):
            Core(self.core.project).snapshot_history(cursor=page['cursor'])
        self.snapshots(1)
        with self.assertRaises(ValueError):
            self.core.snapshot_history(cursor=page['cursor'])
        path = self.core.project / snapshot['destination'] / 'SNAPSHOT.json'
        revision = digest(path.read_bytes())
        self.core.snapshot_document(snapshot['snapshot_id'], 'Drafts/chapter.md', revision)
        payload = self.core.project / snapshot['destination'] / 'Drafts/chapter.md'
        payload.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.core.snapshot_document(snapshot['snapshot_id'], 'Drafts/chapter.md', revision)
        value = json.loads(path.read_bytes())
        value['files']['Drafts/chapter.md'] = digest(b'changed')
        path.write_bytes(admin.encode(value))
        with self.assertRaises(ValueError):
            self.core.snapshot_document(snapshot['snapshot_id'], 'Drafts/chapter.md', revision)
        self.assertEqual(self.core.snapshot_document(snapshot['snapshot_id'], 'Drafts/chapter.md',
                         digest(path.read_bytes()))['text'], 'changed')
        for limit in (0, 51, True, -1):
            with self.assertRaises(ValueError):
                self.core.snapshot_history(limit=limit)

    def test_exact_snapshot_file_identity_and_restore_bytes_and_source_receipt(self):
        payload = b'\xef\xbb\xbf# Historical\r\noriginal bytes\r\n'
        (self.core.project / self.draft).write_bytes(payload)
        a = self.snapshots(1)[0]
        with SafeTree(self.core.project) as tree:
            tree.create('Writer-Workspace/Drafts/other.md', b'other chapter')
        b = admin.review_snapshot(self.core.project, 'Writer-Workspace/Drafts/other.md',
                                  self.context['destination'] + '/SNAPSHOT.json', True)
        revision = digest((self.core.project / a['destination'] / 'SNAPSHOT.json').read_bytes())
        with self.assertRaises(ValueError):
            self.core.snapshot_document(a['snapshot_id'], 'Drafts/other.md', revision)
        restored = self.core.restore_snapshot(a['snapshot_id'], 'Drafts/chapter.md', revision)
        document = self.core.document(restored['document_id'])
        self.assertEqual((self.core.project / document['relative']).read_bytes(), payload)
        operations = list((self.core.project / '.novel-studio/Operations').glob('*.json'))
        receipt = next(json.loads(p.read_bytes()) for p in operations
                       if json.loads(p.read_bytes()).get('source_snapshot'))
        self.assertEqual(receipt['source_snapshot'], restored['source_snapshot'])
        self.assertEqual(receipt['source_snapshot']['revision'], digest(payload))
        with self.assertRaises(ValueError):
            self.core.restore_snapshot(a['snapshot_id'], self.setting, revision)
        self.assertEqual((self.core.project / b['destination'] / 'Drafts/other.md').read_bytes(), b'other chapter')

    def test_file_count_and_link_guards_remain(self):
        with SafeTree(self.core.project) as tree:
            tree.create('Writer-Workspace/Drafts/other.md', b'other')
            with self.assertRaisesRegex(ValueError, 'file limit'):
                tree.files('Writer-Workspace/Drafts', file_limit=1)
        snapshot = self.snapshots(1)[0]
        path = self.core.project / snapshot['destination'] / 'Drafts/chapter.md'
        linked = self.core.project / 'Writer-Workspace/Drafts/hard.md'
        os.link(path, linked)
        revision = digest((self.core.project / snapshot['destination'] / 'SNAPSHOT.json').read_bytes())
        with self.assertRaises(ValueError):
            self.core.snapshot_document(snapshot['snapshot_id'], 'Drafts/chapter.md', revision)
        # No writes to source/archive files when browsing an old project.
        linked.unlink()
        before = {p.relative_to(self.core.project).as_posix(): digest(p.read_bytes())
                  for p in self.core.project.rglob('*') if p.is_file()}
        old = Core(self.core.project)
        old.documents()
        page = old.snapshot_history()
        old.close_snapshot_history(page['cursor'])
        after = {p.relative_to(self.core.project).as_posix(): digest(p.read_bytes())
                 for p in self.core.project.rglob('*') if p.is_file()}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
