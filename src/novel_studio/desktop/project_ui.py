"""First-use project form and the built-in, read-only writing example."""
from pathlib import Path

from PySide6.QtWidgets import (
    QBoxLayout, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QLineEdit,
    QPlainTextEdit, QPushButton, QSizePolicy, QTabWidget, QVBoxLayout, QWidget,
)

from ..core import relative_parts
from .demo import REVIEW, TEXT, TITLE
from .workflow_display import plain


class HomeRow(QWidget):
    """Keep action and card rows readable when the navigation takes space."""

    def __init__(self, stack_below, spacing=12):
        super().__init__()
        self.stack_below = stack_below
        self.row = QBoxLayout(QBoxLayout.LeftToRight, self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(spacing)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def resizeEvent(self, event):
        direction = QBoxLayout.TopToBottom if event.size().width() < self.stack_below else QBoxLayout.LeftToRight
        if self.row.direction() != direction:
            self.row.setDirection(direction)
        super().resizeEvent(event)


class NewProjectDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("projectDialog")
        self.setWindowTitle("新建作品")
        self.resize(520, 250)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        root.addWidget(plain("为你的故事建一个文件夹", "dialogTitle"))
        root.addWidget(plain("输入书名并选择保存位置，之后就可以直接写草稿。", "muted"))

        fields = QFormLayout()
        self.title = QLineEdit()
        self.title.setPlaceholderText("例如：白榆镇回声")
        fields.addRow("作品名称", self.title)
        self.folder = QLineEdit()
        self.folder.setReadOnly(True)
        self.folder.setPlaceholderText("尚未选择文件夹")
        self.folder.setAccessibleName("作品的上一级文件夹")
        location = QBoxLayout(QBoxLayout.LeftToRight)
        location.addWidget(self.folder, 1)
        choose = QPushButton("选择文件夹…")
        choose.clicked.connect(self.choose_folder)
        location.addWidget(choose)
        fields.addRow("保存位置", location)
        root.addLayout(fields)
        self.destination = plain("", "muted")
        root.addWidget(self.destination)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.create_button = buttons.addButton("创建作品", QDialogButtonBox.AcceptRole)
        self.create_button.setObjectName("primary")
        self.create_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.title.textChanged.connect(self.refresh)
        self.folder.textChanged.connect(self.refresh)
        self.refresh()
        self.title.setFocus()

    @property
    def values(self):
        return Path(self.folder.text()), self.title.text().strip()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择存放新作品的位置", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def refresh(self):
        parent, title = self.values
        try:
            valid = bool(title) and relative_parts(title) == (title,) and all(ord(char) >= 32 for char in title)
        except ValueError:
            valid = False
        ready = valid and parent.is_absolute()
        self.create_button.setEnabled(ready)
        if ready:
            self.destination.setText(f"将创建：{parent / title}")
        elif title and not valid:
            self.destination.setText("请输入可用的文件夹名称，不能包含路径或系统保留名称。")
        else:
            self.destination.setText("程序会在所选位置创建一个以书名命名的新文件夹。")


class WritingExampleDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("projectDialog")
        self.setWindowTitle("离线写作示例")
        self.resize(720, 530)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)
        root.addWidget(plain(TITLE, "dialogTitle"))
        root.addWidget(plain("阅读这段示例草稿和相应的审稿意见。是否修改或定稿，由作者决定。", "muted"))
        tabs = QTabWidget()
        for name, text in (("草稿", TEXT), ("责编意见", REVIEW)):
            view = QPlainTextEdit(text)
            view.setReadOnly(True)
            view.setObjectName("chapterReading")
            tabs.addTab(view, name)
        root.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
