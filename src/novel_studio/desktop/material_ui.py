"""Type-first Qt import wizard and local material library. No direct novel writes."""
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QAbstractItemView,
)
from ..core import CoreError
from ..materials import TYPES, Prepared, policy_description, selected_sections
from ..material_formats import FORMATS
from ..material_search import search
from .dialogs import DiffDialog


def plain(text):
    item = QLabel(text)
    item.setTextFormat(Qt.PlainText)
    item.setWordWrap(True)
    return item


class ParseJob(QThread):
    ready = Signal(int, object, object)

    def __init__(self, service, jobs, kind, custom, update_id, parent=None):
        super().__init__(parent)
        self.service, self.jobs, self.kind = service, jobs, kind
        self.custom, self.update_id = custom, update_id

    def run(self):
        retained = 0
        for index, path in self.jobs:
            if self.isInterruptionRequested():
                break
            try:
                item = self.service.prepare(path, self.kind, self.update_id, self.custom)
                retained += len(item.raw)
                if retained > 64 * 1024 * 1024:
                    raise CoreError("BATCH_LIMIT", "本批预览原件超过 64 MiB，请分成较小批次。")
                self.ready.emit(index, item, None)
            except Exception as error:
                self.ready.emit(index, None, error)


class ImportWizard(QDialog):
    def __init__(self, core, parent=None, update_id=None, policy_only=False):
        super().__init__(parent)
        self.core, self.service, self.update_id = core, core.materials, update_id
        self.rows, self.job, self.cancel_pending = [], None, False
        self.changed, self.loading = False, False
        self.setWindowTitle("导入资料 · 先选择资料类型，再选择文件")
        screen = (parent.screen() if parent else QApplication.primaryScreen()).availableGeometry()
        height = min(screen.height() - 36, parent.height() - 60 if parent else 860)
        self.resize(min(1180, max(700, screen.width() - 24)), min(860, max(500, height)))
        layout = QVBoxLayout(self)
        layout.addWidget(plain("1 选择资料类型   →   2 选择文件、检查正文与目录   →   3 作者确认导入"))
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.stack.addWidget(self.type_page())
        self.stack.addWidget(self.preview_page())
        self.notice = plain("尚未选择类型；不会提前打开文件选择器。")
        layout.addWidget(self.notice)
        buttons = QHBoxLayout()
        self.back_button = QPushButton("返回修改资料类型")
        self.back_button.clicked.connect(self.back)
        self.back_button.setVisible(False)
        buttons.addWidget(self.back_button)
        self.add_button = QPushButton("添加文件")
        self.add_button.clicked.connect(self.choose_files)
        self.add_button.setVisible(False)
        buttons.addWidget(self.add_button)
        self.retry_button = QPushButton("仅重试所选失败文件")
        self.retry_button.clicked.connect(self.retry)
        self.retry_button.setVisible(False)
        buttons.addWidget(self.retry_button)
        buttons.addStretch()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.next_button = QPushButton("选择文件…")
        self.next_button.setObjectName("primary")
        self.next_button.setEnabled(False)
        self.next_button.clicked.connect(self.next)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)
        # Qt item views choose row zero when they receive the first focus on
        # show. Start on Cancel so a newly visible wizard still has NO type.
        self.cancel_button.setFocus(Qt.OtherFocusReason)
        if update_id:
            record = self.service.get(update_id)
            index = [r[0] for r in TYPES].index(record["material_type"])
            self.types.setCurrentRow(index)
            if policy_only:
                prepared = self.service.prepare_policy(update_id)
                self.rows = [{"path": prepared.source, "prepared": prepared, "error": None, "status": "待作者批准", "always": record["always_section_ids"][:],
                              "mode": record["context_mode"], "scope": False, "custom": record.get("custom_label", ""), "done": False}]
                self.show_files()
                self.refresh_table()
                self.table.selectRow(0)

    def type_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        self.types = QListWidget()
        self.types.setMinimumWidth(245)
        for key, title, description in TYPES:
            item = QListWidgetItem(title + "\n" + description)
            item.setData(Qt.UserRole, key)
            item.setToolTip(description)
            self.types.addItem(item)
        self.types.setCurrentRow(-1)
        self.types.currentRowChanged.connect(self.type_changed)
        layout.addWidget(self.types, 1)
        right = QVBoxLayout()
        self.type_title = plain("您要导入什么资料？")
        self.type_title.setObjectName("title")
        right.addWidget(self.type_title)
        self.type_description = plain("请先选择左侧资料类型，再点击底部“选择文件”。资料类型与文件格式是两回事。")
        right.addWidget(self.type_description)
        right.addWidget(plain("已实现格式：TXT、Markdown、JSON、DOCX、PDF、EPUB、RTF、HTML、ODT。\n每份文件必须实际解析出正文；扫描 PDF 暂不支持 OCR。\n原件完整保留，不执行文档中的脚本、宏或远程资源。"))
        self.custom_name = QLineEdit()
        self.custom_name.setPlaceholderText("自定义资料类型名称（最多 80 字）")
        self.custom_name.setMaxLength(80)
        self.custom_name.setVisible(False)
        right.addWidget(self.custom_name)
        right.addStretch()
        layout.addLayout(right, 1)
        return page

    def preview_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["选中导入 / 文件名称", "真实格式", "原件大小", "逐项状态"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(65)
        self.table.setMaximumHeight(160)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 300)
        self.table.itemSelectionChanged.connect(self.select_file)
        layout.addWidget(self.table)
        self.details = plain("选中一份文件，检查实际提取正文。")
        layout.addWidget(self.details)
        row = QHBoxLayout()
        row.addWidget(plain("资料使用方式："))
        self.context_mode = QComboBox()
        self.context_mode.addItem("常驻规则 + 其余背景按需检索", "retrieval")
        self.context_mode.addItem("完整预设上下文（超限会阻止发送）", "full")
        self.context_mode.currentIndexChanged.connect(self.save_scope)
        row.addWidget(self.context_mode, 1)
        self.full_preview_button = QPushButton("查看完整正文")
        self.full_preview_button.clicked.connect(self.show_full_text)
        row.addWidget(self.full_preview_button)
        layout.addLayout(row)
        split = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(plain("资料目录 · 勾选需要始终提供的规则（含下级）"))
        self.toc = QTreeWidget()
        self.toc.setHeaderHidden(True)
        self.toc.itemChanged.connect(self.save_scope)
        self.toc.itemClicked.connect(self.select_section)
        left_layout.addWidget(self.toc, 1)
        split.addWidget(left)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        split.addWidget(self.text)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, 1)
        self.scope_ack = QCheckBox("我已确认：勾选项是常驻规则，其余为按需背景；完整模式将提供全文。")
        self.scope_ack.toggled.connect(self.save_scope)
        layout.addWidget(self.scope_ack)
        self.warning = plain("")
        self.warning.setObjectName("muted")
        layout.addWidget(self.warning)
        return page

    def current_type(self):
        item = self.types.currentItem()
        return item.data(Qt.UserRole) if item else None

    def type_changed(self, index):
        if index < 0:
            return
        key, title, description = TYPES[index]
        self.type_title.setText(title)
        self.type_description.setText(description)
        self.custom_name.setVisible(key == "custom")
        if hasattr(self, "next_button"):
            self.notice.setText("已选择：" + title + "。接下来选择文件并检查完整正文。")
            self.next_button.setEnabled(True)
            self.next_button.setText("继续查看已选文件" if self.rows else "选择文件…")

    def next(self):
        if self.job:
            return
        if self.stack.currentIndex() == 0:
            kind = self.current_type()
            if not kind:
                return
            if kind == "custom" and not self.custom_name.text().strip():
                QMessageBox.warning(self, "请填写名称", "请为自定义资料类型填写一个容易理解的名称。")
                return
            if not self.rows:
                self.choose_files()
                return
            for row in self.rows:
                if row["done"]:
                    continue
                item = row.get("prepared")
                if item and item.material_type != kind:
                    item.material_type = kind
                    row["always"] = [s["id"] for s in item.parsed["sections"]] if kind == "writing_rules" else []
                    row["scope"] = False
                    row["duplicates"] = self.service.duplicates(item)
                    row["selection_initialized"] = False
                if item:
                    item.custom_label = self.custom_name.text().strip()
            self.show_files()
            self.refresh_table()
            self.table.selectRow(0)
            self.select_file()
        else:
            self.commit_selected()

    def choose_files(self):
        kind = self.current_type()
        if not kind or self.job:
            return
        names, _ = QFileDialog.getOpenFileNames(self, "为“" + TYPES[self.types.currentRow()][1] + "”选择文件", "",
                    "已实现的文档格式 (" + " ".join("*" + s for s in FORMATS) + ")")
        if not names:
            self.notice.setText("已取消文件选择；没有创建导入记录。")
            return
        if len(names) + len(self.rows) > 32 or self.update_id and len(names) != 1:
            QMessageBox.warning(self, "请选择较小批次", "每批最多 32 个文件；更新已有资料时一次选择一个文件。")
            return
        jobs = []
        for name in names:
            if any(str(row["path"]) == name for row in self.rows):
                continue
            jobs.append((len(self.rows), Path(name)))
            self.rows.append({"path": Path(name), "prepared": None, "error": None, "status": "正在解析", "always": [], "mode": "retrieval", "scope": False, "done": False})
        self.show_files()
        self.refresh_table()
        self.start_parse(jobs)

    def start_parse(self, jobs):
        if not jobs:
            return
        self.job = ParseJob(self.service, jobs, self.current_type(), self.custom_name.text().strip(), self.update_id, self)
        self.job.ready.connect(self.parsed)
        self.job.finished.connect(self.parsing_finished)
        self.next_button.setEnabled(False)
        self.back_button.setEnabled(False)
        self.add_button.setEnabled(False)
        self.notice.setText("正在本机限时解析；尚未确认，不会写入作品。")
        self.job.start()

    def parsed(self, index, item, error):
        row = self.rows[index]
        retained = sum(len(r["prepared"].raw) for i, r in enumerate(self.rows) if i != index and r.get("prepared"))
        if item and retained + len(item.raw) > 64 * 1024 * 1024:
            item, error = None, CoreError("BATCH_LIMIT", "本批预览总量超过 64 MiB，请先完成本批，再分批导入。")
        row.update(prepared=item, error=error, status="解析失败，可单独重试" if error else "正文已提取，待确认")
        # A new parse may change heading order. Never reuse old positional IDs
        # or an earlier author's scope acknowledgement on different content.
        row.update(always=[], scope=False, mode="retrieval", selection_initialized=False, duplicates=[])
        if item:
            row["duplicates"] = self.service.duplicates(item)
            if row["duplicates"] and not self.update_id:
                row["status"] = "相同内容已存在，默认跳过"
            if item.material_type == "writing_rules":
                row["always"] = [s["id"] for s in item.parsed["sections"]]
        self.refresh_table()
        if self.table.currentRow() < 0:
            self.table.selectRow(index)

    def parsing_finished(self):
        old = self.job
        self.job = None
        old.deleteLater()
        self.back_button.setEnabled(True)
        self.add_button.setEnabled(not self.update_id)
        self.next_button.setEnabled(True)
        self.notice.setText("请选择一份文件核对正文与作用范围，再点击“预览并确认导入”。")
        self.select_file()
        if self.cancel_pending:
            super().reject()

    def show_files(self):
        self.stack.setCurrentIndex(1)
        self.back_button.setVisible(True)
        self.add_button.setVisible(not self.update_id)
        self.retry_button.setVisible(True)
        self.next_button.setText("预览并确认导入")
        self.next_button.setEnabled(self.job is None)

    def refresh_table(self):
        self.loading = True
        selected = self.table.currentRow()
        checks = {i: self.table.item(i, 0).checkState() for i in range(self.table.rowCount()) if self.table.item(i, 0)}
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            item = row.get("prepared")
            name = QTableWidgetItem(row["path"].name)
            name.setFlags(name.flags() | Qt.ItemIsUserCheckable)
            default = Qt.Checked if item and not row.get("duplicates") and not row["done"] else Qt.Unchecked
            if item and not row.get("selection_initialized"):
                name.setCheckState(default)
                row["selection_initialized"] = True
            else:
                name.setCheckState(Qt.Unchecked if row["done"] else checks.get(i, default))
            self.table.setItem(i, 0, name)
            self.table.setItem(i, 1, QTableWidgetItem(item.parsed["format"] if item else row["path"].suffix))
            self.table.setItem(i, 2, QTableWidgetItem(f"{len(item.raw) / 1024:.1f} KB" if item else "—"))
            self.table.setItem(i, 3, QTableWidgetItem(row["status"]))
        self.loading = False
        if selected >= 0:
            self.table.selectRow(selected)

    def select_file(self):
        if self.loading:
            return
        index = self.table.currentRow()
        if not 0 <= index < len(self.rows):
            return
        row, self.loading = self.rows[index], True
        item = row.get("prepared")
        self.toc.clear()
        if not item:
            self.text.setPlainText(str(row["error"]) if isinstance(row.get("error"), CoreError) else "尚未成功提取正文。文件可能损坏、为空或资源受限，可单独重试。")
            self.details.setText(row["path"].name + " · " + row["status"])
            self.loading = False
            return
        self.text.setPlainText(item.parsed["text"])
        nodes = {}
        inherited = selected_sections({"always_section_ids": row["always"], "sections": item.parsed["sections"]})
        for section in item.parsed["sections"]:
            node = QTreeWidgetItem([section["title"]])
            node.setData(0, Qt.UserRole, section["id"])
            node.setFlags(node.flags() | Qt.ItemIsUserCheckable)
            node.setCheckState(0, Qt.Checked if section["id"] in inherited else Qt.Unchecked)
            if section["parent"] in nodes:
                nodes[section["parent"]].addChild(node)
            else:
                self.toc.addTopLevelItem(node)
            nodes[section["id"]] = node
            node.setExpanded(True)
        for node in nodes.values():
            if node.childCount():
                node.setFlags(node.flags() | Qt.ItemIsAutoTristate)
        self.context_mode.setCurrentIndex(max(0, self.context_mode.findData(row["mode"])))
        self.scope_ack.setChecked(row["scope"])
        type_title = next(t[1] for t in TYPES if t[0] == item.material_type)
        self.details.setText(f"类型：{type_title}　原件：{item.source.name}　目标：已批准资料库 / 1 个完整资料条目（不拆文件）\n原件 {len(item.raw):,} 字节；正文 {len(item.parsed['text']):,} 字符；目录 {len(item.parsed['sections'])} 节。" + (" 本次更新已有资料，旧版保留。" if self.update_id else ""))
        notes = item.parsed["warnings"][:]
        if row.get("duplicates") and not self.update_id:
            notes.append("重复提醒：相同内容已经存在；若仍勾选导入，将建立独立副本。")
        if item.material_type in {"global_preset", "writing_rules"}:
            notes.append("请明确勾选常驻规则并确认；未勾选内容只按需检索，软件不会猜测全局规则。")
        self.warning.setText("\n".join(notes))
        self.loading = False

    def save_scope(self, *unused):
        if self.loading or not 0 <= self.table.currentRow() < len(self.rows):
            return
        row = self.rows[self.table.currentRow()]
        selected = []
        def collect(parent):
            for i in range(parent.childCount()):
                node = parent.child(i)
                if node.checkState(0) == Qt.Checked:
                    selected.append(node.data(0, Qt.UserRole))
                collect(node)
        collect(self.toc.invisibleRootItem())
        row.update(always=selected, mode=self.context_mode.currentData(), scope=self.scope_ack.isChecked())

    def select_section(self, node, column):
        index = self.table.currentRow()
        if not 0 <= index < len(self.rows) or not self.rows[index].get("prepared"):
            return
        item = self.rows[index]["prepared"]
        section = next(s for s in item.parsed["sections"] if s["id"] == node.data(0, Qt.UserRole))
        self.text.setPlainText(item.parsed["text"][section["start"]:section["end"]])

    def show_full_text(self):
        index = self.table.currentRow()
        if 0 <= index < len(self.rows) and self.rows[index].get("prepared"):
            self.text.setPlainText(self.rows[index]["prepared"].parsed["text"])

    def retry(self):
        index = self.table.currentRow()
        if self.job or not 0 <= index < len(self.rows) or not self.rows[index].get("error"):
            return
        self.rows[index]["status"] = "重新解析中"
        self.start_parse([(index, self.rows[index]["path"])])

    def back(self):
        if self.job:
            return
        self.save_scope()
        self.stack.setCurrentIndex(0)
        self.back_button.setVisible(False)
        self.add_button.setVisible(False)
        self.retry_button.setVisible(False)
        self.next_button.setText("继续查看已选文件")

    def approve_preview(self, preview):
        return DiffDialog(preview, self).exec() == QDialog.Accepted

    def commit_selected(self):
        self.save_scope()
        attempted = False
        for i, row in enumerate(self.rows):
            item = row.get("prepared")
            if not item or row["done"] or self.table.item(i, 0).checkState() != Qt.Checked:
                continue
            attempted = True
            try:
                preview = self.service.preview(item, row["always"], row["mode"], row["scope"], allow_duplicate=bool(row.get("duplicates")))
                if not self.approve_preview(preview):
                    self.core._approvals.pop(preview["handle"], None)
                    row["status"] = "未批准；其余成功结果保留"
                    break
                self.core.author_commit(preview["handle"], True)
                row.update(status="PASS · 已导入，原件与版本保留", done=True, error=None)
                self.changed = True
            except Exception as error:
                row.update(error=error, status="FAIL · " + (str(error) if isinstance(error, CoreError) else "文件已变化或无法安全导入"))
        self.refresh_table()
        self.notice.setText("已成功导入的资料保留；失败项可重试。完成后将更新主笔资料副本，责编旧交接仍保留原版本。" if attempted else "请先勾选需要导入且已成功提取正文的文件。")
        self.cancel_button.setText("完成" if self.changed else "关闭")

    def reject(self):
        if self.job:
            self.cancel_pending = True
            self.job.requestInterruption()
            self.notice.setText("正在取消；当前受限解析最迟 25 秒结束，尚未确认的资料不会写入项目。")
            return
        super().reject()

    def closeEvent(self, event):
        if self.job:
            self.reject()
            event.ignore()
            return
        super().closeEvent(event)


