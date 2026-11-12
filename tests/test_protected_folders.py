"""Portable host protection uses synthetic folders and no host/model requests."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from gui.service import LocalStore, Controller, SafeTree, admin
from novel_studio.core import Core


def payload(paths):
    return admin.encode({'format': 'novel-studio-protected-folders-v1', 'paths': list(map(str, paths))})


class ProtectedFoldersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='protected-folders-synthetic-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.bundle = self.base / 'bundle'
        self.bundle.mkdir()
        self.store = LocalStore(self.base / 'state')
        self.private = self.base / 'synthetic-private'

    def inherited(self, raw):
        folder = self.bundle / '.local'
        folder.mkdir(exist_ok=True)
        file = folder / 'host-protection.json'
        file.write_bytes(raw)
        return file

    def test_inherited_protection_is_captured_without_opening_private_folders(self):
        default = self.inherited(payload([self.private]))
        self.assertEqual(self.store.capture_project_protection(self.bundle), [self.private])
        default.unlink()
        self.assertEqual(self.store.protection_config(self.bundle)['paths'], [self.private])
        self.assertFalse(self.private.exists())

    def test_default_ui_recovery_preserves_separate_protection(self):
        config = self.store.protection_config(self.bundle)
        self.store.add_protected_folders(self.bundle, [str(self.private)], config['revision'])
        protection = self.store.root / 'protected-folders.json'
        before = protection.read_bytes()
        (self.store.root / 'center.json').write_bytes(b'{"layout":')
        self.store.prepare(lambda issue: 'defaults')
        self.assertEqual(protection.read_bytes(), before)
        self.assertEqual(self.store.protection_config(self.bundle)['paths'], [self.private])

    def test_additive_save_preserves_bytes_and_rejects_stale_dialog(self):
        initial = self.store.protection_config(self.bundle)
        self.store.add_protected_folders(self.bundle, [str(self.private)], initial['revision'])
        path = self.store.root / 'protected-folders.json'
        old = path.read_bytes()
        current = self.store.protection_config(self.bundle)
        second = self.base / 'second-protected'
        self.store.add_protected_folders(self.bundle, [str(second)], current['revision'])
        before_conflict = path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.add_protected_folders(self.bundle, [], current['revision'])
        self.assertEqual(path.read_bytes(), before_conflict)
        backup = next((self.store.root / 'Protected-Folders-History').glob('*.json'))
        self.assertEqual(backup.read_bytes(), old)
        self.assertEqual(self.store.protection_config(self.bundle)['paths'], [self.private, second])

    def test_invalid_or_future_protection_cannot_be_silently_dropped(self):
        invalid = [b'{', b'[]', b'{"format":"future","paths":[]}',
                   b'{"format":"novel-studio-protected-folders-v1","paths":[],"paths":[]}',
                   b'{"format":"novel-studio-protected-folders-v1","paths":[],"unknown":"value"}',
                   payload(['relative']), payload([self.base / '..' / 'escape']),
                   payload([None]), payload(['x\x00y']), payload([self.private] * 65)]
        controller = Controller(self.bundle, self.store.root)
        self.store.save({'plugin': str(ROOT)})
        target = self.store.root / 'protected-folders.json'
        for raw in invalid:
            with self.subTest(raw=raw):
                target.write_bytes(raw)
                with self.assertRaises(ValueError):
                    controller.new_project(self.base, 'must-not-create')
                self.assertEqual(target.read_bytes(), raw)
                self.assertFalse((self.base / 'must-not-create').exists())

    def test_old_book_profiles_stay_exact_and_new_books_receive_added_denies(self):
        self.inherited(payload([self.private]))
        controller = Controller(self.bundle, self.store.root)
        self.store.save({'plugin': str(ROOT)})
        controller.new_project(self.base, 'old-synthetic-book')
        old = Core(controller.project)
        before = {p.relative_to(old.project).as_posix(): p.read_bytes() for p in old.project.rglob('*') if p.is_file()}
        self.assertTrue(old.validate_permissions()['legacy_profiles_match'])
        current = self.store.protection_config(self.bundle)
        second = self.base / 'second-protected'
        self.store.add_protected_folders(self.bundle, [str(second)], current['revision'])
        self.assertTrue(Core(old.project).validate_permissions()['legacy_profiles_match'])
        self.assertEqual(before, {p.relative_to(old.project).as_posix(): p.read_bytes() for p in old.project.rglob('*') if p.is_file()})
        controller.new_project(self.base, 'new-synthetic-book')
        newer = Core(controller.project)
        self.assertIn(str(self.private), newer.metadata['private_roots'])
        self.assertIn(str(second), newer.metadata['private_roots'])
        for role in ('writer', 'editor'):
            with SafeTree(newer.project) as tree:
                profile = json.loads(tree.read(f'.novel-studio/Sessions/{role}.json'))
            self.assertFalse(profile['allowNonWorkspaceAccess'])
            self.assertIn(f'read_file({self.private.as_posix()})', profile['permissions']['deny'])
            self.assertIn(f'read_file({second.as_posix()})', profile['permissions']['deny'])

    def test_changed_inherited_configuration_requires_fresh_confirmation(self):
        file = self.inherited(payload([self.private]))
        viewed = self.store.protection_config(self.bundle)
        file.write_bytes(payload([self.base / 'changed-private']))
        with self.assertRaises(ValueError):
            self.store.add_protected_folders(self.bundle, [], viewed['revision'])
        self.assertFalse((self.store.root / 'protected-folders.json').exists())

    def test_added_root_cannot_contain_the_application_or_its_settings(self):
        initial = self.store.protection_config(self.bundle)
        for folder in (self.base, self.bundle, self.store.root):
            with self.subTest(folder=folder), self.assertRaises(ValueError):
                self.store.add_protected_folders(self.bundle, [str(folder)], initial['revision'])
        self.assertFalse((self.store.root / 'protected-folders.json').exists())

    def test_save_failure_keeps_original_and_verified_history(self):
        initial = self.store.protection_config(self.bundle)
        self.store.add_protected_folders(self.bundle, [str(self.private)], initial['revision'])
        path = self.store.root / 'protected-folders.json'
        before = path.read_bytes()
        current = self.store.protection_config(self.bundle)
        original = SafeTree.publish
        def interrupted(tree, name, raw, expected):
            if name == 'protected-folders.json':
                raise OSError('synthetic before publication')
            return original(tree, name, raw, expected)
        with patch.object(SafeTree, 'publish', new=interrupted), self.assertRaises(OSError):
            self.store.add_protected_folders(self.bundle, [str(self.base / 'extra')], current['revision'])
        self.assertEqual(path.read_bytes(), before)
        backup = next((self.store.root / 'Protected-Folders-History').glob('*.json'))
        self.assertEqual(backup.read_bytes(), before)
        self.assertEqual(LocalStore.protection_paths(backup.read_bytes()), [self.private])


if __name__ == '__main__':
    unittest.main()
