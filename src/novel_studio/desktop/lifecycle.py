"""Dispose this process's Qt windows before destroying QApplication.

Call only after its event loop has ended and guarded background jobs finished.
"""
import gc
from PySide6.QtCore import QEvent


def dispose_windows(app):
    for window in app.topLevelWidgets():
        window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    gc.collect()
