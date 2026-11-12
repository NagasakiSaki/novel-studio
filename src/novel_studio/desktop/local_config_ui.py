"""Local configuration recovery before projects, providers or layouts load."""
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QListWidget, QFileDialog, QDialogButtonBox


class ProtectedFoldersDialog(QDialog):
    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.setWindowTitle('保护其他文件夹')
        self.resize(640, 350)
        layout = QVBoxLayout(self)
        text = QLabel('添加不供 AI 角色读取的文件夹。设置保存在本机，用于之后新建的作品；已有作品继续使用各自保存的规则。')
        text.setWordWrap(True)
        layout.addWidget(text)
        self.folders = QListWidget()
        self.folders.addItems(self.paths)
        layout.addWidget(self.folders)
        self.add_folder = QPushButton('添加保护文件夹')
        self.add_folder.clicked.connect(self.choose_folder)
        layout.addWidget(self.add_folder)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, '选择需要保护的文件夹')
        if folder and folder not in self.paths:
            self.paths.append(folder)
            self.folders.addItem(folder)


class ConfigRecoveryDialog(QDialog):
    def __init__(self, issue, parent=None):
        super().__init__(parent)
        self.choice = 'exit'
        self.setWindowTitle('配置文件无法正常读取')
        self.resize(760, 490)
        layout = QVBoxLayout(self)
        text = ('Novel Studio 发现本机界面配置异常。你的小说正文和作品资料不会被清空。\n\n'
                '出错文件：center.json\n类型：' + issue.LABELS[issue.kind] + '\n'
                '原始配置已安全备份：' + ('是' if issue.backup_verified else '否') + '\n'
                '备份位置：' + (issue.backup or '尚无已验证备份') + '\n'
                '原始 SHA256：' + (issue.sha256 or '文件无法读取，未计算') + '\n\n'
                + ('下一步：仅恢复默认界面设置，不自动打开作品。原最近项目等设置保存在完整备份中。'
                   if issue.can_reset else '当前不能使用默认设置覆盖原文件。可重新尝试读取，或暂不恢复并退出。'))
        if issue.backup_failed:
            text += '\n备份未完成或未通过验证，已停止自动重置。'
        self.description = QLabel(text)
        self.description.setTextFormat(Qt.PlainText)
        self.description.setWordWrap(True)
        self.description.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.description)
        self.diagnostic = QPlainTextEdit()
        self.diagnostic.setReadOnly(True)
        self.diagnostic.setPlainText(json.dumps(issue.diagnostic(), ensure_ascii=False, indent=2))
        self.diagnostic.hide()
        layout.addWidget(self.diagnostic)
        buttons = QHBoxLayout()
        self.defaults = QPushButton('使用默认界面设置启动')
        self.defaults.setEnabled(issue.can_reset)
        self.defaults.clicked.connect(lambda:self.decide('defaults'))
        details = QPushButton('查看诊断信息')
        details.setCheckable(True)
        details.toggled.connect(self.diagnostic.setVisible)
        self.retry = QPushButton('重新尝试读取')
        self.retry.clicked.connect(lambda:self.decide('retry'))
        leave = QPushButton('暂不恢复并退出')
        leave.clicked.connect(self.reject)
        for button in (self.defaults, details, self.retry, leave):
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def decide(self, choice):
        self.choice = choice
        self.accept()


def choose_config(issue, parent=None):
    dialog = ConfigRecoveryDialog(issue, parent)
    return dialog.choice if dialog.exec() == QDialog.Accepted else 'exit'
