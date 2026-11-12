"""Shared Qt operation runner; consumers supply operations and signal callbacks."""
from PySide6.QtCore import QThread, Signal
from ..diagnostics import scope


class Job(QThread):
    finished_value = Signal(object)
    failed = Signal(object)
    progress = Signal(dict)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            with scope(**getattr(self, 'diagnostic_context', {})):
                self.finished_value.emit(self.operation(self.progress.emit))
        except Exception as error:
            self.failed.emit(error)
