"""Author-first workflow views, using existing Qt themes and controller intents."""
from __future__ import annotations

import json
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QPlainTextEdit, QComboBox, QSpinBox, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QProgressBar, QDialog, QDialogButtonBox, QFormLayout, QTreeWidget,
    QTreeWidgetItem, QScrollArea, QMessageBox, QCheckBox)
from ..core import CoreError
from ..workflow_scale import derive
from ..workflow_planning import BOOK_TEXT, VOLUME_TEXT, CHAPTER_TEXT
from .workflow_display import plain, STATUS, PHASE, model_label  # Public compatibility exports.
LABELS = {"concept": "核心概念", "theme": "主题", "synopsis": "故事梗概", "characters": "人物与关系", "conflicts": "主要冲突",
    "direction": "发展方向", "ending": "结局", "title": "标题", "summary": "剧情概要", "milestones": "重要节点",
    "relationships": "人物成长与关系变化", "foreshadowing": "伏笔与回收", "events": "主要事件", "constraints": "约束与注意"}


def action_button(text, callback, primary=False):
    button = QPushButton(text)
    if primary:
        button.setObjectName("primary")
    button.clicked.connect(callback)
    return button


class ScaleDialog(QDialog):
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.setWindowTitle("作品规模与创作目标")
        self.resize(680, 650)
        layout, form = QVBoxLayout(self), QFormLayout()
        layout.addWidget(plain("篇幅设置仅供 AI 创作参考，实际卷章结构和篇幅以已确认大纲及故事发展为准。", "section"))
        self.fields = {}
        labels = {"chapter_target": "每章建议字数", "chapter_minimum": "每章建议下限", "chapter_maximum": "每章建议上限", "chapters_per_volume": "每卷章节数参考",
            "total_words": "全书篇幅参考", "total_chapters": "全书章节数参考", "total_volumes": "全书卷数参考"}
        value = core.workflow.scale.get()
        for key, label in labels.items():
            spin = QSpinBox()
            spin.setRange(0 if key.startswith("total_") else 1, {"total_words": 100_000_000, "total_chapters": 1000, "total_volumes": 100, "chapters_per_volume": 200}.get(key, 100_000))
            if key.startswith("total_"):
                spin.setSpecialValueText("未确定")
            spin.setValue(value[key] or 0)
            spin.valueChanged.connect(self.recalculate)
            self.fields[key] = spin
            form.addRow(label, spin)
        layout.addLayout(form)
        self.explanation = QPlainTextEdit()
        self.explanation.setReadOnly(True)
        layout.addWidget(self.explanation, 1)
        layout.addWidget(plain("字数由本地统计：默认含标点，排除空白和 Markdown 格式标记；其他口径可在主笔字数管理中设置。", "muted"))
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton("保存作品目标", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.recalculate()

    def value(self):
        return {key: (spin.value() or None) if key.startswith("total_") else spin.value() for key, spin in self.fields.items()}

    def recalculate(self, *args):
        if not hasattr(self, "explanation"):
            return
        try:
            value = derive(self.value())
            self.explanation.setPlainText(f"预计全书：{value['estimated_words'] or '未确定'} 字\n预计章节：{value['estimated_chapters'] or '未确定'} 章\n预计卷数：{value['estimated_volumes'] or '未确定'} 卷\n标准卷篇幅：{value['words_per_full_volume']} 字\n最后一卷：{value['last_volume_chapters'] or '未确定'} 章\n\n计算依据：{value['basis']}\n\n" + "\n".join(value["warnings"]))
        except CoreError as error:
            self.explanation.setPlainText(str(error))

    def save(self):
        try:
            self.core.workflow.scale.save(self.value())
            self.accept()
        except (CoreError, ValueError) as error:
            QMessageBox.warning(self, "目标未保存", str(error))


class OutlineEditDialog(QDialog):
    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.value = json.loads(json.dumps(value))
        self.current_path = None
        self.setWindowTitle("编辑大纲并保存为新候选")
        self.resize(1020, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(plain("只修改选定字段；保存为新版本，不改变已确认的大纲。", "muted"))
        row = QHBoxLayout()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMaximumWidth(260)
        root = QTreeWidgetItem(["整书规划"])
        root.setData(0, Qt.UserRole, [])
        self.tree.addTopLevelItem(root)
        for vi, volume in enumerate(self.value["volumes"]):
            item = QTreeWidgetItem([f"第{volume['number']}卷 {volume['title']}"])
            item.setData(0, Qt.UserRole, ["volumes", vi])
            root.addChild(item)
            for ci, chapter in enumerate(volume["chapters"]):
                child = QTreeWidgetItem([f"第{chapter['number']}章 {chapter['title']}"])
                child.setData(0, Qt.UserRole, ["volumes", vi, "chapters", ci])
                item.addChild(child)
        root.setExpanded(True)
        row.addWidget(self.tree)
        scroll, content = QScrollArea(), QWidget()
        scroll.setWidgetResizable(True)
        self.form = QFormLayout(content)
        self.editors, self.labels = {}, {}
        for key in dict.fromkeys((*BOOK_TEXT, *VOLUME_TEXT, *CHAPTER_TEXT)):
            editor = QPlainTextEdit()
            editor.setMaximumHeight(100)
            label = plain(LABELS[key])
            self.form.addRow(label, editor)
            self.editors[key], self.labels[key] = editor, label
        scroll.setWidget(content)
        row.addWidget(scroll, 1)
        layout.addLayout(row, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton("另存为大纲草稿", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.tree.currentItemChanged.connect(self.select)
        self.tree.setCurrentItem(root)

    def node(self, path):
        row = self.value
        for key in path:
            row = row[key]
        return row

    def flush(self):
        if self.current_path is not None:
            row = self.node(self.current_path)
            for key, editor in self.editors.items():
                if key in row:
                    row[key] = editor.toPlainText()

    def select(self, item, old=None):
        self.flush()
        self.current_path = item.data(0, Qt.UserRole)
        row = self.node(self.current_path)
        for key, editor in self.editors.items():
            editor.setVisible(key in row)
            self.labels[key].setVisible(key in row)
            editor.setPlainText(row.get(key, ""))

    def save(self):
        self.flush()
        self.accept()


class StartDialog(QDialog):
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core, self.preview = core, None
        self.setWindowTitle("开始本次连续创作")
        self.resize(760, 640)
        layout = QVBoxLayout(self)
        layout.addWidget(plain("主笔：" + model_label(core, "writer") + "\n责编：" + model_label(core, "editor"), "section"))
        form = QFormLayout()
        self.mode, self.count = QComboBox(), QSpinBox()
        for text, data in [("写指定章节数", "chapters"), ("写指定卷数", "volumes"), ("按已确认大纲完成全书", "book")]:
            self.mode.addItem(text, data)
        self.count.setRange(1, 1000)
        self.count.setValue(1)
        self.creative_mode = QComboBox()
        self.creative_mode.addItem("一键连续创作 · 自动接力与返修", "continuous")
        self.creative_mode.addItem("辅助创作 · 每步保存后由我继续", "assisted")
        form.addRow("创作方式", self.creative_mode)
        form.addRow("本次范围", self.mode)
        form.addRow("数量", self.count)
        layout.addLayout(form)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        layout.addWidget(self.summary, 1)
        self.confirm = QCheckBox("我确认本次范围与模型；只生成待确认草稿，模型服务可能计费")
        layout.addWidget(self.confirm)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.start = buttons.addButton("一键开始创作", QDialogButtonBox.AcceptRole)
        self.start.setObjectName("primary")
        self.start.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.confirm.toggled.connect(lambda yes: self.start.setEnabled(yes and self.preview is not None))
        self.mode.currentIndexChanged.connect(self.invalidate)
        self.creative_mode.currentIndexChanged.connect(self.invalidate)
        self.count.valueChanged.connect(self.invalidate)
        layout.addWidget(buttons)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.prepare)
        self.finished.connect(lambda result: self.preview_timer.stop())
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.prepare()

    def invalidate(self, *args):
        self.preview = None
        self.start.setEnabled(False)
        self.confirm.setChecked(False)
        self.count.setEnabled(self.mode.currentData() != "book")
        self.summary.setPlainText("正在核对新的有限范围…")
        self.preview_timer.start(180)

    def prepare(self):
        self.preview_timer.stop()
        self.confirm.setChecked(False)
        try:
            self.preview = self.core.workflow.pipeline.preview(self.mode.currentData(), self.count.value(), self.creative_mode.currentData())
            p = self.preview
            dynamic = p.get("execution_scope", {}).get("dynamic", False)
            scope = f"最多 {p['execution_scope']['chapter_limit']} 章" if dynamic and p["mode"] == "chapters" else (f"指定 {p['volume_count']} 卷，章数随叙事形成" if p["mode"] == "volumes" else "按正式大纲推进，章数随叙事形成") if dynamic else f"{p['chapter_count']} 章，涉及 {p['volume_count']} 卷"
            calls = str(p["estimated_model_tasks"]) if p["estimated_model_tasks"] is not None else "随章节数变化"
            self.summary.setPlainText(f"正式大纲：已确认的第{self.core.workflow.store.read()['outlines'][p['outline_version']]['number']}版\n本次：{scope}\n默认每章约 {p['scale']['chapter_target']} 字；责编会按正式大纲和叙事需要制定具体范围，实际可自然浮动。\n基础规划/写作/审稿任务：{calls}；每章各一次，不单独请求标题。\n重要问题每章最多两轮返修（方案、修订、复审），每章最多九次。\n本次授权安全上限：{p['max_model_tasks']} 次模型任务；这是运行上限，不是故事章数目标。人工重试另计。\n\n{p['notice']}\n" + "\n".join(p.get("warnings", [])) + "\n\n暂停/停止会等待当前步骤保存；不会保证服务端立即停止或不计费。")
            self.start.setEnabled(self.confirm.isChecked())
        except (CoreError, ValueError) as error:
            presenter = getattr(self.parent(), 'presenter', None)
            log = getattr(presenter, 'diagnostic_log', None)
            if log:
                identity = log.error(error, action='workflow_start', dialog_class='StartDialog')
            else:
                identity = None
            self.preview = None
            self.start.setEnabled(False)
            self.summary.setPlainText(str(error) + ('\n\n诊断编号：' + identity if identity else ''))


class NumberingDialog(QDialog):
    def __init__(self, chapters, parent=None):
        super().__init__(parent)
        self.rows = chapters
        self.setWindowTitle("核对正式章节编号")
        self.resize(760, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(plain("编号由当前执行目录自动分配，不要求大纲预先填写。可以手动调整，但不能重复、跳号或覆盖已有章节；下一步将展示完整差异。"))
        self.table = QTableWidget(len(chapters), 3)
        self.table.setHorizontalHeaderLabels(["章节", "正式卷号", "正式章号"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.fields = {}
        for index, row in enumerate(chapters):
            self.table.setItem(index, 0, QTableWidgetItem(row["title"]))
            volume, number = QSpinBox(), QSpinBox()
            volume.setRange(1, 100)
            number.setRange(1, 1000)
            volume.setValue(row["volume"])
            number.setValue(row["number"])
            self.table.setCellWidget(index, 1, volume)
            self.table.setCellWidget(index, 2, number)
            self.fields[row["chapter_id"]] = (volume, number)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.addButton("检查完整差异", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def numbering(self):
        return {key: (v.value(), n.value()) for key, (v, n) in self.fields.items()}


# Public view contract; dialogs above remain shared with the existing controller.
from .workspace import WorkflowPanel
