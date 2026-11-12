"""Phase two uses real synthetic files; no private data or model requests."""
import copy
import json
import os
import subprocess
import sys
import time
import threading
from pathlib import Path
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from novel_studio.core import Core, CoreError, SafeTree, admin, digest
import test_canonical_recovery as recovery_fixture
import test_workflow_chapters as chapter_fixture


class PhaseTwoTests(unittest.TestCase):
    def setUp(self):
        fixture = recovery_fixture.CanonicalRecoveryTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.core, self.cid, self.ids = fixture.core, fixture.cid, fixture.ids
        preview = self.core.workflow.chapters.preview_revision(self.cid)
        self.receipt = self.core.author_commit(preview['handle'], True)
        self.txn = self.receipt['transaction_id']
        self.receipt = json.loads((self.core.project / 'Backups/Transactions' / self.txn / 'COMMITTED.json').read_bytes())

    def test_restore_published_index_interruption_is_recoverable(self):
        preview = self.core.author_preview(transaction=self.txn)
        original = SafeTree.publish
        def fail_index(tree, name, payload, expected):
            if name == '.novel-studio/Workflow/state.json':
                raise OSError('synthetic index interruption')
            return original(tree, name, payload, expected)
        with patch.object(SafeTree, 'publish', fail_index), self.assertRaises(OSError):
            self.core.author_commit(preview['handle'], True)
        self.core = Core(self.core.project)
        rows = self.core.recovery.diagnose()['operations']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state'], 'INDEX_PENDING')
        target = self.receipt['target']
        body = (self.core.project / target).read_bytes()
        original_create = SafeTree.create
        original_publish = SafeTree.publish
        def no_create(tree, name, payload):
            self.assertFalse(name.replace(chr(92), '/').startswith('Canonical/'))
            return original_create(tree, name, payload)
        def no_publish(tree, name, payload, expected):
            self.assertFalse(name.replace(chr(92), '/').startswith('Canonical/'))
            return original_publish(tree, name, payload, expected)
        models = SimpleNamespace(run=Mock(side_effect=AssertionError('model')), structured_task=Mock(side_effect=AssertionError('model')))
        with patch.object(admin, '_commit', side_effect=AssertionError('republication')), patch.object(SafeTree, 'create', no_create), patch.object(SafeTree, 'publish', no_publish), patch.object(self.core, 'models', models):
            handle = self.core.recovery.preview(rows[0]['transaction_id'])['handle']
            self.core.author_commit(handle, True)
        models.run.assert_not_called()
        models.structured_task.assert_not_called()
        self.assertEqual((self.core.project / target).read_bytes(), body)
        chapter = self.core.workflow.store.read()['chapters'][self.cid]
        self.assertEqual(chapter['canonical']['revision'], digest(body))
        sequence = self.core.workflow.store.read()['sequence']
        self.core.author_commit(self.core.recovery.preview(rows[0]['transaction_id'])['handle'], True)
        self.assertEqual(self.core.workflow.store.read()['sequence'], sequence)

    def body_hashes(self):
        return {p.relative_to(self.core.project).as_posix(): digest(p.read_bytes())
                for area in ('Canonical', 'Writer-Workspace', 'Editor-Workspace', 'Backups/Transactions')
                for p in (self.core.project / area).rglob('*') if p.is_file()}

    def legacy(self):
        # A real older publication with a directory mapping but no index intent.
        self.core.workflow.store.mutate('test_legacy', lambda s, t:s['chapters'][self.cid].pop('approval_transaction'))
        chapter = self.core.workflow.store.read()['chapters'][self.cid]
        with SafeTree(self.core.project) as tree, tree.lock():
            plan = admin._plan(tree, 'chapter_revision', self.core.document(chapter['draft']['document_id'])['relative'],
                               self.receipt['target'], b'# Legacy published synthetic\nNew approved bytes')
            receipt = admin._commit(tree, plan, plan[0]['approval_digest'])
        return receipt['transaction_id']

    def test_legacy_explicit_association_and_repeat_preserve_all_proof(self):
        txn = self.legacy()
        before = self.body_hashes()
        preview = self.core.recovery.legacy_preview(self.core.project, txn, self.cid)
        with patch.object(admin, '_commit', side_effect=AssertionError('body publication')):
            self.core.author_commit(preview['handle'], True)
        sequence = self.core.workflow.store.read()['sequence']
        self.core.author_commit(self.core.recovery.legacy_preview(self.core.project, txn, self.cid)['handle'], True)
        self.assertEqual(self.core.workflow.store.read()['sequence'], sequence)
        self.assertEqual(self.body_hashes(), before)

    def test_legacy_wrong_project_missing_proof_sha_and_cancel(self):
        txn = self.legacy()
        before = self.body_hashes()
        with self.assertRaises(CoreError):
            self.core.recovery.legacy_preview(self.core.project.parent, txn, self.cid)
        with self.assertRaises(CoreError):
            self.core.recovery.legacy_preview(self.core.project, txn, self.ids[1])
        with self.assertRaises((ValueError, OSError)):
            self.core.recovery.legacy_preview(self.core.project, 'f' * 32, self.cid)
        preview = self.core.recovery.legacy_preview(self.core.project, txn, self.cid)
        with self.assertRaises(CoreError):
            self.core.author_commit(preview['handle'], False)
        self.assertEqual(self.body_hashes(), before)
        (self.core.project / 'Backups/Transactions' / txn / 'after.bin').write_bytes(b'corrupt')
        with self.assertRaises(CoreError):
            self.core.recovery.legacy_preview(self.core.project, txn, self.cid)

    def test_legacy_interrupt_registration_and_resume(self):
        txn = self.legacy()
        preview = self.core.recovery.legacy_preview(self.core.project, txn, self.cid)
        def crash(name):
            if name == 'pending':
                raise OSError('synthetic registration interruption')
        with patch('novel_studio.canonical_recovery.checkpoint', crash), self.assertRaises(OSError):
            self.core.author_commit(preview['handle'], True)
        self.core = Core(self.core.project)
        before = self.body_hashes()
        self.core.author_commit(self.core.recovery.legacy_preview(self.core.project, txn, self.cid)['handle'], True)
        self.assertEqual(self.body_hashes(), before)

    def test_legacy_known_successor_or_aba_refused(self):
        txn = self.legacy()
        self.core.workflow.store.mutate('test_successor', lambda s, t:s['chapters'][self.cid].update(approval_transaction=self.txn))
        with self.assertRaises(CoreError):
            self.core.recovery.legacy_preview(self.core.project, txn, self.cid)

    def test_legacy_already_indexed_without_new_journal_is_explicitly_verified(self):
        txn = self.legacy()
        with SafeTree(self.core.project) as tree:
            receipt = json.loads(tree.read('Backups/Transactions/' + txn + '/COMMITTED.json'))
        self.core.workflow.store.mutate('test_old_index', lambda s,t:s['chapters'][self.cid].update(
            approval_transaction=txn, canonical={'document_id':digest(receipt['target'].encode())[:32], 'revision':receipt['after_sha256']}))
        sequence = self.core.workflow.store.read()['sequence']
        before = self.body_hashes()
        for _ in range(2):
            result = self.core.author_commit(self.core.recovery.legacy_preview(self.core.project, txn, self.cid)['handle'], True)
            self.assertEqual(result['state'], 'INDEX_ALREADY_LINKED')
        self.assertEqual(self.core.workflow.store.read()['sequence'], sequence)
        self.assertEqual(self.body_hashes(), before)

    def test_old_restore_manual_association_requires_exact_current_parent(self):
        # Execute the old, real author Restore publication without its index tail.
        plan = admin.restore(self.core.project, self.txn)
        old_restore = admin.restore(self.core.project, self.txn, True, plan['approval_digest'])
        before = self.body_hashes()
        preview = self.core.recovery.legacy_transactions_preview(self.core.project, [old_restore['transaction_id']], self.cid)
        with patch.object(admin, '_commit', side_effect=AssertionError('body publication')):
            self.core.author_commit(preview['handle'], True)
        chapter = self.core.workflow.store.read()['chapters'][self.cid]
        self.assertEqual(chapter['approval_transaction'], old_restore['transaction_id'])
        self.assertEqual(chapter['canonical']['revision'], self.receipt['before_sha256'])
        self.assertEqual(self.body_hashes(), before)

    def test_old_restore_wrong_parent_index_is_not_associated(self):
        plan = admin.restore(self.core.project, self.txn)
        old_restore = admin.restore(self.core.project, self.txn, True, plan['approval_digest'])
        self.core.workflow.store.mutate('test_aba_index', lambda s,t:s['chapters'][self.cid].update(approval_transaction='f' * 32))
        before = self.body_hashes()
        with self.assertRaises(CoreError):
            self.core.recovery.legacy_transactions_preview(self.core.project, [old_restore['transaction_id']], self.cid)
        self.assertEqual(self.body_hashes(), before)

    def test_restore_rejects_external_edit_stale_preview_and_parent_damage(self):
        preview = self.core.author_preview(transaction=self.txn)
        target = self.core.project / self.receipt['target']
        target.write_bytes(b'external body')
        with self.assertRaises((CoreError, ValueError)):
            self.core.author_commit(preview['handle'], True)
        self.assertEqual(target.read_bytes(), b'external body')
        (self.core.project / 'Backups/Transactions' / self.txn / 'COMMITTED.json').write_bytes(b'{')
        with self.assertRaises((CoreError, ValueError)):
            self.core.author_preview(transaction=self.txn)

    def test_restore_path_traversal_and_cross_project(self):
        for txn in ('../' + self.txn, self.txn + '/after.bin', 'Z' * 32):
            with self.subTest(txn=txn), self.assertRaises((ValueError, OSError)):
                self.core.author_preview(transaction=txn)
        path = self.core.project / 'Backups/Transactions' / self.txn / 'COMMITTED.json'
        receipt = json.loads(path.read_bytes())
        receipt['project'] = str(self.core.project.parent)
        path.write_bytes(admin.encode(receipt))
        with self.assertRaises(CoreError):
            self.core.author_preview(transaction=self.txn)

    def test_restore_capacity_and_lock_conflict_preserve_body(self):
        preview = self.core.author_preview(transaction=self.txn)
        before = self.body_hashes()
        with patch('novel_studio.canonical_recovery.shutil.disk_usage', return_value=type('Disk', (), {'free': 0})()), self.assertRaises(CoreError):
            self.core.author_commit(preview['handle'], True)
        self.assertEqual(self.body_hashes(), before)
        preview = self.core.author_preview(transaction=self.txn)
        with SafeTree(self.core.project) as tree, tree.lock(), self.assertRaises(CoreError):
            self.core.author_commit(preview['handle'], True)
        self.assertEqual(self.body_hashes(), before)

    def test_derived_partial_restart_exact_fields_and_idempotence(self):
        # Make an existing derived field stale, preserving all author settings.
        self.core.lengths.link(self.cid, self.core.workflow.store.read()['chapters'][self.cid]['draft']['document_id'])
        self.core._state(lambda s:s.update(version_order=[]))
        original_settings = self.core.creative.state()
        before = self.body_hashes()
        preview = self.core.recovery.derived_preview(self.cid)
        def crash(name):
            if name == 'derived_file':
                raise OSError('synthetic derived interruption')
        with patch('novel_studio.recovery_extensions.checkpoint', crash), self.assertRaises(OSError):
            self.core.author_commit(preview['handle'], True)
        self.core = Core(self.core.project)
        preview = self.core.recovery.derived_preview(self.cid)
        with patch.object(admin, '_commit', side_effect=AssertionError('body')):
            self.core.author_commit(preview['handle'], True)
            after = {p.relative_to(self.core.project).as_posix():digest(p.read_bytes()) for p in self.core.project.rglob('*') if p.is_file()}
            self.core.author_commit(self.core.recovery.derived_preview(self.cid)['handle'], True)
        self.assertEqual(self.body_hashes(), before)
        self.assertEqual(after, {p.relative_to(self.core.project).as_posix():digest(p.read_bytes()) for p in self.core.project.rglob('*') if p.is_file()})
        settings = self.core.creative.state()
        original_settings['length_plan']['nodes'][self.cid]['source'] = self.core.workflow.store.read()['chapters'][self.cid]['canonical']
        self.assertEqual(settings, original_settings)
        self.assertIn(self.txn, self.core._state()['version_order'])

    def test_derived_conflict_body_or_author_settings(self):
        preview = self.core.recovery.derived_preview(self.cid)
        self.core._state(lambda s:s.update(display_title='Author new title'))
        before = self.body_hashes()
        with self.assertRaises(CoreError):
            self.core.author_commit(preview['handle'], True)
        self.assertEqual(self.body_hashes(), before)
        (self.core.project / self.receipt['target']).write_bytes(b'external body')
        with self.assertRaises(CoreError):
            self.core.recovery.derived_preview(self.cid)

    def test_future_creative_format_is_not_downgraded(self):
        path = self.core.project / '.novel-studio/Creative/settings.json'
        settings = json.loads(path.read_bytes())
        settings['format'] = 'novel-studio-creative-future'
        path.write_bytes(admin.encode(settings))
        before = path.read_bytes()
        with self.assertRaises(CoreError):
            self.core.recovery.derived_preview(self.cid)
        self.assertEqual(path.read_bytes(), before)

    def test_derived_manifest_tampering_is_rejected(self):
        before = self.body_hashes()
        # Existing normal publication already has a completed plan. Force a
        # corrupted local manifest rather than treating local bytes as trusted.
        folder = self.core.project / '.novel-studio/Workflow/Recovery-Extensions/Derived' / self.txn
        pointer = json.loads((folder / 'CURRENT.json').read_bytes())
        folder = folder / pointer['job_id']
        plan = json.loads((folder / 'PLAN.json').read_bytes())
        plan['files']['../../Canonical/evil'] = plan['files'].pop('.novel-studio/Desktop.json')
        (folder / 'PLAN.json').write_bytes(admin.encode(plan))
        with self.assertRaises(CoreError):
            self.core.recovery.derived_preview(self.cid)
        self.assertEqual(self.body_hashes(), before)

    def test_derived_disk_failure_before_plan_preserves_body_and_can_retry(self):
        self.core._state(lambda s:s.update(version_order=[]))
        preview = self.core.recovery.derived_preview(self.cid)
        before = self.body_hashes()
        original = SafeTree.create
        def full(tree, path, payload):
            if '/Recovery-Extensions/' in path:
                raise OSError(28, 'synthetic full disk')
            return original(tree, path, payload)
        with patch.object(SafeTree, 'create', full), self.assertRaises(OSError):
            self.core.author_commit(preview['handle'], True)
        self.assertEqual(self.body_hashes(), before)
        self.core = Core(self.core.project)
        self.core.author_commit(self.core.recovery.derived_preview(self.cid)['handle'], True)
        self.assertIn(self.txn, self.core._state()['version_order'])

    def test_restore_two_windows_concurrent_recovery_exactly_once(self):
        preview = self.core.author_preview(transaction=self.txn)
        def crash(name):
            if name == 'body':
                raise OSError('synthetic')
        with patch('novel_studio.canonical_recovery.checkpoint', crash), self.assertRaises(OSError):
            self.core.author_commit(preview['handle'], True)
        row = self.core.recovery.diagnose()['operations'][0]
        other = Core(self.core.project)
        handles = [(c, c.recovery.preview(row['transaction_id'])['handle']) for c in (self.core, other)]
        before = self.body_hashes()
        sequence = self.core.workflow.store.read()['sequence']
        barrier, results = threading.Barrier(2), []
        def work(core, handle):
            barrier.wait(timeout=10)
            try:
                core.author_commit(handle, True)
                results.append('done')
            except (ValueError, OSError):
                results.append('conflict')
        workers = [threading.Thread(target=work, args=item) for item in handles]
        for worker in workers: worker.start()
        for worker in workers:
            worker.join(timeout=30)
            self.assertFalse(worker.is_alive(), 'project lock deadlock')
        self.assertEqual(sorted(results), ['conflict', 'done'])
        self.assertEqual(self.core.workflow.store.read()['sequence'], sequence + 1)
        self.assertEqual(self.body_hashes(), before)

    def test_startup_extended_diagnosis_is_read_only_bounded(self):
        self.core._state(lambda s:s.update(version_order=[]))
        before = {p.relative_to(self.core.project).as_posix():digest(p.read_bytes()) for p in self.core.project.rglob('*') if p.is_file()}
        with patch.object(SafeTree, 'files', side_effect=AssertionError('archive scan')), patch.object(SafeTree, 'create', side_effect=AssertionError('write')), patch.object(SafeTree, 'publish', side_effect=AssertionError('write')):
            result = Core(self.core.project).recovery.extended_diagnose(limit=1)
        self.assertEqual(len(result['derived']), 1)
        self.assertTrue(result['more'])
        self.assertEqual(before, {p.relative_to(self.core.project).as_posix():digest(p.read_bytes()) for p in self.core.project.rglob('*') if p.is_file()})

    def batch_fixture(self):
        fixture = chapter_fixture.ChapterAcceptanceTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def interrupt_batch(self, core, ids):
        preview = core.workflow.chapters.preview(ids)
        original = admin._commit
        def crash(tree, bundle, approval, **kw):
            original(tree, bundle, approval, **kw)
            raise OSError('synthetic after body')
        with patch.object(admin, '_commit', crash), self.assertRaises(OSError):
            core.author_commit(preview['handle'], True)
        return preview['batch_id']

    def old_batch(self, f, publish=2):
        preview = f.core.workflow.chapters.preview(f.ids)
        bundle = f.core._approvals[preview['handle']][1]
        bid = bundle['batch_id']
        def begin(state, tree):
            ref = f.core.workflow.store.put(tree, '.novel-studio/Workflow/Batches/' + bid + '.json', bundle)
            state['batches'][bid] = {**ref, 'status':'APPLYING', 'completed':[]}
        f.core.workflow.store.mutate('test_old_batch_original_protocol', begin)
        transactions = []
        with SafeTree(f.core.project) as tree, tree.lock():
            for item in bundle['items'][:publish]:
                plan = admin._plan(tree, 'chapter_accept', item['source'], item['plan']['target'], tree.read(item['source']), bid)
                transactions.append(admin._commit(tree, plan, item['plan']['approval_digest'])['transaction_id'])
        return bid, transactions

    def test_old_batch_manual_association_cancellation_repeat_and_derived(self):
        f = self.batch_fixture()
        bid, transactions = self.old_batch(f)
        with self.assertRaises(CoreError):
            f.core.workflow.chapters.recover_batch(bid, True)
        preview = f.core.recovery.legacy_transactions_preview(f.core.project, transactions, f.ids[0])
        with self.assertRaises(CoreError):
            f.core.author_commit(preview['handle'], False)
        sequence = f.core.workflow.store.read()['sequence']
        self.assertEqual(f.core.workflow.store.read()['batches'][bid]['status'], 'APPLYING')
        before = {p.relative_to(f.core.project).as_posix():digest(p.read_bytes()) for p in (f.core.project / 'Backups/Transactions').rglob('*') if p.is_file()}
        with patch.object(admin, '_commit', side_effect=AssertionError('publication')):
            result = f.core.author_commit(f.core.recovery.legacy_transactions_preview(f.core.project, transactions, f.ids[0])['handle'], True)
            self.assertEqual(result['remaining_need_new_author_preview'], 0)
            sequence = f.core.workflow.store.read()['sequence']
            f.core.author_commit(f.core.recovery.legacy_transactions_preview(f.core.project, transactions, f.ids[0])['handle'], True)
            self.assertEqual(f.core.workflow.store.read()['sequence'], sequence)
            f.core.author_commit(f.core.recovery.derived_preview(f.ids[0])['handle'], True)
        self.assertEqual(before, {p.relative_to(f.core.project).as_posix():digest(p.read_bytes()) for p in (f.core.project / 'Backups/Transactions').rglob('*') if p.is_file()})

    def test_old_batch_missing_selection_cross_project_and_external_conflict(self):
        f = self.batch_fixture()
        bid, transactions = self.old_batch(f)
        with self.assertRaises(CoreError):
            f.core.recovery.legacy_transactions_preview(f.core.project, transactions[:1], f.ids[0])
        with self.assertRaises(CoreError):
            f.core.recovery.legacy_transactions_preview(f.core.project.parent, transactions, f.ids[0])
        preview = f.core.recovery.legacy_transactions_preview(f.core.project, transactions, f.ids[0])
        target = f.core.project / 'Canonical/Final-Manuscript/Volume-001/Chapter-002.md'
        target.write_bytes(b'external synthetic')
        with self.assertRaises(CoreError):
            f.core.author_commit(preview['handle'], True)
        self.assertEqual(target.read_bytes(), b'external synthetic')

    def test_old_batch_association_interrupted_before_index_resumes(self):
        f = self.batch_fixture()
        bid, transactions = self.old_batch(f, publish=1)
        preview = f.core.recovery.legacy_transactions_preview(f.core.project, transactions, f.ids[0])
        def crash(name):
            if name == 'legacy_batch_associated':
                raise OSError('synthetic interruption')
        with patch('novel_studio.recovery_extensions.checkpoint', crash), self.assertRaises(OSError):
            f.core.author_commit(preview['handle'], True)
        f.core = Core(f.core.project)
        result = f.core.author_commit(f.core.recovery.legacy_transactions_preview(f.core.project, transactions, f.ids[0])['handle'], True)
        self.assertFalse(result['complete'])
        self.assertEqual(result['batches'][bid]['remaining_need_new_author_preview'], 1)

    def test_batch_exact_manifest_no_scan_duplicate_and_remaining_unpublished(self):
        f = self.batch_fixture()
        bid = self.interrupt_batch(f.core, f.ids)
        before = {p.relative_to(f.core.project).as_posix():digest(p.read_bytes()) for p in (f.core.project / 'Canonical').rglob('*') if p.is_file()}
        old_state = (f.core.project / '.novel-studio/Workflow/state.json').read_bytes()
        quota_before = json.loads((f.core.project / '.novel-studio/Workflow/Canonical-Recovery/Quota.json').read_bytes())['used']
        with patch.object(SafeTree, 'files', side_effect=AssertionError('full history scan')), patch.object(admin, '_commit', side_effect=AssertionError('republish')):
            result = f.core.workflow.chapters.recover_batch(bid, True)
            sequence = f.core.workflow.store.read()['sequence']
            f.core.workflow.chapters.recover_batch(bid, True)
        self.assertEqual(f.core.workflow.store.read()['sequence'], sequence)
        self.assertEqual((f.core.project / '.novel-studio/Workflow/History' / (digest(old_state) + '.json')).read_bytes(), old_state)
        quota_after = json.loads((f.core.project / '.novel-studio/Workflow/Canonical-Recovery/Quota.json').read_bytes())['used']
        self.assertGreaterEqual(quota_after - quota_before, len(old_state))
        self.assertEqual(result['remaining_need_new_author_preview'], 1)
        self.assertEqual(f.core.workflow.store.read()['batches'][bid]['status'], 'PARTIAL')
        self.assertEqual(before, {p.relative_to(f.core.project).as_posix():digest(p.read_bytes()) for p in (f.core.project / 'Canonical').rglob('*') if p.is_file()})

    def test_batch_missing_receipt_external_successor_and_capacity(self):
        f = self.batch_fixture()
        bid = self.interrupt_batch(f.core, [f.ids[0]])
        with self.assertRaises(CoreError):
            f.core.recovery.batches_preview([bid] * 17)
        record = json.loads((f.core.project / '.novel-studio/Workflow/Recovery-Extensions/Batches' / (bid + '.json')).read_bytes())
        txn = record['transactions'][f.ids[0]]
        path = f.core.project / 'Backups/Transactions' / txn / 'COMMITTED.json'
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(CoreError):
            f.core.workflow.chapters.recover_batch(bid, True)
        self.assertFalse(path.exists(), 'recovery must not fabricate a COMMITTED receipt')
        path.write_bytes(original)
        f.core.workflow.store.mutate('test_successor', lambda s,t:s['chapters'][f.ids[0]].update(title='successor context'))
        with self.assertRaises(CoreError):
            f.core.workflow.chapters.recover_batch(bid, True)

    def test_multiple_batches_restart_between_batches_and_order(self):
        f = self.batch_fixture()
        first = self.interrupt_batch(f.core, [f.ids[0]])
        f.core.workflow.chapters.recover_batch(first, True)
        second = self.interrupt_batch(f.core, [f.ids[1]])
        f.core = Core(f.core.project)
        preview = f.core.recovery.batches_preview([second, first])
        def crash(name):
            if name == 'between_batches':
                raise OSError('synthetic between batches')
        with patch('novel_studio.recovery_extensions.checkpoint', crash), self.assertRaises(OSError):
            f.core.author_commit(preview['handle'], True)
        f.core = Core(f.core.project)
        sequence = f.core.workflow.store.read()['sequence']
        result = f.core.author_commit(f.core.recovery.batches_preview([first, second])['handle'], True)
        self.assertTrue(result['complete'])
        self.assertEqual(f.core.workflow.store.read()['sequence'], sequence)

    def test_group_single_batch_failure_cannot_claim_complete(self):
        f = self.batch_fixture()
        first = self.interrupt_batch(f.core, [f.ids[0]])
        f.core.workflow.chapters.recover_batch(first, True)
        second = self.interrupt_batch(f.core, [f.ids[1]])
        path = f.core.project / '.novel-studio/Workflow/Recovery-Extensions/Batches' / (second + '.json')
        record = json.loads(path.read_bytes())
        (f.core.project / 'Backups/Transactions' / record['transactions'][f.ids[1]] / 'after.bin').write_bytes(b'corrupt')
        preview = f.core.recovery.batches_preview([first, second])
        with self.assertRaises(CoreError):
            f.core.author_commit(preview['handle'], True)
        self.assertEqual(f.core.workflow.store.read()['batches'][first]['status'], 'COMPLETED')
        self.assertEqual(f.core.workflow.store.read()['batches'][second]['status'], 'APPLYING')

    def test_group_final_check_rejects_change_to_an_earlier_member(self):
        f = self.batch_fixture()
        first = self.interrupt_batch(f.core, [f.ids[0]])
        f.core.workflow.chapters.recover_batch(first, True)
        second = self.interrupt_batch(f.core, [f.ids[1]])
        preview = f.core.recovery.batches_preview([first, second])
        changed = []
        def another_window(name):
            if name == 'between_batches' and not changed:
                changed.append(True)
                other = Core(f.core.project)
                other.workflow.store.mutate('test_concurrent_author', lambda s,t:s['chapters'][f.ids[0]].update(title='Later author choice'))
        with patch('novel_studio.recovery_extensions.checkpoint', another_window), self.assertRaises(CoreError):
            f.core.author_commit(preview['handle'], True)
        self.assertEqual(f.core.workflow.store.read()['chapters'][f.ids[0]]['title'], 'Later author choice')

    def test_group_500_items_bounded_and_600_refused_without_publication(self):
        f = self.batch_fixture()
        template_preview = f.core.workflow.chapters.preview([f.ids[0]])
        template = copy.deepcopy(f.core._approvals[template_preview['handle']][1]['items'][0])
        bids = []
        def setup(state, tree):
            prototype = state['chapters'][f.ids[0]]
            for group in range(6):
                bid = digest(('phase2-batch-' + str(group)).encode())[:32]
                bids.append(bid)
                items = []
                for n in range(group * 100 + 1, group * 100 + 101):
                    cid = digest(('phase2-item-' + str(n)).encode())[:32]
                    chapter = copy.deepcopy(prototype)
                    chapter.update(chapter_id=cid, number=n, ordinal=n)
                    state['chapters'][cid] = chapter
                    item = copy.deepcopy(template)
                    item.update(chapter_id=cid, number=n)
                    item['plan'] = admin._plan(tree, 'chapter_accept', item['source'], f'Canonical/Final-Manuscript/Volume-001/Chapter-{n:03d}.md', tree.read(item['source']), bid)[0]
                    items.append(item)
                bundle = {'batch_id': bid, 'outline_version': f.core._approvals[template_preview['handle']][1]['outline_version'], 'items': items}
                f.core.recovery.prepare_batch(tree, bundle, state)
                ref = f.core.workflow.store.put(tree, '.novel-studio/Workflow/Batches/' + bid + '.json', bundle)
                state['batches'][bid] = {**ref, 'status': 'APPLYING', 'completed': []}
        f.core.workflow.store.mutate('test_synthetic_batches', setup)
        with self.assertRaises(CoreError):
            f.core.recovery.batches_preview(bids)
        preview = f.core.recovery.batches_preview(bids[:5])
        with patch.object(admin, '_commit', side_effect=AssertionError('publication')):
            result = f.core.author_commit(preview['handle'], True)
        self.assertFalse(result['complete'])
        self.assertEqual(sum(r['remaining_need_new_author_preview'] for r in result['batches'].values()), 500)
        self.assertEqual(list((f.core.project / 'Canonical/Final-Manuscript').rglob('*.md')), [])

    def run_child(self, point, kill=False):
        script = self.core.project.parent / 'synthetic-phase2-child.py'
        marker = self.core.project.parent / 'checkpoint.marker'
        if marker.exists():
            marker.unlink()
        script.write_text('''import sys, os, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from novel_studio.core import Core, SafeTree
import novel_studio.canonical_recovery as recovery
core = Core(Path(sys.argv[2]))
def stop(name):
    if name == sys.argv[4]:
        Path(sys.argv[5]).write_text(name)
        if sys.argv[6] == 'kill':
            while True: time.sleep(.05)
        os._exit(23)
recovery.checkpoint = stop
original = SafeTree.create
def create(tree, name, payload):
    if name.endswith('/COMMITTED.json'): stop('before_committed')
    if name.replace(chr(92), '/').startswith('Canonical/') and name.endswith('.tmp') and sys.argv[4] == 'partial_stage':
        original(tree, name, payload[:max(1, len(payload)//2)])
        stop('partial_stage')
    return original(tree, name, payload)
SafeTree.create = create
core.author_commit(core.author_preview(transaction=sys.argv[3])['handle'], True)
''', encoding='utf-8')
        child = subprocess.Popen([sys.executable, '-B', str(script), str(ROOT / 'src'), str(self.core.project), self.txn, point, str(marker), 'kill' if kill else 'exit'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 30
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(marker.exists(), child.communicate(timeout=1) if child.poll() is not None else 'child checkpoint timeout')
            if kill:
                child.kill()  # Windows TerminateProcess, only this test-owned PID.
            output, errors = child.communicate(timeout=10)
            self.assertNotEqual(child.returncode, 0, errors.decode(errors='replace'))
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=10)
        self.core = Core(self.core.project)
        self.core.workflow.store.recover_locks(True)
        row = self.core.recovery.diagnose()['operations'][0]
        self.child_evidence = {'checkpoint':point, 'termination':'Windows TerminateProcess' if kill else 'os._exit(23)',
            'exit_code':child.returncode, 'restart_state':row['state'], 'transaction_id':row['transaction_id'],
            'chapter_id':row.get('chapter_id')}
        return row

    def test_real_process_exit_after_body_restart_zero_body_writes(self):
        row = self.run_child('body')
        self.assertEqual(row['state'], 'INDEX_PENDING')
        before = self.body_hashes()
        with patch.object(admin, '_commit', side_effect=AssertionError('republication')):
            self.core.author_commit(self.core.recovery.preview(row['transaction_id'])['handle'], True)
        self.assertEqual(self.body_hashes(), before)

    def test_real_process_exit_before_committed_is_conservative(self):
        row = self.run_child('before_committed')
        self.assertEqual(row['state'], 'PUBLISHED_NO_RECEIPT')
        self.assertFalse(row['can_recover'])

    def test_real_process_registration_and_partial_stage_boundaries(self):
        for point, expected in [('reserved', 'INCOMPLETE_UNPUBLISHED'), ('intent', 'ORPHAN_UNREGISTERED'),
                                ('pending', 'PENDING_UNREGISTERED'), ('registered', 'CURRENT_BEFORE'),
                                ('partial_stage', 'PUBLISHED_NO_RECEIPT')]:
            with self.subTest(point=point):
                row = self.run_child(point)
                self.assertEqual(row['state'], expected)
                self.assertFalse(row['can_recover'])
                if row['can_close']:
                    self.core.author_commit(self.core.recovery.preview(row['transaction_id'], close=True)['handle'], True)
                else:
                    self.assertEqual(digest((self.core.project / self.receipt['target']).read_bytes()), self.receipt['after_sha256'])

    @unittest.skipUnless(os.name == 'nt', 'Windows force termination is platform-specific')
    def test_windows_force_kill_after_index_and_restart_audit(self):
        row = self.run_child('index', kill=True)
        self.assertEqual(row['state'], 'INDEX_APPLIED_AUDIT_PENDING')
        before = self.body_hashes()
        sequence = self.core.workflow.store.read()['sequence']
        self.core.recovery.retry_audits()
        self.assertEqual(self.body_hashes(), before)
        self.assertEqual(self.core.workflow.store.read()['sequence'], sequence)


if __name__ == '__main__':
    unittest.main()
