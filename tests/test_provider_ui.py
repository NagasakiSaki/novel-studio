"""Offscreen author setup flows. Never actual account material or network."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
try:
    from PySide6.QtWidgets import QApplication, QLineEdit
    from novel_studio.desktop.provider_ui import ProviderDialog
    from novel_studio.api_service import ProviderRegistry
    from novel_studio.core import Core, CoreError
    from novel_studio.host import ModelService
    from gui.service import LocalStore
    from test_official_providers import MemoryVault
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt development environment not installed")
class ProviderUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_password_field_local_save_and_role_separation(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            store = LocalStore(base / "state")
            registry = ProviderRegistry(store, MemoryVault())
            dialog = ProviderDialog(registry)
            try:
                dialog.provider.setCurrentIndex(dialog.provider.findData("openai"))
                self.assertEqual(dialog.secret.echoMode(), QLineEdit.Password)
                self.assertFalse(dialog.consent.isChecked())
                self.assertFalse(dialog.apply.isEnabled())
                dialog.secret.setText("SYNTHETIC-UI-CANARY")
                dialog.store_key()
                self.assertEqual(dialog.secret.text(), "")
                self.assertNotIn("SYNTHETIC-UI-CANARY", (store.root / "center.json").read_text("utf-8"))
                registry.reference("writer", "openai")
                with self.assertRaises(CoreError):
                    registry.reference("editor", "openai")
                dialog.secret.setText("UNSAVED-SYNTHETIC")
                dialog.provider.setCurrentIndex(dialog.provider.findData("gemini"))
                self.assertEqual(dialog.secret.text(), "")
            finally:
                dialog.reject()
                dialog.deleteLater()

    def test_author_must_consent_and_unknown_capability_not_guessed(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            core = Core.create(base, "模型界面合成工程", ROOT)
            ModelService(core)
            registry = ProviderRegistry(LocalStore(base / "state"), MemoryVault())
            registry.save_key("writer", "openai", "SYNTHETIC")
            dialog = ProviderDialog(registry, core)
            try:
                dialog.provider.setCurrentIndex(dialog.provider.findData("openai"))
                dialog.model.setCurrentText("synthetic-model")
                self.assertFalse(dialog.effort.isEnabled())
                self.assertEqual(dialog.cache.count(), 1)
                dialog.apply_config()
                self.assertEqual(core.models.selected("writer")["provider"], "antigravity")
                dialog.consent.setChecked(True)
                dialog.apply_config()
                self.assertEqual(core.models.selected("writer")["provider"], "openai")
                self.assertEqual(core.models.selected("editor")["provider"], "antigravity")
                dialog.secret.setText("UNSAVED-SYNTHETIC")
                dialog.reject()
                self.assertEqual(dialog.secret.text(), "")
            finally:
                dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
