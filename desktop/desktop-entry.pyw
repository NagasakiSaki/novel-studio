"""Packaged single-window entry. Isolated Python ignores system site-packages."""
from pathlib import Path
import sys
import ctypes
import os

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))
try:
    from novel_studio.__main__ import main
    raise SystemExit(main())
except Exception as error:
    try:
        from novel_studio.diagnostics import DiagnosticLog
        local = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/share')))
        identity = DiagnosticLog(local / 'NovelStudioDesktop').error(error, 'unhandled')
    except Exception:
        identity = 'STARTUP_LOG_UNAVAILABLE'
    ctypes.windll.user32.MessageBoxW(None, "程序未能完整启动。请保留整个文件夹，不要只复制一个 exe。\n诊断编号：" + identity + "\n重新打开后可导出诊断日志。", "Novel Studio 无法启动", 0x10)
    raise SystemExit(1)
