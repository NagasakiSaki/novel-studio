"""B02 real filesystem fault injection, synthetic books and no model calls."""
import copy
import json
import os
import subprocess
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from novel_studio.core import Core, CoreError, SafeTree, admin, digest
import test_workflow_chapters as chapter_fixture


class CanonicalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fresh()

    def fresh(self):
        fixture = chapter_fixture.ChapterAcceptanceTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.core, self.ids = fixture.core, fixture.ids
        self.core.author_commit(self.core.workflow.chapters.preview(self.ids)['handle'], True)
        self.cid = self.ids[0]
        self.prepare(self.cid)

    def row(self):
        return next(r for r in self.core.recovery.diagnose()['operations'] if r.get('chapter_id') == self.cid)

    def point_crash(self, point):
        preview = self.core.workflow.chapters.preview_revision(self.cid)
        def crash(name):
            if name == point:
                raise OSError('SYNTHETIC_' + point)
        with patch('novel_studio.canonical_recovery.checkpoint', side_effect=crash), self.assertRaises(OSError):
            self.core.author_commit(preview['handle'], True)
        self.core = Core(self.core.project)

    def inventory(self):
        return {p.relative_to(self.core.project).as_posix():digest(p.read_bytes())
                for p in self.core.project.rglob('*') if p.is_file()}

    def prepare(self, cid):
        old = copy.deepcopy(self.core.workflow.store.read()['chapters'][cid])
        draft = self.core._artifact('writer', 'drafts', '# Revised\nApproved synthetic revision ' + cid,
                                    'b02-revised-' + cid)
        self.core.workflow.chapters.adopt_draft(cid, draft['document_id'])
        self.core.workflow.store.mutate('test_review', lambda s, t:s['chapters'][cid].update(
            review=old['review'], next_direction=old['next_direction']))
        return self.core.workflow.chapters.preview_revision(cid)

    def crash_before_index(self):
        preview = self.core.workflow.chapters.preview_revision(self.cid)
        original = SafeTree.publish
        def crash(tree, name, payload, expected):
            if name == '.novel-studio/Workflow/state.json':
                raise OSError('SYNTHETIC_BEFORE_INDEX')
            return original(tree, name, payload, expected)
        with patch.object(SafeTree, 'publish', crash), self.assertRaises(OSError):
            self.core.author_commit(preview['handle'], True)
        self.core = Core(self.core.project)

    def test_published_revision_survives_restart_and_repairs_only_index(self):
        self.crash_before_index()
        chapter = self.core.workflow.store.read()['chapters'][self.cid]
        actual = self.core.document(chapter['canonical']['document_id'])
        self.assertNotEqual(chapter['canonical']['revision'], actual['revision'])
        with self.assertRaises(CoreError):
            self.core.workflow.chapters.checked_document(chapter['canonical'], 'canonical')
        row = next(r for r in self.core.recovery.diagnose()['operations'] if r.get('chapter_id') == self.cid)
        self.assertEqual(row['state'], 'INDEX_PENDING')
        preview = self.core.recovery.preview(row['transaction_id'])
        original = SafeTree.publish
        original_create = SafeTree.create
        def no_body(tree, name, payload, expected):
            self.assertFalse(str(name).replace('\\', '/').startswith('Canonical/'), 'recovery wrote formal prose')
            return original(tree, name, payload, expected)
        def no_create(tree, name, payload):
            self.assertFalse(str(name).replace('\\', '/').startswith('Canonical/'), 'recovery created formal prose')
            return original_create(tree, name, payload)
        models = SimpleNamespace(run=Mock(side_effect=AssertionError('model')), structured_task=Mock(side_effect=AssertionError('model')))
        with patch.object(self.core, 'models', models), patch.object(SafeTree, 'publish', no_body), patch.object(SafeTree, 'create', no_create), patch.object(admin, '_commit', side_effect=AssertionError('republication')):
            self.core.author_commit(preview['handle'], True)
        models.run.assert_not_called()
        models.structured_task.assert_not_called()
        chapter = self.core.workflow.store.read()['chapters'][self.cid]
        self.assertEqual(self.core.workflow.chapters.checked_document(chapter['canonical'], 'canonical')['revision'], actual['revision'])
        from novel_studio.exporting import ExportService, DEFAULTS
        exporting = ExportService(self.core)
        selection = exporting.select('custom', [chapter['canonical']['document_id']])
        preview = exporting.preview(selection, {**DEFAULTS, 'title': 'B02 synthetic recovered chapter'}, 'txt')
        destination = self.core.project.parent / 'b02-recovered.txt'
        exporting.export(preview['handle'], destination, True)
        self.assertIn('Approved synthetic revision', destination.read_text(encoding='utf-8'))
        self.assertEqual(self.core.document(chapter['canonical']['document_id'])['revision'], actual['revision'])

    def test_registration_crash_boundaries_are_discoverable_and_can_close(self):
        for point, state in [('reserved', 'INCOMPLETE_UNPUBLISHED'), ('intent', 'ORPHAN_UNREGISTERED'),
                             ('pending', 'PENDING_UNREGISTERED'), ('registered', 'CURRENT_BEFORE')]:
            with self.subTest(point=point):
                self.fresh()
                before = self.core.workflow.store.read()
                self.point_crash(point)
                row = self.row()
                self.assertEqual(row['state'], state)
                self.assertFalse(row['can_recover'])
                approved = self.core.recovery.preview(row['transaction_id'], close=True)
                self.core.author_commit(approved['handle'], True)
                self.assertEqual(self.core.workflow.store.read(), before)
                self.assertEqual(self.core.recovery.diagnose()['operations'], [])

    def test_partial_intent_and_pending_writes_never_authorize_publication(self):
        from novel_studio.canonical_recovery import BASE
        for suffix in ('INTENT.json', 'Pending.json', 'REGISTERED.json'):
            with self.subTest(suffix=suffix):
                self.fresh()
                original_create, original_publish = SafeTree.create, SafeTree.publish
                preview = self.core.workflow.chapters.preview_revision(self.cid)
                def create(tree, name, payload):
                    if name.endswith(suffix):
                        original_create(tree, name, b'{')
                        raise OSError('partial write')
                    return original_create(tree, name, payload)
                def publish(tree, name, payload, expected):
                    if name == BASE + '/Pending.json' and suffix == 'Pending.json':
                        original_publish(tree, name, b'{', expected)
                        raise OSError('partial write')
                    return original_publish(tree, name, payload, expected)
                before = self.core.workflow.store.read()['chapters'][self.cid]['canonical']
                with patch.object(SafeTree, 'create', create), patch.object(SafeTree, 'publish', publish), self.assertRaises(OSError):
                    self.core.author_commit(preview['handle'], True)
                self.core = Core(self.core.project)
                self.assertEqual(self.core.document(before['document_id'])['revision'], before['revision'])
                diagnostic = self.core.recovery.diagnose()
                self.assertTrue(diagnostic['operations'] or diagnostic['warnings'])
                self.assertFalse(any(r['can_recover'] for r in diagnostic['operations']))

    def test_body_published_without_committed_is_never_repaired(self):
        original = SafeTree.create
        def stop(tree, name, payload):
            if name.endswith('/COMMITTED.json'):
                raise OSError('missing COMMITTED')
            return original(tree, name, payload)
        preview = self.core.workflow.chapters.preview_revision(self.cid)
        with patch.object(SafeTree, 'create', stop), self.assertRaises(OSError):
            self.core.author_commit(preview['handle'], True)
        self.core = Core(self.core.project)
        self.assertEqual(self.row()['state'], 'PUBLISHED_NO_RECEIPT')
        with self.assertRaises(CoreError):
            self.core.recovery.preview(self.row()['transaction_id'])

    def test_recovery_authorization_and_index_audit_windows(self):
        self.crash_before_index()
        txn = self.row()['transaction_id']
        preview = self.core.recovery.preview(txn)
        def stop(point):
            if point == 'recovery_index':
                raise OSError('index applied; missing audit')
        with patch('novel_studio.canonical_recovery.checkpoint', side_effect=stop), self.assertRaises(OSError):
            self.core.author_commit(preview['handle'], True)
        self.core = Core(self.core.project)
        self.assertEqual(self.row()['state'], 'INDEX_APPLIED_AUDIT_PENDING')
        sequence = self.core.workflow.store.read()['sequence']
        with patch.object(admin, '_commit', side_effect=AssertionError('body transaction')):
            self.assertEqual(self.core.recovery.retry_audits(), [txn])
            self.assertEqual(self.core.recovery.retry_audits(), [])
        self.assertEqual(self.core.workflow.store.read()['sequence'], sequence)
        # Direct completed lookup is also idempotent and never inventories archives.
        p = self.core.recovery.preview(txn)
        self.core.author_commit(p['handle'], True)
        self.assertEqual(self.core.workflow.store.read()['sequence'], sequence)

    def test_audit_retry_never_applies_a_pending_index(self):
        self.crash_before_index()
        before = self.core.workflow.store.read()
        self.assertEqual(self.core.recovery.retry_audits(), [])
        self.assertEqual(self.core.workflow.store.read(), before)

    def test_all_archive_and_cleanup_interruption_points_resume(self):
        for point in ('before_done', 'done', 'archive_copied', 'pending_archive', 'quota_archive', 'cleanup_one', 'pending_removed'):
            with self.subTest(point=point):
                self.fresh()
                self.point_crash(point)
                sequence = self.core.workflow.store.read()['sequence']
                self.assertTrue(self.core.recovery.diagnose()['operations'])
                self.core.recovery.retry_audits()
                self.assertEqual(self.core.recovery.diagnose()['operations'], [])
                self.assertEqual(self.core.workflow.store.read()['sequence'], sequence)

    def test_diagnosis_is_readonly_and_archives_are_not_enumerated(self):
        self.crash_before_index()
        before = self.inventory()
        original = SafeTree.children
        def children(tree, name):
            self.assertNotIn('/Archive', name)
            self.assertNotEqual(name, 'Backups/Transactions')
            return original(tree, name)
        with patch.object(SafeTree, 'children', children):
            self.core.recovery.diagnose()
        self.assertEqual(before, self.inventory())

    def test_sha_and_receipt_corruption_fail_closed(self):
        from novel_studio.canonical_recovery import BASE
        for name in ('before.bin', 'after.bin', 'PREPARED.json', 'COMMITTED.json', 'intent', 'body'):
            with self.subTest(name=name):
                self.fresh()
                self.crash_before_index()
                row = self.row()
                txn = row['transaction_id']
                if name == 'intent':
                    path = self.core.project / BASE / 'Staging' / f"{row['slot']:02d}" / ('.ns-' + txn + '-INTENT.json')
                elif name == 'body':
                    current = self.core.workflow.store.read()['chapters'][self.cid]['canonical']
                    path = self.core.project / self.core.document(current['document_id'])['relative']
                else:
                    path = self.core.project / 'Backups/Transactions' / txn / name
                path.write_bytes(b'corrupt')
                self.assertFalse(any(r['can_recover'] for r in self.core.recovery.diagnose()['operations']))
                with self.assertRaises((ValueError, OSError)):
                    self.core.recovery.preview(txn)
                self.assertEqual(path.read_bytes(), b'corrupt')

    def test_unapproved_stale_and_cross_project_handles_are_rejected(self):
        preview = self.core.workflow.chapters.preview_revision(self.cid)
        before = self.inventory()
        with self.assertRaises(CoreError):
            self.core.author_commit(preview['handle'], False)
        self.assertEqual(before, self.inventory())
        self.crash_before_index()
        preview = self.core.recovery.preview(self.row()['transaction_id'])
        with self.assertRaises(CoreError):
            Core(self.core.project).author_commit(preview['handle'], True)
        self.core.workflow.store.mutate('test_unrelated_change', lambda s, t:s.update(test_value=True))
        with self.assertRaises(CoreError):
            self.core.author_commit(preview['handle'], True)

    def test_successor_aba_and_same_chapter_context_cannot_be_overwritten(self):
        self.crash_before_index()
        txn = self.row()['transaction_id']
        old = self.core.workflow.store.read()['chapters'][self.cid]
        self.core.workflow.store.mutate('test_aba', lambda s,t:s['chapters'][self.cid].update(approval_transaction='f' * 32))
        self.assertFalse(self.row()['can_recover'])
        with self.assertRaises(CoreError):
            self.core.recovery.preview(txn)
        self.core.workflow.store.mutate('test_new_draft_context', lambda s,t:s['chapters'][self.cid].update(
            approval_transaction=old['approval_transaction'], title='new author title'))
        self.assertFalse(self.row()['can_recover'])

    def test_capacity_refuses_new_publication_but_allows_reads(self):
        from novel_studio import canonical_recovery as recovery
        before = self.core.workflow.store.read()['chapters'][self.cid]['canonical']
        preview = self.core.workflow.chapters.preview_revision(self.cid)
        with patch.object(recovery, 'HARD', recovery.RESERVE - 1), self.assertRaises(CoreError):
            self.core.author_commit(preview['handle'], True)
        self.assertEqual(self.core.document(before['document_id'])['revision'], before['revision'])
        self.assertEqual(self.core.recovery.diagnose()['operations'], [])

    def test_pending_missing_with_registered_uses_explicit_confirmed_merge(self):
        from novel_studio.canonical_recovery import BASE
        self.crash_before_index()
        path = self.core.project / BASE / 'Pending.json'
        value = json.loads(path.read_bytes())
        value['entries'] = {}
        path.write_bytes(admin.encode(value))
        row = self.row()
        self.assertEqual(row['state'], 'REGISTRY_MISSING')
        preview = self.core.recovery.preview(row['transaction_id'])
        self.core.author_commit(preview['handle'], True)
        self.assertEqual(self.core.recovery.diagnose()['operations'], [])

    def test_path_traversal_duplicate_json_and_hardlink_protection(self):
        from novel_studio.canonical_recovery import BASE
        self.crash_before_index()
        row = self.row()
        for txn in ('../escape', 'C:/escape', 'x' * 32):
            with self.assertRaises(ValueError):
                self.core.recovery.preview(txn)
        path = self.core.project / BASE / 'Staging' / f"{row['slot']:02d}" / ('.ns-' + row['transaction_id'] + '-INTENT.json')
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b'"slot":', b'"slot": 0, "slot":', 1))
        self.assertFalse(any(r['can_recover'] for r in self.core.recovery.diagnose()['operations']))
        path.write_bytes(raw)
        linked = self.core.project / 'test-hardlink.bin'
        os.link(path, linked)
        self.assertFalse(any(r['can_recover'] for r in self.core.recovery.diagnose()['operations']))

    def test_two_windows_recover_once_and_project_lock_is_nonblocking(self):
        self.crash_before_index()
        txn = self.row()['transaction_id']
        other = Core(self.core.project)
        a = self.core.recovery.preview(txn)
        b = other.recovery.preview(txn)
        before = self.core.workflow.store.read()['sequence']
        self.core.author_commit(a['handle'], True)
        with self.assertRaises(CoreError):
            other.author_commit(b['handle'], True)
        self.assertEqual(self.core.workflow.store.read()['sequence'], before + 1)
        with SafeTree(self.core.project) as tree, tree.lock():
            with self.assertRaises(CoreError) as error:
                with other.recovery._write():
                    self.fail('lock reentered')
            self.assertEqual(error.exception.code, 'BUSY')

    def test_two_chapters_registration_merges_latest_pending(self):
        other_cid = self.ids[1]
        preview_b = self.prepare(other_cid)
        preview_a = self.core.workflow.chapters.preview_revision(self.cid)
        other = Core(self.core.project)
        # A remains registered; B must read and merge A rather than overwrite it.
        def stop(point):
            if point == 'registered':
                raise OSError('registered, before body')
        for core, preview in [(self.core, preview_a), (self.core, preview_b)]:
            with patch('novel_studio.canonical_recovery.checkpoint', side_effect=stop), self.assertRaises(OSError):
                core.author_commit(preview['handle'], True)
        rows = other.recovery.diagnose()['operations']
        self.assertEqual({r['chapter_id'] for r in rows}, set(self.ids))
        self.assertEqual(len({r['slot'] for r in rows}), 2)

    def test_concurrent_cores_return_busy_and_preserve_other_chapter_entry(self):
        other_cid = self.ids[1]
        self.prepare(other_cid)
        other = Core(self.core.project)
        a = self.core.workflow.chapters.preview_revision(self.cid)
        b = other.workflow.chapters.preview_revision(other_cid)
        held, release = threading.Event(), threading.Event()
        errors = []
        def stop(point):
            if point == 'registered' and threading.current_thread().name == 'b02-first':
                held.set()
                if not release.wait(5):
                    raise AssertionError('test release missing')
                raise OSError('registered A')
        def first():
            try:
                self.core.author_commit(a['handle'], True)
            except OSError:
                pass
            except Exception as error:
                errors.append(error)
        with patch('novel_studio.canonical_recovery.checkpoint', side_effect=stop):
            thread = threading.Thread(target=first, name='b02-first')
            thread.start()
            try:
                self.assertTrue(held.wait(5))
                with self.assertRaises(CoreError) as error:
                    other.author_commit(b['handle'], True)
                self.assertEqual(error.exception.code, 'BUSY')
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        b = other.workflow.chapters.preview_revision(other_cid)
        other.author_commit(b['handle'], True)
        rows = other.recovery.diagnose()['operations']
        self.assertEqual([r['chapter_id'] for r in rows], [self.cid])

    def test_concurrent_recovery_cores_never_double_apply(self):
        self.crash_before_index()
        txn = self.row()['transaction_id']
        other = Core(self.core.project)
        a, b = self.core.recovery.preview(txn), other.recovery.preview(txn)
        held, release = threading.Event(), threading.Event()
        errors = []
        before = self.core.workflow.store.read()['sequence']
        def pause(point):
            if point == 'recovery_authorized':
                held.set()
                if not release.wait(5):
                    raise AssertionError('release missing')
        def first():
            try:
                self.core.author_commit(a['handle'], True)
            except Exception as error:
                errors.append(error)
        with patch('novel_studio.canonical_recovery.checkpoint', side_effect=pause):
            thread = threading.Thread(target=first)
            thread.start()
            try:
                self.assertTrue(held.wait(5))
                with self.assertRaises(CoreError):
                    other.author_commit(b['handle'], True)
            finally:
                release.set()
                thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(other.workflow.store.read()['sequence'], before + 1)

    def test_real_child_process_exit_leaves_lock_and_recoverable_published_body(self):
        code = '''import sys, os
sys.path.insert(0, sys.argv[3])
from novel_studio.core import Core
from novel_studio import canonical_recovery
def abrupt(point):
    if point == 'body': os._exit(23)
canonical_recovery.checkpoint = abrupt
c = Core(sys.argv[1])
p = c.workflow.chapters.preview_revision(sys.argv[2])
c.author_commit(p['handle'], True)
'''
        process = subprocess.run([sys.executable, '-B', '-c', code, str(self.core.project), self.cid, str(ROOT / 'src')], timeout=30)
        self.assertEqual(process.returncode, 23)
        self.core = Core(self.core.project)
        self.assertTrue(self.core.recovery.diagnose()['busy'])
        self.assertEqual(self.core.workflow.store.recover_locks(True), ['.ns-author.lock'])
        self.assertEqual(self.row()['state'], 'INDEX_PENDING')
        preview = self.core.recovery.preview(self.row()['transaction_id'])
        self.core.author_commit(preview['handle'], True)

    def test_capacity_warning_full_slots_and_history_reuse(self):
        from novel_studio import canonical_recovery as recovery
        self.point_crash('registered')
        self.prepare(self.ids[1])
        before = self.inventory()
        with patch.object(recovery, 'SLOTS', 1), self.assertRaises(CoreError):
            preview = self.core.workflow.chapters.preview_revision(self.ids[1])
            self.core.author_commit(preview['handle'], True)
        self.assertEqual(before, self.inventory())
        with patch.object(recovery, 'WARNING', 0):
            self.assertTrue(self.core.recovery.diagnose()['warnings'])
        q = json.loads((self.core.project / recovery.BASE / 'Quota.json').read_bytes())
        entry = next(iter(q['entries'].values()))
        self.assertLessEqual(len(entry['history_new']), 1)

    def test_cleanup_refuses_corrupt_archive_and_keeps_unique_source(self):
        from novel_studio.canonical_recovery import BASE
        self.point_crash('archive_copied')
        row = self.row()
        archive = self.core.project / BASE / 'Archive' / row['transaction_id']
        plan = next(archive.glob('*-PLAN.json'))
        plan.write_bytes(b'corrupt')
        stage = self.core.project / BASE / 'Staging' / f"{row['slot']:02d}"
        before = {p.name:p.read_bytes() for p in stage.iterdir()}
        self.assertEqual(self.core.recovery.retry_audits(), [])
        self.assertEqual(before, {p.name:p.read_bytes() for p in stage.iterdir()})

    def test_external_body_change_between_confirmation_and_index_is_preserved(self):
        self.crash_before_index()
        chapter = self.core.workflow.store.read()['chapters'][self.cid]
        path = self.core.project / self.core.document(chapter['canonical']['document_id'])['relative']
        preview = self.core.recovery.preview(self.row()['transaction_id'])
        before = self.core.workflow.store.read()
        def change(point):
            if point == 'recovery_authorized':
                path.write_bytes(b'external author bytes')
        with patch('novel_studio.canonical_recovery.checkpoint', side_effect=change), self.assertRaises(CoreError):
            self.core.author_commit(preview['handle'], True)
        self.assertEqual(path.read_bytes(), b'external author bytes')
        self.assertEqual(self.core.workflow.store.read(), before)

    def test_many_closed_directories_do_not_change_bounded_startup_reads(self):
        from novel_studio.canonical_recovery import BASE
        self.crash_before_index()
        archive = self.core.project / BASE / 'Archive'
        archive.mkdir(exist_ok=True)
        for number in range(2000):
            (archive / f'{number:032x}').mkdir()
        original_read, original_children = SafeTree.read, SafeTree.children
        reads = []
        def read(tree, name):
            self.assertNotIn('/Archive/', name)
            reads.append(name)
            return original_read(tree, name)
        def children(tree, name):
            self.assertNotIn('/Archive', name)
            return original_children(tree, name)
        with patch.object(SafeTree, 'read', read), patch.object(SafeTree, 'children', children):
            row = Core(self.core.project).recovery.diagnose()['operations'][0]
        self.assertEqual(row['state'], 'INDEX_PENDING')
        self.assertLess(len(reads), 40)

    def test_approved_plan_traversal_remains_rejected_after_rehashing_local_manifest(self):
        from novel_studio.canonical_recovery import BASE
        self.crash_before_index()
        row = self.row()
        txn = row['transaction_id']
        folder = self.core.project / BASE / 'Staging' / f"{row['slot']:02d}"
        plan_path = folder / ('.ns-' + txn + '-PLAN.json')
        intent_path = folder / ('.ns-' + txn + '-INTENT.json')
        plan = json.loads(plan_path.read_bytes())
        plan['target'] = 'Canonical/../outside.md'
        plan['approval_digest'] = digest(admin.encode({k:v for k,v in plan.items() if k != 'approval_digest'}))
        plan_path.write_bytes(admin.encode(plan))
        intent = json.loads(intent_path.read_bytes())
        intent['plan_sha'] = digest(plan_path.read_bytes())
        intent_path.write_bytes(admin.encode(intent))
        for name in ('Pending', 'Quota'):
            path = self.core.project / BASE / (name + '.json')
            ledger = json.loads(path.read_bytes())
            ledger['entries'][txn]['intent_sha'] = digest(intent_path.read_bytes())
            path.write_bytes(admin.encode(ledger))
        self.assertFalse(any(r['can_recover'] for r in self.core.recovery.diagnose()['operations']))
        with self.assertRaises(ValueError):
            self.core.recovery.preview(txn)
        self.assertFalse((self.core.project / 'outside.md').exists())

    def test_source_changed_after_preview_cannot_publish_or_persist_approval(self):
        preview = self.core.workflow.chapters.preview_revision(self.cid)
        envelope = self.core._approvals[preview['handle']][1]
        (self.core.project / envelope['plan']['source']).write_bytes(b'changed after preview')
        before = self.core.workflow.store.read()['chapters'][self.cid]['canonical']
        with self.assertRaises(CoreError):
            self.core.author_commit(preview['handle'], True)
        self.assertEqual(self.core.document(before['document_id'])['revision'], before['revision'])
        self.assertEqual(self.core.recovery.diagnose()['operations'], [])

    def test_disk_space_guard_and_audit_failure_do_not_modify_index(self):
        from novel_studio import canonical_recovery as recovery
        preview = self.core.workflow.chapters.preview_revision(self.cid)
        with patch.object(recovery.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)), self.assertRaises(CoreError):
            self.core.author_commit(preview['handle'], True)
        self.crash_before_index()
        before = self.core.workflow.store.read()
        preview = self.core.recovery.preview(self.row()['transaction_id'])
        original = SafeTree.create
        def full(tree, name, payload):
            if '-AUTH-' in name:
                raise OSError('ENOSPC before recovery authorization')
            return original(tree, name, payload)
        with patch.object(SafeTree, 'create', full), self.assertRaises(OSError):
            self.core.author_commit(preview['handle'], True)
        self.assertEqual(self.core.workflow.store.read(), before)

    def test_pending_without_registered_cannot_close_if_transaction_directory_exists(self):
        self.point_crash('pending')
        row = self.row()
        self.assertEqual(row['state'], 'PENDING_UNREGISTERED')
        (self.core.project / 'Backups/Transactions' / row['transaction_id']).mkdir()
        row = self.row()
        self.assertEqual(row['state'], 'CONFLICT')
        self.assertFalse(row['can_close'])
        self.assertFalse(row['can_recover'])

    def test_shared_pending_cas_never_overwrites_an_external_edit(self):
        from novel_studio.canonical_recovery import BASE
        self.point_crash('registered')
        service = self.core.recovery
        with service._write() as tree:
            pending, quota = service._controls(tree)
            path = BASE + '/Pending.json'
            raw = tree.read(path)
            changed = json.loads(raw)
            changed['generation'] += 1
            tree.publish(path, admin.encode(changed), digest(raw))
            with self.assertRaises(CoreError):
                service._ledger(tree, 'Pending', pending)
            self.assertEqual(tree.read(path), admin.encode(changed))

    def test_torn_done_audit_is_preserved_and_completed_without_reconfirmation(self):
        from novel_studio.canonical_recovery import BASE
        self.crash_before_index()
        txn = self.row()['transaction_id']
        preview = self.core.recovery.preview(txn)
        original = SafeTree.create
        def torn(tree, name, payload):
            if name.endswith('-DONE.json'):
                original(tree, name, b'{')
                raise OSError('ENOSPC during completed audit write')
            return original(tree, name, payload)
        with patch.object(SafeTree, 'create', torn), self.assertRaises(OSError):
            self.core.author_commit(preview['handle'], True)
        self.core = Core(self.core.project)
        self.assertEqual(self.row()['state'], 'INDEX_APPLIED_AUDIT_PENDING')
        sequence = self.core.workflow.store.read()['sequence']
        self.assertEqual(self.core.recovery.retry_audits(), [txn])
        archive = self.core.project / BASE / 'Archive' / txn
        self.assertEqual((archive / ('.ns-' + txn + '-DONE.json')).read_bytes(), b'{')
        self.assertEqual(len(list(archive.glob('*-DONE-*.json'))), 1)
        self.assertEqual(self.core.workflow.store.read()['sequence'], sequence)

    def test_legacy_schema_is_not_migrated_and_future_schema_is_not_written(self):
        path = self.core.project / '.novel-studio/Workflow/state.json'
        state = json.loads(path.read_bytes())
        state['product_schema'] = 1
        path.write_bytes(admin.encode(state))
        before = self.inventory()
        self.assertEqual(Core(self.core.project).recovery.diagnose()['operations'], [])
        self.assertEqual(before, self.inventory())
        self.crash_before_index()
        preview = self.core.recovery.preview(self.row()['transaction_id'])
        self.core.author_commit(preview['handle'], True)
        self.assertEqual(json.loads(path.read_bytes())['product_schema'], 1)
        state = json.loads(path.read_bytes())
        state['product_schema'] = 999
        path.write_bytes(admin.encode(state))
        before = self.inventory()
        with self.assertRaises(CoreError):
            Core(self.core.project).recovery.diagnose()
        self.assertEqual(before, self.inventory())

    def test_four_recovery_prestate_limit_keeps_all_evidence(self):
        self.crash_before_index()
        txn = self.row()['transaction_id']
        def stop(point):
            if point == 'recovery_authorized':
                raise OSError('before index')
        for number in range(4):
            self.core.workflow.store.mutate('test_other_metadata', lambda s,t:s.update(other_metadata=number))
            preview = self.core.recovery.preview(txn)
            with patch('novel_studio.canonical_recovery.checkpoint', side_effect=stop), self.assertRaises(OSError):
                self.core.author_commit(preview['handle'], True)
        self.core.workflow.store.mutate('test_other_metadata', lambda s,t:s.update(other_metadata=5))
        preview = self.core.recovery.preview(txn)
        before = self.inventory()
        with self.assertRaises(CoreError) as error:
            self.core.author_commit(preview['handle'], True)
        self.assertEqual(error.exception.code, 'RECOVERY_CAPACITY')
        self.assertEqual(before, self.inventory())

    def test_cancelled_orphan_archive_interruption_is_idempotent(self):
        for point in ('archive_copied', 'pending_archive', 'cleanup_one'):
            with self.subTest(point=point):
                self.fresh()
                self.point_crash('intent')
                row = self.row()
                before = self.core.workflow.store.read()
                preview = self.core.recovery.preview(row['transaction_id'], close=True)
                def stop(name):
                    if name == point:
                        raise OSError('cancel archive window')
                with patch('novel_studio.canonical_recovery.checkpoint', side_effect=stop), self.assertRaises(OSError):
                    self.core.author_commit(preview['handle'], True)
                self.core = Core(self.core.project)
                self.assertEqual(self.row()['state'], 'CANCELLED_CLOSING')
                self.assertEqual(self.core.recovery.retry_audits(), [row['transaction_id']])
                self.assertEqual(self.core.recovery.diagnose()['operations'], [])
                self.assertEqual(self.core.workflow.store.read(), before)

    def test_failed_history_backup_orphan_closes_without_phantom_byte_charge(self):
        from novel_studio.canonical_recovery import BASE, MIB
        preview = self.core.workflow.chapters.preview_revision(self.cid)
        original = SafeTree.create
        def stop(tree, name, payload):
            if name.startswith('.novel-studio/Workflow/History/'):
                raise OSError('ENOSPC before history backup')
            return original(tree, name, payload)
        with patch.object(SafeTree, 'create', stop), self.assertRaises(OSError):
            self.core.author_commit(preview['handle'], True)
        row = self.row()
        self.assertEqual(row['state'], 'INCOMPLETE_UNPUBLISHED')
        preview = self.core.recovery.preview(row['transaction_id'], close=True)
        self.core.author_commit(preview['handle'], True)
        quota = json.loads((self.core.project / BASE / 'Quota.json').read_bytes())
        self.assertEqual(quota['entries'], {})
        self.assertLess(quota['used'], MIB)

    def test_partial_body_transaction_backups_do_not_allow_index_recovery(self):
        for suffix in ('/before.bin', '/after.bin', '/PREPARED.json'):
            with self.subTest(suffix=suffix):
                self.fresh()
                before = self.core.workflow.store.read()['chapters'][self.cid]['canonical']
                preview = self.core.workflow.chapters.preview_revision(self.cid)
                original = SafeTree.create
                def partial(tree, name, payload):
                    if name.endswith(suffix):
                        original(tree, name, b'{')
                        raise OSError('ENOSPC during formal transaction preparation')
                    return original(tree, name, payload)
                with patch.object(SafeTree, 'create', partial), self.assertRaises(OSError):
                    self.core.author_commit(preview['handle'], True)
                self.core = Core(self.core.project)
                self.assertEqual(self.core.document(before['document_id'])['revision'], before['revision'])
                self.assertFalse(self.row()['can_recover'])

    def test_publication_staging_failure_preserves_formal_bytes(self):
        for written in (False, True):
            with self.subTest(written=written):
                self.fresh()
                before = self.core.workflow.store.read()['chapters'][self.cid]['canonical']
                preview = self.core.workflow.chapters.preview_revision(self.cid)
                original = SafeTree.create
                def staging(tree, name, payload):
                    if str(name).replace('\\', '/').startswith('Canonical/') and Path(name).name.startswith('.ns-'):
                        if written:
                            original(tree, name, payload)
                        raise OSError('publication staging failure')
                    return original(tree, name, payload)
                with patch.object(SafeTree, 'create', staging), self.assertRaises(OSError):
                    self.core.author_commit(preview['handle'], True)
                self.core = Core(self.core.project)
                self.assertEqual(self.core.document(before['document_id'])['revision'], before['revision'])
                self.assertFalse(self.row()['can_recover'])

    def test_invalid_archive_pointer_does_not_close_an_orphan(self):
        from novel_studio.canonical_recovery import BASE
        self.point_crash('intent')
        row = self.row()
        path = self.core.project / BASE / 'Quota.json'
        quota = json.loads(path.read_bytes())
        quota['entries'][row['transaction_id']]['archive_sha'] = '0' * 64
        path.write_bytes(admin.encode(quota))
        before = self.inventory()
        diagnosed = self.core.recovery.diagnose()['operations'][0]
        self.assertEqual(diagnosed['state'], 'EVIDENCE_INVALID')
        self.assertFalse(diagnosed['can_close'])
        self.assertFalse(diagnosed['can_recover'])
        self.assertEqual(before, self.inventory())
