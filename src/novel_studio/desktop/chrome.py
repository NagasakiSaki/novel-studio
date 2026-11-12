"""Native Qt navigation and bounded, owned width animations. No services or I/O."""
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QSize, Signal, QPoint
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QSizePolicy


def icon(kind, dark=False):
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#c5d2cb" if dark else "#52675e"), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if kind == "menu":
        for y in (6, 12, 18):
            painter.drawLine(4, y, 20, y)
    elif kind == "home":
        painter.drawPolyline([QPoint(4, 11), QPoint(12, 4), QPoint(20, 11)])
        painter.drawRoundedRect(6, 11, 12, 9, 1, 1)
    elif kind == "create":
        painter.drawLine(5, 19, 8, 12)
        painter.drawLine(8, 12, 17, 4)
        painter.drawLine(17, 4, 20, 7)
        painter.drawLine(20, 7, 11, 16)
        painter.drawLine(11, 16, 5, 19)
    elif kind in {"outline", "chapters"}:
        painter.drawRoundedRect(5, 3, 14, 18, 2, 2)
        for y in (8, 12, 16):
            painter.drawLine(8, y, 16, y)
    elif kind == "materials":
        painter.drawRoundedRect(3, 7, 18, 13, 2, 2)
        painter.drawLine(4, 7, 4, 4)
        painter.drawLine(4, 4, 11, 4)
        painter.drawLine(11, 4, 14, 7)
    elif kind == "chat":
        painter.drawRoundedRect(3, 4, 18, 13, 3, 3)
        painter.drawPolyline([QPoint(7, 17), QPoint(7, 21), QPoint(12, 17)])
        painter.drawLine(7, 9, 17, 9)
        painter.drawLine(7, 13, 14, 13)
    elif kind == "export":
        painter.drawLine(12, 3, 12, 15)
        painter.drawLine(8, 11, 12, 15)
        painter.drawLine(12, 15, 16, 11)
        painter.drawPolyline([QPoint(4, 15), QPoint(4, 20), QPoint(20, 20), QPoint(20, 15)])
    else:
        painter.drawEllipse(4, 4, 16, 16)
        painter.drawEllipse(9, 9, 6, 6)
    painter.end()
    return QIcon(pixmap)


class AnimatedPanel(QWidget):
    settled = Signal()
    extent_changed = Signal()

    def __init__(self, parent=None, *, rail=0, width=300):
        super().__init__(parent)
        self.rail = rail
        self.expanded_width = width
        self.expanded = bool(rail)
        self.animation = QPropertyAnimation(self, b"maximumWidth", self)
        self.animation.setDuration(170)
        self.animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.animation.finished.connect(self.finish)
        self.animation.valueChanged.connect(lambda value: self.extent_changed.emit())
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.setMinimumWidth(rail)
        self.setMaximumWidth(420 if self.expanded else rail)

    def set_expanded(self, expanded, animate=True):
        self.animation.stop()
        current = self.width() if self.isVisible() else self.rail
        self.expanded = bool(expanded)
        self.setMinimumWidth(self.rail)
        if self.expanded:
            self.show()
        target = self.expanded_width if self.expanded else self.rail
        if animate and self.isVisible():
            self.setMaximumWidth(max(self.rail, current))
            self.animation.setStartValue(max(self.rail, current))
            self.animation.setEndValue(target)
            self.animation.start()
        else:
            self.setMaximumWidth(target)
            self.finish()

    def finish(self):
        if self.expanded:
            self.setMinimumWidth(172 if self.rail else 240)
            self.setMaximumWidth(360 if self.rail else 420)
        else:
            self.setMinimumWidth(self.rail)
            self.setMaximumWidth(self.rail)
            if not self.rail:
                self.hide()
        self.settled.emit()


class Navigation(AnimatedPanel):
    selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, rail=64, width=210)
        self.setObjectName("mainNavigation")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 22, 10, 16)
        layout.setSpacing(6)
        self.caption = QLabel("创作空间")
        self.caption.setObjectName("navCaption")
        layout.addWidget(self.caption)
        self.buttons = {}
        self.labels = {"home": "作品", "create": "创作", "outline": "大纲", "chapters": "章节", "chat": "AI 对话", "materials": "资料", "export": "导出", "settings": "设置"}
        for key, text in self.labels.items():
            if key == "settings":
                layout.addStretch()
            button = QPushButton(text)
            button.setObjectName("navItem")
            button.setCheckable(True)
            button.setMinimumHeight(42)
            button.setIconSize(QSize(22, 22))
            button.setToolTip(text)
            button.clicked.connect(lambda checked=False, name=key: self.selected.emit(name))
            self.buttons[key] = button
            layout.addWidget(button)
        self.footnote = QLabel("本机作品 · 作者掌握定稿")
        self.footnote.setObjectName("muted")
        layout.addWidget(self.footnote)
        self.update_style(False)

    def update_style(self, dark):
        for key, button in self.buttons.items():
            button.setIcon(icon(key, dark))

    def set_expanded(self, expanded, animate=True):
        for key, button in self.buttons.items():
            button.setText(self.labels[key] if expanded else "")
            button.setAccessibleName(self.labels[key])
        self.caption.setVisible(expanded)
        self.footnote.setVisible(expanded)
        super().set_expanded(expanded, animate)

    def highlight(self, key):
        for name, button in self.buttons.items():
            button.setChecked(name == key)
