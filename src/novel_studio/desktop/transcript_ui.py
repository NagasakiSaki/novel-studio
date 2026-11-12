"""Read-only local history; never submitted as provider request history."""
from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout
from .ui_state import transcript_text


class TranscriptDialog(QDialog):
    def __init__(self, models, role, parent=None):
        super().__init__(parent)
        self.models, self.role, self.cursor = models, role, None
        self.setWindowTitle("本角色本地对话记录 · " + ("主笔" if role == "writer" else "责编"))
        self.resize(980, 730)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("记录保存在作品本机目录，与供应商原生上下文独立。早期版本已经裁掉的记录无法在此恢复。"))
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        layout.addWidget(self.view, 1)
        self.older = QPushButton("查看更早的记录")
        self.older.clicked.connect(self.load)
        layout.addWidget(self.older)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self.load()

    def load(self):
        try:
            value = self.models.transcript_page(self.role, self.cursor)
            text = []
            state = self.models.core.workflow.store.read()
            for entry in reversed(value["entries"]):
                text += [transcript_text(entry, self.role, state), "\n────────────\n"]
            self.view.setPlainText("\n".join(text) or "尚无本地对话记录。")
            self.cursor = value["cursor"]
            self.older.setEnabled(self.cursor is not None)
        except (OSError, ValueError):
            self.view.setPlainText("历史记录未通过完整性检查，未继续读取；原记录保持不变。")
            self.older.setEnabled(False)
