"""Desktop entry; the demo never opens a project or performs model requests."""
import argparse
import os
from pathlib import Path
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from .desktop.window import Workbench


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    from .diagnostics import DiagnosticLog
    from .desktop.diagnostic_ui import DiagnosticObserver
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share")))
    log = DiagnosticLog(local / 'NovelStudioDesktop') if not args.demo else None
    observer = DiagnosticObserver(app, log) if log else None
    if log:
        log.event('process_start')
    app.setApplicationName("Novel Studio")
    app.setOrganizationName("Novel Studio")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[2] / "assets/novel-studio.png")))
    window = Workbench(demo_mode=args.demo)
    if not args.demo:
        from .desktop.presenter import Presenter
        from gui.service import ConfigStartupCancelled
        try:
            window.controller = Presenter(window, local / "NovelStudioDesktop", diagnostic_log=log, background_load=True)
        except ConfigStartupCancelled:
            from .desktop.lifecycle import dispose_windows
            dispose_windows(app)
            if log:
                log.event('process_exit', exit_code=0)
                observer.close()
            return 0
    window.show()
    result = app.exec()
    from .desktop.lifecycle import dispose_windows
    dispose_windows(app)
    if log:
        log.event('process_exit', exit_code=result)
        observer.close()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
