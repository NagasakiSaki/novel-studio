"""Author edits to versioned, scoped future advice; never edits story canon."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QCheckBox, QFormLayout,
    QPushButton, QMessageBox, QDialogButtonBox)
from .workflow_display import plain


class GuidanceDialog(QDialog):
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.current = None
        self.setWindowTitle("后续创作指导 · 查看、修改与失效")
        self.resize(900, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(plain("这些是有来源的创作建议，不能自动变成已确认设定。修改会保留版本；锁定后，模型不能静默替换。"))
        row = QHBoxLayout()
        self.list = QListWidget()
        row.addWidget(self.list, 1)
        form = QFormLayout()
        self.topic, self.text = QLineEdit(), QPlainTextEdit()
        self.kind, self.scope = QComboBox(), QComboBox()
        for label, key in [("世界观", "world"), ("人物", "character"), ("伏笔与剧情", "thread"), ("文风", "style"), ("时间线", "timeline")]:
            self.kind.addItem(label, key)
        for label, key in [("有效期内始终提供", "always"), ("按本章任务检索", "relevant"), ("仅下一章", "next")]:
            self.scope.addItem(label, key)
        self.expiry = QSpinBox()
        self.expiry.setRange(0, 1000)
        self.expiry.setSpecialValueText("未指定到期章")
        self.active = QCheckBox("启用")
        self.locked = QCheckBox("由作者锁定，模型不得替换")
        self.source = plain("", "muted")
        for label, field in [("主题", self.topic), ("内容", self.text), ("类别", self.kind), ("适用方式", self.scope), ("全书第几章后失效", self.expiry), ("", self.active), ("", self.locked), ("来源", self.source)]:
            form.addRow(label, field)
        row.addLayout(form, 2)
        layout.addLayout(row, 1)
        save = QPushButton("保存指导新版本")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.list.currentItemChanged.connect(self.select)
        self.reload()

    def reload(self):
        self.current = None
        self.list.clear()
        for gid, row in self.core.workflow.store.read()["guidance"].items():
            item = QListWidgetItem(row["note"]["topic"] + " · 第" + str(row["version"]) + "版 · " + ("启用" if row["status"] == "active" else "停用或待复核"))
            item.setData(Qt.UserRole, gid)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def select(self, item, previous=None):
        if item is None:
            self.current = None
            return
        if self.current is not None and previous is not None:
            old = self.core.workflow.store.read()["guidance"][self.current]
            changed = (self.topic.text() != old["note"]["topic"] or self.text.toPlainText() != old["note"]["text"]
                or self.kind.currentData() != old["note"]["kind"] or self.scope.currentData() != old["note"]["scope"]
                or self.expiry.value() != old["note"]["expires_after_chapter"] or self.active.isChecked() != (old["status"] == "active")
                or self.locked.isChecked() != old.get("locked", False))
            if changed and QMessageBox.question(self, "指导尚未保存", "切换会放弃当前指导的未保存修改，是否切换？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                self.list.blockSignals(True)
                self.list.setCurrentItem(previous)
                self.list.blockSignals(False)
                return
        self.current = item.data(Qt.UserRole)
        row = self.core.workflow.store.read()["guidance"][self.current]
        note = row["note"]
        self.topic.setText(note["topic"])
        self.text.setPlainText(note["text"])
        self.kind.setCurrentIndex(self.kind.findData(note["kind"]))
        self.scope.setCurrentIndex(self.scope.findData(note["scope"]))
        self.expiry.setValue(note["expires_after_chapter"])
        self.active.setChecked(row["status"] == "active")
        self.locked.setChecked(row.get("locked", False))
        self.source.setText("从全书第" + str(row["from_ordinal"]) + "章开始适用；来源为已保存的章节审稿记录。")

    def save(self):
        if self.current is None:
            return
        try:
            note = {"topic": self.topic.text(), "text": self.text.toPlainText(), "kind": self.kind.currentData(),
                    "scope": self.scope.currentData(), "expires_after_chapter": self.expiry.value()}
            self.core.workflow.guidance.update(self.current, note, active=self.active.isChecked(), locked=self.locked.isChecked())
            self.reload()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "指导未保存", str(error))
