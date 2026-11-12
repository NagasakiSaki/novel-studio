"""Same Qt settings dialog, without opening any novel or last project."""
import os
from pathlib import Path
from PySide6.QtWidgets import QApplication
from novel_studio.desktop.provider_ui import ProviderDialog
from novel_studio.desktop.theme import apply_theme
from novel_studio.api_service import ProviderRegistry
from gui.service import LocalStore

app = QApplication([])
app.setApplicationName("Novel Studio · 本机官方 API 设置")
apply_theme(app, "light")
local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share")))
registry = ProviderRegistry(LocalStore(local / "NovelStudioDesktop"))
dialog = ProviderDialog(registry)
dialog.show()
result = app.exec()
from novel_studio.desktop.lifecycle import dispose_windows
dispose_windows(app)
raise SystemExit(result)
