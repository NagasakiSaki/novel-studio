"""Author-visible decisions. No direct filesystem mutations."""
import json
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit, QApplication,
    QPushButton, QVBoxLayout, QWidget, QGridLayout,
)


class DiffDialog(QDialog):
    def __init__(self, preview, parent=None):
        super().__init__(parent)
        self.setWindowTitle("作者批准 · 请查看完整差异")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1060, max(640, screen.width() - 32)), min(760, max(420, screen.height() - 40)))
        layout = QVBoxLayout(self)
        title = QLabel("即将更新：" + preview["target"])
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(QLabel("− 删除的旧内容    + 新增的内容\n旧版本会先备份；作品已变化时会阻止提交。"))
        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setPlainText(preview["diff"] or "内容相同，无需更新。")
        layout.addWidget(self.diff_view, 1)
        self.confirm = QCheckBox("我已查看完整差异，明确批准这次正式作品变更")
        layout.addWidget(self.confirm)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.approve = buttons.addButton("批准并保留历史版本", QDialogButtonBox.AcceptRole)
        self.approve.setEnabled(False)
        self.confirm.toggled.connect(lambda yes: self.approve.setEnabled(yes and bool(preview["diff"])))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class CacheTrend(QWidget):
    def __init__(self, values, parent=None):
        super().__init__(parent)
        self.values = values
        self.setMinimumHeight(150)
        self.setAccessibleName("最近回合官方报告的 Cache Read Token 趋势；空缺不连接。")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = self.palette().text().color()
        painter.setPen(color)
        painter.drawText(12, 22, "缓存读取量 · 最近回合（原始报告值，不代表命中率或节省金额）")
        measured = [v for v in self.values if type(v) is int and v >= 0]
        if not measured:
            painter.drawText(12, 65, "当前模型未提供该项统计")
            return
        maximum = max(max(measured), 1)
        previous = None
        painter.setPen(QPen(QColor("#518b77"), 2))
        for i, value in enumerate(self.values):
            if type(value) is not int:
                previous = None
                continue
            point = (20 + int(i * (self.width() - 45) / max(len(self.values) - 1, 1)),
                     self.height() - 15 - int(value / maximum * (self.height() - 55)))
            if previous:
                painter.drawLine(*previous, *point)
            painter.drawEllipse(point[0] - 3, point[1] - 3, 6, 6)
            previous = point


class InformationDialog(QDialog):
    def __init__(self, title, data, parent=None, trend=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(930, max(640, screen.width() - 32)), min(690, max(420, screen.height() - 40)))
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlainText(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2))
        layout.addWidget(self.text, 1)
        if trend is not None:
            layout.addWidget(CacheTrend(trend))
        self.actions = QGridLayout()
        self.action_count = 0
        layout.addLayout(self.actions)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.buttons.rejected.connect(self.reject)
        copy = self.buttons.addButton("复制诊断信息", QDialogButtonBox.ActionRole)
        copy.clicked.connect(lambda: self.text.copy() if self.text.textCursor().hasSelection() else self._copy_all())
        layout.addWidget(self.buttons)

    def add_action(self, text):
        item = QPushButton(text)
        self.actions.addWidget(item, self.action_count // 2, self.action_count % 2)
        self.action_count += 1
        return item

    def _copy_all(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.text.toPlainText())
