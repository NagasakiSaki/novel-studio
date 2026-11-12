"""B07 Qt offscreen and legacy startup adapters; synthetic roots only."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tests')]
from gui.service import LocalStore, LocalConfigIssue, ConfigStartupCancelled, SafeTree, digest
try:
    from PySide6.QtWidgets import QApplication, QDialog
    from novel_studio.desktop.window import Workbench
    from novel_studio.desktop.presenter import Presenter
    from novel_studio.desktop.local_config_ui import ConfigRecoveryDialog
    from ui2_fixtures import LocalOnlyCLI
    QT = True
except ImportError:
    QT = False


def tk_entry():
    loader = importlib.machinery.SourceFileLoader('b07_legacy_tk', str(ROOT / 'gui/app.pyw'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@unittest.skipUnless(QT, 'Qt developer environment not installed')
class ConfigRecoveryUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='b07-ui-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = LocalStore(self.base / 'state')
        self.path = self.store.root / 'center.json'
        self.window = Workbench()
        self.addCleanup(self.window.deleteLater)

    def issue(self, raw=b'{"layout":'):
        self.path.write_bytes(raw)
        try:
            self.store.load()
        except LocalConfigIssue as error:
            self.store.backup(error)
            return error
        self.fail('expected malformed state')

    def test_dialog_explains_backup_and_default_start_without_secret_values(self):
        raw = b'{"api_key":"SYNTHETIC-SECRET", "project":"D:/private/fiction", "layout":'
        issue = self.issue(raw)
        dialog = ConfigRecoveryDialog(issue, self.window)
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.defaults.isEnabled())
        self.assertIn('center.json', dialog.description.text())
        self.assertIn(issue.sha256, dialog.description.text())
        self.assertIn(issue.backup, dialog.description.text())
        self.assertNotIn('SYNTHETIC-SECRET', dialog.description.text() + dialog.diagnostic.toPlainText())
        self.assertNotIn('D:/private/fiction', dialog.description.text() + dialog.diagnostic.toPlainText())
        dialog.defaults.click()
        self.assertEqual(dialog.choice, 'defaults')

    def test_permission_and_future_version_have_no_default_reset_button(self):
        for kind in ('permission', 'io', 'future', 'unsafe'):
            with self.subTest(kind=kind):
                dialog = ConfigRecoveryDialog(LocalConfigIssue(kind), self.window)
                self.addCleanup(dialog.deleteLater)
                self.assertFalse(dialog.defaults.isEnabled())
                self.assertIn('不能使用默认设置覆盖', dialog.description.text())
                dialog.retry.click()
                self.assertEqual(dialog.choice, 'retry')

    def test_presenter_recovery_shows_window_without_opening_or_creating_book(self):
        from novel_studio.core import Core
        core = Core.create(self.base, 'B07 synthetic book', ROOT)
        with SafeTree(core.project) as tree:
            for name in ('Writer-Workspace/Drafts/chapter.md', 'Canonical/Canon-Characters/person.md',
                         'Canonical/Final-Manuscript/old.md', 'Backups/Checkpoints/history.bin'):
                tree.create(name, b'synthetic immutable book bytes')
            tree.mkdir('.novel-studio/Workflow/Canonical-Recovery')
            tree.create('.novel-studio/Workflow/Canonical-Recovery/B02-opaque.bin', b'B02 bytes stay untouched')
        before = {p.relative_to(core.project).as_posix():digest(p.read_bytes()) for p in core.project.rglob('*') if p.is_file()}
        raw = ('{"project":' + json.dumps(str(core.project)) + ',"layout":').encode()
        self.path.write_bytes(raw)
        with patch.object(Presenter, 'open_project', side_effect=AssertionError('auto-opened book')):
            p = Presenter(self.window, self.store.root, LocalOnlyCLI(), config_recovery=lambda issue:'defaults')
        self.assertIsNone(p.core)
        self.assertTrue(p.store.safe_start)
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        after = {v.relative_to(core.project).as_posix():digest(v.read_bytes()) for v in core.project.rglob('*') if v.is_file()}
        self.assertEqual(before, after)
        self.assertEqual({d.name for d in self.base.iterdir() if d.is_dir()}, {'state', 'B07 synthetic book'})
        self.assertEqual(next((self.store.root / 'Config-Recovery').glob('*.bin')).read_bytes(), raw)
        p.workflow_timer.stop()
        p.recovery_timer.stop()

    def test_presenter_cancel_is_explicit_and_preserves_source(self):
        raw = b'{"layout":'
        self.path.write_bytes(raw)
        with self.assertRaises(ConfigStartupCancelled):
            Presenter(self.window, self.store.root, LocalOnlyCLI(), config_recovery=lambda issue:'exit')
        self.assertEqual(self.path.read_bytes(), raw)

    def test_protected_folders_use_visible_selection_and_save_only_after_acceptance(self):
        from PySide6.QtWidgets import QPushButton
        from novel_studio.desktop.dialogs import InformationDialog
        from novel_studio.desktop.local_config_ui import ProtectedFoldersDialog
        bundle = self.base / 'synthetic-program'
        bundle.mkdir()
        private = self.base / 'synthetic-private'
        presenter = Presenter(self.window, self.store.root, LocalOnlyCLI())

        def show_settings(dialog):
            button = next(item for item in dialog.findChildren(QPushButton) if item.text() == '保护其他文件夹')
            button.click()
            return QDialog.Rejected

        for accepted in (False, True):
            def choose(dialog):
                with patch('novel_studio.desktop.local_config_ui.QFileDialog.getExistingDirectory', return_value=str(private)):
                    dialog.add_folder.click()
                dialog.accept() if accepted else dialog.reject()
                return dialog.result()
            with patch('novel_studio.desktop.presenter.ROOT', bundle), \
                 patch.object(InformationDialog, 'exec', new=show_settings), \
                 patch.object(ProtectedFoldersDialog, 'exec', new=choose):
                presenter.diagnostics()
            self.assertEqual(presenter.store.protection_config(bundle)['paths'], [private] if accepted else [])
        self.assertFalse(private.exists())

    def test_qt_main_handles_cancel_before_main_event_loop(self):
        import novel_studio.__main__ as entry
        root = self.base / 'NovelStudioDesktop'
        root.mkdir()
        (root / 'center.json').write_bytes(b'{"layout":')
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.base)}), patch.object(sys, 'argv', ['novel-studio']), \
             patch.object(entry, 'QApplication', return_value=self.app), patch.object(entry, 'Workbench', return_value=self.window), \
             patch('novel_studio.desktop.local_config_ui.ConfigRecoveryDialog.exec', return_value=QDialog.Rejected), \
             patch('novel_studio.desktop.lifecycle.dispose_windows') as disposing, patch.object(self.app, 'exec', return_value=0) as loop:
            self.assertEqual(entry.main(), 0)
        loop.assert_not_called()
        disposing.assert_called_once()
        self.assertEqual((root / 'center.json').read_bytes(), b'{"layout":')

    def test_qt_main_default_recovery_reaches_main_window_and_restarts(self):
        import novel_studio.__main__ as entry
        root = self.base / 'NovelStudioDesktop'
        root.mkdir()
        raw = b'{"layout":'
        (root / 'center.json').write_bytes(raw)
        def accept_defaults(dialog):
            self.assertIsNone(dialog.parentWidget())
            self.assertTrue(dialog.defaults.isEnabled())
            dialog.defaults.click()
            return QDialog.Accepted
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.base)}), patch.object(sys, 'argv', ['novel-studio']), \
             patch.object(entry, 'QApplication', return_value=self.app), patch.object(entry, 'Workbench', return_value=self.window), \
             patch.object(ConfigRecoveryDialog, 'exec', new=accept_defaults), \
             patch('novel_studio.desktop.lifecycle.dispose_windows'), patch.object(self.app, 'exec', return_value=0) as loop:
            self.assertEqual(entry.main(), 0)
        loop.assert_called_once()
        self.assertTrue(self.window.isVisible())
        self.assertIsNone(self.window.controller.core)
        self.window.controller.workflow_timer.stop()
        self.window.controller.recovery_timer.stop()
        restarted = LocalStore.open(root)
        self.assertIsInstance(restarted.startup_state['layout'], dict)
        self.assertNotIn('project', restarted.startup_state)
        self.assertFalse(restarted.safe_start)
        self.assertEqual(next((root / 'Config-Recovery').glob('*.bin')).read_bytes(), raw)

    def test_legacy_tk_main_cancel_uses_explicit_exit_path(self):
        entry = tk_entry()
        root = MagicMock()
        with patch.object(entry.tk, 'Tk', return_value=root), patch.object(entry, 'Center', side_effect=entry.ConfigStartupCancelled), \
             patch.object(entry.Path, 'home', return_value=self.base), patch.object(entry.messagebox, 'showerror') as error, \
             patch.object(sys, 'argv', ['app.pyw', '--state', str(self.store.root)]), \
             patch.object(entry.ctypes.windll.user32, 'SetProcessDPIAware'):
            entry.main()
        root.destroy.assert_called_once()
        root.mainloop.assert_not_called()
        error.assert_not_called()

    def test_legacy_tk_recovery_choices_use_the_same_backup_requirement(self):
        entry = tk_entry()
        issue = self.issue()
        commands = {}
        def button(parent, **kwargs):
            commands[kwargs['text']] = kwargs
            return MagicMock()
        root = MagicMock()
        root.wait_window.side_effect = lambda dialog:commands['使用默认界面设置启动']['command']()
        with patch.object(entry.tk, 'Toplevel'), patch.object(entry.ttk, 'Label'), patch.object(entry.ttk, 'Frame'), patch.object(entry.ttk, 'Button', side_effect=button):
            self.assertEqual(entry.choose_config(issue, root), 'defaults')
        self.assertEqual(commands['使用默认界面设置启动']['state'], 'normal')
        commands.clear()
        root.wait_window.side_effect = lambda dialog:commands['暂不恢复并退出']['command']()
        with patch.object(entry.tk, 'Toplevel'), patch.object(entry.ttk, 'Label'), patch.object(entry.ttk, 'Frame'), patch.object(entry.ttk, 'Button', side_effect=button):
            self.assertEqual(entry.choose_config(LocalConfigIssue('permission'), root), 'exit')
        self.assertEqual(commands['使用默认界面设置启动']['state'], 'disabled')
