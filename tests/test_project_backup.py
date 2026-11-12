"""B03 synthetic project/ZIP fault injection; no private state or model calls."""
import errno
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from novel_studio.core import Core, CoreError, SafeTree, digest, admin
from novel_studio import project_backup as service


class ProjectBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='b03-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.core = Core.create(self.base, 'B03 synthetic book', ROOT)
        self.target = self.base / '完整备份.zip'
        self.intent = '.novel-studio/Workflow/Canonical-Recovery/.ns-' + 'a' * 32 + '-INTENT.json'
        with SafeTree(self.core.project) as tree:
            for name, raw in {
                'Canonical/Final-Manuscript/第一章.md': '已批准的合成正文。'.encode(),
                'Writer-Workspace/Drafts/第一章.md': '合成草稿。'.encode(),
                'Canonical/Canon-Characters/人物.md': '合成人物。'.encode(),
                'Backups/Checkpoints/history.bin': b'synthetic old version',
                'Writer-Workspace/Drafts/empty.md': b'',
            }.items():
                tree.create(name, raw)
            tree.mkdir(str(Path(self.intent).parent))
            tree.create(self.intent, b'{"synthetic":"B02 immutable evidence"}')
            tree.mkdir('Writer-Workspace/Drafts/deep/一/二/三/四/empty-directory')
            tree.create('Writer-Workspace/Drafts/deep/一/二/三/四/text.md', b'deep synthetic bytes')

    def inventory(self):
        return {p.relative_to(self.core.project).as_posix():digest(p.read_bytes())
                for p in self.core.project.rglob('*') if p.is_file()}

    def test_original_partial_write_fault_never_leaves_a_formal_zip(self):
        before = self.inventory()
        original = SafeTree.create
        def interrupted(tree, name, payload):
            if tree.root == self.target.parent and payload.startswith(b'PK'):
                original(tree, name, payload[:31])
                raise OSError(errno.ENOSPC, 'synthetic disk full after 31 bytes')
            return original(tree, name, payload)
        with patch.object(SafeTree, 'create', interrupted), self.assertRaises((OSError, CoreError)):
            service.backup(self.core, self.target)
        self.assertEqual(self.inventory(), before)
        self.assertFalse(self.target.exists(), 'an incomplete ZIP was published under the final name')
        service.backup(self.core, self.target)
        with zipfile.ZipFile(self.target) as archive:
            self.assertIsNone(archive.testzip())

    def test_backup_keeps_persistent_ns_evidence(self):
        before = self.inventory()
        service.backup(self.core, self.target)
        with zipfile.ZipFile(self.target) as archive:
            self.assertIn(self.intent, archive.namelist(), 'B02 recovery evidence was silently excluded')
        self.assertEqual(self.inventory(), before)

    def test_backup_keeps_empty_directories(self):
        service.backup(self.core, self.target)
        with zipfile.ZipFile(self.target) as archive:
            self.assertIn('Writer-Workspace/Drafts/deep/一/二/三/四/empty-directory/', archive.namelist())

    def restore_copy(self, payload, target):
        # Test-only reference roundtrip, not a product ZIP restore/import API.
        checked = service.verify(payload)
        target.mkdir(exist_ok=False)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive, SafeTree(target) as output:
            for name in checked['directories']:
                output.mkdir(name[:-1])
            for name in checked['files']:
                if Path(name).parent.as_posix() != '.':
                    output.mkdir(Path(name).parent.as_posix())
                output.create(name, archive.read(name))
        return {p.relative_to(target).as_posix():digest(p.read_bytes()) for p in target.rglob('*') if p.is_file()}

    def packed(self, files=None, manifest=None, extra=(), compression=zipfile.ZIP_STORED):
        files = files if files is not None else {'deep/中文.md': b'synthetic bytes', 'empty': b''}
        manifest = manifest if manifest is not None else {'format':service.FORMAT, 'files':{n:digest(raw) for n,raw in files.items()}}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression) as archive:
            for name, raw in files.items():
                archive.writestr(name, raw)
            for name, raw in extra:
                archive.writestr(name, raw)
            archive.writestr(service.MANIFEST, manifest if isinstance(manifest, bytes) else json.dumps(manifest).encode())
        return stream.getvalue()

    def test_full_roundtrip_includes_chinese_empty_deep_large_and_many_files(self):
        with SafeTree(self.core.project) as tree:
            tree.create('Writer-Workspace/Drafts/large.bin', bytes(range(256)) * 65536)
            for index in range(120):
                tree.create('Writer-Workspace/Drafts/file-' + str(index) + '.md', str(index).encode())
        before = self.inventory()
        dirs = {p.relative_to(self.core.project).as_posix() for p in self.core.project.rglob('*') if p.is_dir()}
        result = service.backup(self.core, self.target)
        payload = self.target.read_bytes()
        self.assertEqual(result['sha256'], digest(payload))
        self.assertEqual(result['files'], len(before))
        restored = self.base / 'fresh-restored-copy'
        self.assertEqual(self.restore_copy(payload, restored), before)
        self.assertEqual({p.relative_to(restored).as_posix() for p in restored.rglob('*') if p.is_dir()}, dirs)
        self.assertEqual(self.inventory(), before)

    def test_existing_v1_without_directory_entries_is_still_valid(self):
        payload = self.packed()
        result = service.verify(payload)
        self.assertEqual(result['directories'], [])
        self.assertEqual(self.restore_copy(payload, self.base / 'legacy-copy'), result['files'])

    def test_zip_crc_corruption_is_rejected(self):
        payload = bytearray(self.packed())
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            info = archive.infolist()[0]
            start = info.header_offset
            size = int.from_bytes(payload[start+26:start+28], 'little')
            extra = int.from_bytes(payload[start+28:start+30], 'little')
            payload[start + 30 + size + extra] ^= 1
        with self.assertRaises(CoreError):
            service.verify(bytes(payload))

    def test_deflate_stream_corruption_is_reported_as_invalid_zip(self):
        payload = bytearray(self.packed(compression=zipfile.ZIP_DEFLATED))
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            start = archive.infolist()[0].header_offset
            size = int.from_bytes(payload[start+26:start+28], 'little')
            extra = int.from_bytes(payload[start+28:start+30], 'little')
            payload[start + 30 + size + extra] = 255  # Reserved DEFLATE block type.
        with self.assertRaises(CoreError) as caught:
            service.verify(bytes(payload))
        self.assertEqual(caught.exception.code,'BACKUP_INVALID')

    def test_missing_extra_and_sha_mismatched_files_are_rejected(self):
        manifests = [
            {'format':service.FORMAT,'files':{}},
            {'format':service.FORMAT,'files':{'absent.md':digest(b'absent')}},
            {'format':service.FORMAT,'files':{'deep/中文.md':'0'*64,'empty':digest(b'')}},
            {'format':service.FORMAT,'files':{'deep/中文.md':12,'empty':digest(b'')}},
        ]
        for manifest in manifests:
            with self.subTest(manifest=manifest), self.assertRaises(CoreError):
                service.verify(self.packed(manifest=manifest))

    def test_broken_manifest_duplicate_keys_and_future_versions_are_rejected(self):
        for manifest in (b'{', b'[]', b'{"format":"x","format":"y","files":{}}',
                         {'format':'novel-studio-backup-copy-v99','files':{}}, {'format':service.FORMAT,'files':[]}):
            with self.subTest(manifest=manifest), self.assertRaises(CoreError):
                service.verify(self.packed(manifest=manifest))

    def test_manifest_missing_or_repeated_is_rejected(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            archive.writestr('only.md',b'synthetic bytes')
        with self.assertRaises(CoreError):
            service.verify(stream.getvalue())
        with self.assertRaises(CoreError):
            service.verify(self.packed(extra=((service.MANIFEST,b'{}'),)))

    def test_truncated_and_non_zip_inputs_are_rejected_without_writes(self):
        before = self.inventory()
        for payload in (b'', b'not zip', self.packed()[:31]):
            with self.subTest(size=len(payload)), self.assertRaises(CoreError):
                service.verify(payload)
        self.assertEqual(self.inventory(), before)

    def test_validator_rejects_paths_case_aliases_duplicates_and_links(self):
        names = ('../escape', '/absolute', 'C:/absolute', 'back\\slash', 'a//b', 'a/./b', 'a/../b', 'NUL.txt')
        for name in names:
            with self.subTest(name=name), self.assertRaises(CoreError):
                service.verify(self.packed(files={name:b'x'}))
        for entries in [({'A':b'x','a':b'x'},()), ({'a':b'x','a/b':b'x'},()),
                        ({'a':b'x'},(('a',b'x'),))]:
            with self.subTest(entries=entries), self.assertRaises(CoreError):
                service.verify(self.packed(files=entries[0],extra=entries[1]))
        link = zipfile.ZipInfo('link')
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        with self.assertRaises(CoreError):
            service.verify(self.packed(extra=((link,b'synthetic-target'),)))

    def test_duplicate_target_never_changes_existing_backup(self):
        before = self.inventory()
        service.backup(self.core, self.target)
        original = self.target.read_bytes()
        with self.assertRaises(CoreError) as caught:
            service.backup(self.core, self.target)
        self.assertEqual(caught.exception.code, 'BACKUP_EXISTS')
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(self.inventory(), before)

    def test_repeated_distinct_backups_keep_all_versions(self):
        service.backup(self.core, self.target)
        old = self.target.read_bytes()
        later = self.base / 'second.zip'
        service.backup(self.core, later)
        self.assertEqual(self.target.read_bytes(), old)
        self.assertEqual(service.verify(old)['files'], service.verify(later.read_bytes())['files'])

    def test_source_read_permission_and_disk_errors_keep_source_and_old_backup(self):
        service.backup(self.core, self.target)
        old = self.target.read_bytes()
        before = self.inventory()
        original = SafeTree.read
        for error in (PermissionError(errno.EACCES,'synthetic denied'), OSError(errno.EIO,'synthetic read failure')):
            def denied(tree, name):
                if name == 'Writer-Workspace/Drafts/第一章.md':
                    raise error
                return original(tree, name)
            with patch.object(SafeTree, 'read', denied), self.assertRaises(OSError):
                service.backup(self.core, self.base / 'failed-read.zip')
            self.assertEqual(self.inventory(), before)
            self.assertEqual(self.target.read_bytes(), old)
            self.assertFalse((self.base / 'failed-read.zip').exists())

    def test_in_memory_zip_write_failure_never_publishes(self):
        before = self.inventory()
        original = zipfile.ZipFile.writestr
        def fail(archive, name, raw, *args, **kwargs):
            original(archive, name, raw, *args, **kwargs)
            raise OSError(errno.ENOSPC,'synthetic ZIP write failure')
        with patch.object(zipfile.ZipFile, 'writestr', fail), self.assertRaises(OSError):
            service.backup(self.core, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.inventory(), before)

    def test_fsync_failure_preserves_complete_unpublished_stage_and_old_zip(self):
        service.backup(self.core, self.target)
        old = self.target.read_bytes()
        before = self.inventory()
        original = os.fsync
        def fail(fd):
            if os.fstat(fd).st_size > 100:
                raise OSError(errno.ENOSPC,'synthetic fsync failure')
            return original(fd)
        with patch('safe_project.os.fsync', fail), self.assertRaises(CoreError):
            service.backup(self.core, self.base / 'failed-fsync.zip')
        self.assertFalse((self.base / 'failed-fsync.zip').exists())
        self.assertTrue(list(self.base.glob('.ns-*.tmp')))
        self.assertEqual(self.target.read_bytes(), old)
        self.assertEqual(self.inventory(), before)

    def test_silent_stage_corruption_is_detected_before_publication(self):
        before = self.inventory()
        original = SafeTree.create
        def corrupt(tree, name, payload):
            if tree.root == self.target.parent and payload.startswith(b'PK'):
                payload = payload[:-1] + bytes([payload[-1] ^ 1])
            return original(tree, name, payload)
        with patch.object(SafeTree, 'create', corrupt), self.assertRaises(CoreError):
            service.backup(self.core, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.inventory(), before)

    def test_source_changes_during_staging_are_detected_without_rollback(self):
        original = SafeTree.create
        path = self.core.project / 'Writer-Workspace/Drafts/第一章.md'
        changed = b'changed externally during ZIP write'
        def mutate(tree, name, payload):
            original(tree, name, payload)
            if tree.root == self.target.parent and payload.startswith(b'PK'):
                path.write_bytes(changed)
        before = self.inventory()
        with patch.object(SafeTree, 'create', mutate), self.assertRaises(CoreError) as caught:
            service.backup(self.core, self.target)
        self.assertEqual(caught.exception.code, 'REVISION_CONFLICT')
        self.assertFalse(self.target.exists())
        self.assertEqual(self.inventory(), {**before,'Writer-Workspace/Drafts/第一章.md':digest(changed)})

    def test_new_file_or_empty_directory_during_staging_prevents_publication(self):
        original = SafeTree.create
        for name, directory in [('new.md',False),('new-empty',True)]:
            path = self.core.project / name
            def mutate(tree, relative, payload):
                original(tree, relative, payload)
                if tree.root == self.target.parent and payload.startswith(b'PK'):
                    path.mkdir() if directory else path.write_bytes(b'external addition')
            with patch.object(SafeTree, 'create', mutate), self.assertRaises(CoreError):
                service.backup(self.core, self.target)
            self.assertFalse(self.target.exists())

    def test_same_name_race_never_overwrites_other_publishers_zip(self):
        old = self.packed()
        original = SafeTree.create
        def competitor(tree, name, payload):
            original(tree, name, payload)
            if tree.root == self.target.parent and payload.startswith(b'PK'):
                self.target.write_bytes(old)
        with patch.object(SafeTree,'create',competitor), self.assertRaises(CoreError) as caught:
            service.backup(self.core,self.target)
        self.assertEqual(caught.exception.code,'BACKUP_EXISTS')
        self.assertEqual(self.target.read_bytes(),old)

    def test_simultaneous_backups_same_project_and_role_turns_are_exclusive(self):
        other = Core(self.core.project)
        before = self.inventory()
        entered, release = threading.Event(), threading.Event()
        original = SafeTree.create
        def pause(tree, name, payload):
            original(tree,name,payload)
            if tree.root == self.target.parent and payload.startswith(b'PK'):
                entered.set()
                if not release.wait(10):
                    raise RuntimeError('test timed out')
        with ThreadPoolExecutor(max_workers=1) as pool, patch.object(SafeTree,'create',pause):
            future = pool.submit(service.backup,self.core,self.target)
            try:
                self.assertTrue(entered.wait(10))
                with self.assertRaises((FileExistsError,CoreError)) as caught:
                    service.backup(other,self.base/'other-window.zip')
                if isinstance(caught.exception,CoreError):
                    self.assertEqual(caught.exception.code,'BUSY')
                for role in ('writer','editor'):
                    with self.assertRaises(FileExistsError):
                        with admin.role_turn(self.core.project,role):
                            self.fail('role turn entered during backup')
            finally:
                release.set()
            future.result(10)
        self.assertEqual(self.inventory(),before)
        self.assertFalse((self.base/'other-window.zip').exists())

    def test_two_projects_publishing_same_target_have_exactly_one_winner(self):
        other = Core.create(self.base,'B03 second synthetic book',ROOT)
        other_before = {p.relative_to(other.project).as_posix():digest(p.read_bytes()) for p in other.project.rglob('*') if p.is_file()}
        before = self.inventory()
        barrier = threading.Barrier(2)
        original = SafeTree.create
        def pause(tree,name,payload):
            original(tree,name,payload)
            if tree.root == self.target.parent and payload.startswith(b'PK'):
                barrier.wait(10)
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(SafeTree,'create',pause):
            futures = [pool.submit(service.backup,core,self.target) for core in (self.core,other)]
            results = []
            for future in futures:
                try:
                    future.result(15)
                    results.append('saved')
                except CoreError as error:
                    self.assertEqual(error.code,'BACKUP_EXISTS')
                    results.append('exists')
        self.assertCountEqual(results,['saved','exists'])
        self.assertIn(service.verify(self.target.read_bytes())['files'],[before,other_before])
        self.assertEqual(self.inventory(),before)

    def test_active_role_refuses_backup_without_touching_its_lock(self):
        before=self.inventory()
        with admin.role_turn(self.core.project,'writer'):
            active=self.inventory()
            with self.assertRaises(CoreError) as caught:
                service.backup(self.core,self.target)
            self.assertEqual(caught.exception.code,'BUSY')
            self.assertEqual(self.inventory(),active)
        self.assertEqual(self.inventory(),before)
        self.assertFalse(self.target.exists())

    def test_capacity_refusal_never_removes_only_valid_backup(self):
        service.backup(self.core,self.target)
        old = self.target.read_bytes()
        before = self.inventory()
        for setting, value in [('LIMIT',64),('ENTRIES',8)]:
            with patch.object(service,setting,value), self.assertRaises(CoreError):
                service.backup(self.core,self.base/'over-capacity.zip')
            self.assertFalse((self.base/'over-capacity.zip').exists())
            self.assertEqual(self.target.read_bytes(),old)
            self.assertEqual(self.inventory(),before)

    def test_individual_file_guard_is_not_raised_for_backups(self):
        path = self.core.project/'Writer-Workspace/Drafts/too-large.bin'
        path.write_bytes(b'x'*(32*1024*1024+1))
        before = self.inventory()
        with self.assertRaises(ValueError):
            service.backup(self.core,self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.inventory(),before)

    def test_reserved_manifest_collision_is_rejected_without_omission(self):
        (self.core.project/service.MANIFEST).write_bytes(b'synthetic user file; must not be omitted')
        before = self.inventory()
        with self.assertRaises(CoreError):
            service.backup(self.core,self.target)
        self.assertEqual(self.inventory(),before)
        self.assertFalse(self.target.exists())

    def test_output_path_boundaries_are_retained(self):
        for target in (Path('relative.zip'),self.core.project/'inside.zip',ROOT/'inside.zip',self.base/'wrong.txt'):
            with self.subTest(target=target), self.assertRaises(CoreError):
                service.backup(self.core,target)

    def test_process_exit_during_write_leaves_no_formal_zip_and_keeps_old_backup(self):
        service.backup(self.core,self.target)
        old = self.target.read_bytes()
        before = self.inventory()
        target = self.base/'crashed.zip'
        program = '''
import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from novel_studio.core import Core,SafeTree
from novel_studio.project_backup import backup
original=SafeTree.create
target=Path(sys.argv[3])
def crash(tree,name,payload):
    if tree.root==target.parent and payload.startswith(b'PK'):
        original(tree,name,payload[:31])
        os._exit(73)
    return original(tree,name,payload)
SafeTree.create=crash
backup(Core(Path(sys.argv[2])),target)
'''
        result=subprocess.run([sys.executable,'-B','-c',program,str(ROOT/'src'),str(self.core.project),str(target)],capture_output=True,timeout=30)
        self.assertEqual(result.returncode,73,result.stderr)
        self.assertFalse(target.exists())
        stages=list(self.base.glob('.ns-*.tmp'))
        self.assertEqual(len(stages),1)
        self.assertEqual(stages[0].stat().st_size,31)
        after=self.inventory()
        self.assertEqual({name:after[name] for name in before},before)
        self.assertEqual(set(after)-set(before),service.LOCKS)
        self.assertEqual(self.target.read_bytes(),old)
        self.assertEqual(service.verify(old)['files'],before)
        with self.assertRaises(CoreError):
            service.verify(stages[0].read_bytes())
        # Reuse the existing explicit, PID-verified crash-lock recovery. Backup
        # must not leave unrecognized role locks requiring a new recovery format.
        reopened=Core(self.core.project)
        removed=reopened.workflow.store.recover_locks(True)
        self.assertEqual(removed,['.ns-author.lock'])
        recovered_before=self.inventory()
        resumed=self.base/'resumed-after-confirmed-lock-recovery.zip'
        service.backup(reopened,resumed)
        self.assertEqual(service.verify(resumed.read_bytes())['files'],recovered_before)
        self.assertEqual(self.inventory(),recovered_before)

    def test_reopened_project_and_zip_keep_complete_inventory(self):
        before=self.inventory()
        service.backup(self.core,self.target)
        reopened=Core(self.core.project)
        second=self.base/'after-restart.zip'
        service.backup(reopened,second)
        self.assertEqual(service.verify(second.read_bytes())['files'],before)
        self.assertEqual(self.restore_copy(second.read_bytes(),self.base/'restart-copy'),before)