class MaterialPanel(QWidget):
    action_requested = Signal(str, dict)
    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.core, self.current = None, None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.addWidget(plain("资料，让故事有根"))
        row = QHBoxLayout()
        self.import_button = QPushButton("导入资料")
        self.import_button.setObjectName("primary")
        self.import_button.clicked.connect(lambda: self.action_requested.emit("import_material", {}))
        row.addWidget(self.import_button)
        row.addStretch()
        from PySide6.QtWidgets import QMenu
        self.more_button = QPushButton("资料操作")
        self.more_menu = QMenu(self.more_button)
        for text, action in [("更新此资料", "update_material"), ("调整常驻与检索范围", "material_policy"), ("版本历史与恢复", "history")]:
            item = self.more_menu.addAction(text)
            item.triggered.connect(lambda checked=False, a=action: self.action_requested.emit(a, {"material_id": self.current}))
        self.more_button.setMenu(self.more_menu)
        self.more_button.setEnabled(False)
        row.addWidget(self.more_button)
        layout.addLayout(row)
        search_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("查找人物、地点、术语或资料标题")
        self.query.returnPressed.connect(self.search)
        search_row.addWidget(self.query, 1)
        self.search_button = QPushButton("搜索")
        self.search_button.clicked.connect(self.search)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("已批准资料 / 目录")
        self.tree.currentItemChanged.connect(lambda item, old: self.selected(item, 0))
        split.addWidget(self.tree)
        right = QWidget()
        body = QVBoxLayout(right)
        self.info = plain("打开作品后可导入人物、世界观或写作规则。")
        body.addWidget(self.info)
        self.content = QPlainTextEdit()
        self.content.setReadOnly(True)
        body.addWidget(self.content, 1)
        split.addWidget(right)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, 1)

    def bind(self, core):
        changed = self.core is not None and (core is None or self.core.project_id != core.project_id)
        previous = self.tree.currentItem()
        selection = previous.data(0, Qt.UserRole) if previous and not changed else None
        if changed:
            self.query.clear()
        self.core = core
        self.tree.blockSignals(True)
        self.tree.clear()
        self.tree.blockSignals(False)
        self.import_button.setEnabled(core is not None)
        if not core:
            self.selected(None, 0)
            return
        groups = {}
        active_ids, restore = set(), None
        for record in core.materials.records():
            active_ids.add(record['material_id'])
            kind = record["material_type"]
            if kind not in groups:
                groups[kind] = QTreeWidgetItem([dict((r[0], r[1]) for r in TYPES)[kind]])
                self.tree.addTopLevelItem(groups[kind])
                groups[kind].setExpanded(True)
            item = QTreeWidgetItem([record.get('display_name', record['source']['name']) + " · v" + str(record["version"]) + (' · 已排除上下文' if not record.get('context_enabled', True) else '')])
            item.setData(0, Qt.UserRole, (record["material_id"], None))
            groups[kind].addChild(item)
            if selection and selection[0] == record['material_id']:
                restore = item
            nodes = {}
            for section in record["sections"]:
                node = QTreeWidgetItem([section["title"]])
                node.setData(0, Qt.UserRole, (record["material_id"], section["id"]))
                (nodes.get(section["parent"]) or item).addChild(node)
                nodes[section["id"]] = node
                if selection == (record["material_id"], section["id"]):
                    restore = node
        if restore:
            parent = restore.parent()
            while parent:
                parent.setExpanded(True)
                parent = parent.parent()
            self.tree.setCurrentItem(restore)
        else:
            self.selected(None, 0)
        if selection and selection[0] not in active_ids:
            self.info.setText('此资料已移到回收区，原件与历史保留；可点击资料回收区恢复。')

    def selected(self, item, column):
        value = item.data(0, Qt.UserRole) if item else None
        if not value or not self.core:
            self.current = None
            self.more_button.setEnabled(False)
            self.content.clear()
            self.info.setText("打开作品后可导入人物、世界观或写作规则。" if not self.core else
                              "在左侧选择资料查看原文与版本。" if self.tree.topLevelItemCount() else
                              "资料库为空。点「导入资料」添加人物、世界观或写作规则。")
            self.selection_changed.emit()
            return
        identifier, section_id = value
        self.current = identifier
        self.more_button.setEnabled(True)
        record = self.core.materials.get(identifier)
        section = next((s for s in record["sections"] if s["id"] == section_id), None)
        name = record.get('display_name', record['source']['name'])
        self.info.setText(f"{name} · 第 {record['version']} 版" + (f" · {section['title']}" if section else "") +
                          "\n" + policy_description(record) + "\n原始来源（仅本机）：" + record["source"]["path"])
        self.content.setPlainText(record["text"][section["start"]:section["end"]] if section else record["text"])
        self.selection_changed.emit()

    def open_material(self, identifier):
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            for child in range(group.childCount()):
                item = group.child(child)
                if item.data(0, Qt.UserRole) == (identifier, None):
                    group.setExpanded(True)
                    self.tree.setCurrentItem(item)
                    self.tree.scrollToItem(item)
                    return
        raise CoreError("OBJECT_CHANGED", "这份资料已不在当前资料库中，请重新选择。")

    def search(self):
        if not self.core:
            return
        results = []
        for record in self.core.materials.records():
            for piece in search(record, self.query.text(), 8, cache=self.core._search_cache):
                results.append("【" + record["source"]["name"] + " · v" + str(record["version"]) + " · " + piece["title"] + "】\n" + piece["text"])
        self.tree.setCurrentItem(None)
        self.info.setText(f"搜索结果 · {len(results)} 个片段。选择左侧资料可查看其完整内容。")
        self.content.setPlainText("\n\n".join(results) if results else "没有匹配资料；可尝试人物、地点、术语或标题关键词。")
