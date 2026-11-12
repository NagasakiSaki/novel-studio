"""Chinese author export workflow; all file operations remain in ExportService."""
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget, QSplitter, QGroupBox)
from ..core import CoreError
from ..export_formats import CONVERTER_FORMATS, converter_path
from ..exporting import ExportService, DEFAULTS, FORMATS, TEMPLATES
from .dialogs import InformationDialog
from .jobs import Job
from .export_selection import ExportSelection
from .context_menu import attach_items, populate, entry


class ExportDialog(QDialog):
    def __init__(self, core, current_document=None, parent=None, *, embedded=False):
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.Widget)
        self.core, self.current_document = core, current_document
        self.service = ExportService(core)
        self.preview_value, self.cover, self.job = None, None, None
        self.last_result, self.export_order, self._signature = None, [], None
        self.setWindowTitle("作品导出 · 作者选择版本")
        self.resize(1120, 820)
        root = QVBoxLayout(self)
        note = QLabel("默认只导出正式作品。导出不改变原文；草稿显著标记，目标同名文件不会覆盖。")
        note.setWordWrap(True)
        root.addWidget(note)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        select = QWidget()
        layout = QVBoxLayout(select)
        row = QHBoxLayout()
        self.scope = QComboBox()
        for text, key in (("全部当前正式作品", "approved"), ("规划中的全书", "book"), ("指定分卷", "volume"), ("指定章节", "chapter"), ("自选章节范围（可排序）", "custom"), ("当前已保存草稿", "draft"), ("已批准历史版本", "history")):
            self.scope.addItem(text, key)
        row.addWidget(self.scope, 1)
        self.nodes = QComboBox()
        row.addWidget(self.nodes, 1)
        layout.addLayout(row)
        split = QSplitter()
        self.sources = ExportSelection()
        split.addWidget(self.sources)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.addWidget(QLabel('选中名称只预览，勾选框决定导出范围。'))
        self.source_preview = QPlainTextEdit()
        self.source_preview.setReadOnly(True)
        side_layout.addWidget(self.source_preview, 1)
        side_layout.addWidget(QLabel('本次导出顺序（可调整）'))
        self.order_list = QListWidget()
        self.order_list.setMaximumHeight(150)
        side_layout.addWidget(self.order_list)
        split.addWidget(side)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, 1)
        ordering = QHBoxLayout()
        ordering.addStretch()
        order_button = QPushButton("调整顺序")
        order_menu = QMenu(order_button)
        for title, delta in (("上移所选项", -1), ("下移所选项", 1)):
            item = order_menu.addAction(title)
            item.triggered.connect(lambda unused=False, direction=delta: self.move_source(direction))
        order_button.setMenu(order_menu)
        ordering.addWidget(order_button)
        layout.addLayout(ordering)
        choices = QHBoxLayout()
        for text, state in [('全选', True), ('清空选择', False), ('反选', 'invert')]:
            control = QPushButton(text)
            control.clicked.connect(lambda checked=False, value=state:self.sources.choose(value))
            choices.addWidget(control)
        self.selection_summary = QLabel()
        choices.addWidget(self.selection_summary, 1)
        layout.addLayout(choices)
        self.scope_note = QLabel()
        self.scope_note.setWordWrap(True)
        layout.addWidget(self.scope_note)
        self.tabs.addTab(select, "选择范围")
        self.fields = {}
        settings = QWidget()
        outer = QVBoxLayout(settings)
        self.templates = QComboBox()
        self.templates.addItems(list(TEMPLATES))
        outer.addWidget(self.templates)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        labels = {"title": "书名", "author": "作者", "subtitle": "副标题", "copyright": "版权声明", "preface": "序言", "toc": "生成内容目录", "volumes": "显示分卷标题", "header": "页眉", "footer": "页脚", "page_numbers": "页码（PDF/Word）", "font": "字体名称", "font_size": "字号", "line_spacing": "行距倍数", "paragraph_spacing": "段后间距（磅）", "first_indent": "首行缩进（字）", "chapter_break": "每章分页"}
        for key, default in DEFAULTS.items():
            if type(default) is bool:
                field = QCheckBox()
                field.setChecked(default)
                field.toggled.connect(self.invalidate)
            elif key in {"copyright", "preface"}:
                field = QPlainTextEdit()
                field.setMaximumHeight(105)
                field.textChanged.connect(self.invalidate)
            elif type(default) in {int, float}:
                field = QDoubleSpinBox()
                ranges = {"font_size": (9, 28), "line_spacing": (1, 3), "paragraph_spacing": (0, 30), "first_indent": (0, 4)}
                field.setRange(*ranges[key])
                field.setSingleStep(.1 if key == "line_spacing" else 1)
                field.setValue(default)
                field.valueChanged.connect(self.invalidate)
            else:
                field = QLineEdit(core.title if key == "title" else default)
                field.textChanged.connect(self.invalidate)
            self.fields[key] = field
            form.addRow(labels[key], field)
        cover_row = QHBoxLayout()
        cover_button = QPushButton("选择本地封面（PNG/JPEG）")
        cover_button.clicked.connect(self.choose_cover)
        clear_cover = QPushButton("清除封面")
        clear_cover.clicked.connect(self.clear_cover)
        cover_row.addWidget(cover_button)
        cover_row.addWidget(clear_cover)
        self.cover_label = QLabel("未选择封面")
        self.cover_label.setTextFormat(Qt.PlainText)
        outer.addLayout(cover_row)
        outer.addWidget(self.cover_label)
        self.tabs.addTab(settings, "书籍信息与排版")
        preview = QWidget()
        layout = QVBoxLayout(preview)
        top = QHBoxLayout()
        self.format = QComboBox()
        has_converter = converter_path() is not None
        for key, text in FORMATS.items():
            if has_converter or key not in CONVERTER_FORMATS:
                self.format.addItem(text + " (." + key + ")", key)
        row.addWidget(self.format)
        if not has_converter:
            format_note = QLabel("当前版本提供六种导出格式。EPUB 和 RTF 导出暂缓。")
            format_note.setWordWrap(True)
            select.layout().addWidget(format_note)
        self.initial_preview_button = QPushButton("预览本次导出")
        self.initial_preview_button.setObjectName("primary")
        self.initial_preview_button.clicked.connect(self.build_preview)
        select.layout().addWidget(self.initial_preview_button)
        generate = QPushButton("重新预览")
        generate.clicked.connect(self.build_preview)
        top.addWidget(generate)
        details = QPushButton("查看来源与格式说明")
        details.clicked.connect(self.details)
        top.addWidget(details)
        layout.addLayout(top)
        self.preview_browser = QTextBrowser()
        self.preview_browser.setOpenLinks(False)
        self.preview_browser.setOpenExternalLinks(False)
        layout.addWidget(self.preview_browser, 1)
        self.confirm = QCheckBox("我已查看范围、版本及格式说明，确认导出所选内容")
        self.confirm.toggled.connect(self.update_export_button)
        layout.addWidget(self.confirm)
        self.export_button = QPushButton("选择保存位置并导出")
        self.export_button.setObjectName("primary")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.save_export)
        layout.addWidget(self.export_button)
        self.tabs.addTab(preview, "内容预览")
        self.status = QLabel("选择范围和格式后预览；书籍信息和排版可以按需调整。未保存的修改不会被导出。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        root.addWidget(self.status)
        self.result_card = QGroupBox('导出完成')
        result_layout = QVBoxLayout(self.result_card)
        self.result_path = QLabel()
        self.result_path.setTextFormat(Qt.PlainText)
        self.result_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result_path.setWordWrap(True)
        result_layout.addWidget(self.result_path)
        result_actions = QHBoxLayout()
        for label, callback in [('打开文件', lambda:self.open_result(False)), ('打开所在文件夹', lambda:self.open_result(True)),
                ('重新导出', self.build_preview), ('查看本次导出记录', self.show_receipt)]:
            control = QPushButton(label)
            control.clicked.connect(lambda checked=False, action=callback:self.guarded(action))
            result_actions.addWidget(control)
        result_layout.addLayout(result_actions)
        root.addWidget(self.result_card)
        self.result_card.hide()
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        root.addWidget(close)
        close.setVisible(not embedded)
        self.scope.currentIndexChanged.connect(self.refresh_sources)
        self.sources.choices_changed.connect(self.choices_changed)
        self.sources.currentItemChanged.connect(self.preview_source)
        self.nodes.currentIndexChanged.connect(self.refresh_sources)
        self.format.currentIndexChanged.connect(self.invalidate)
        self.templates.currentIndexChanged.connect(self.apply_template)
        self.refresh_sources()
        attach_items(self.sources, self.source_actions, self.source_action)
        attach_items(self.order_list, lambda item:self.source_actions(next(n for n in self.sources.leaves if n.data(0, Qt.UserRole)['source_id']==item.data(Qt.UserRole))), self.source_action)

    def guarded(self, operation):
        try:
            return operation()
        except Exception as error:
            self.error(error)

    def error(self, error):
        message = str(error) if isinstance(error, CoreError) else "导出未完成，请检查本机保存位置。可能留下来源记录或暂存文件；原始作品没有变化。"
        self.status.setText(message)
        QMessageBox.warning(self, "作品导出", message)

    def invalidate(self, *unused):
        self.preview_value = None
        if self.last_result:
            self.result_card.setTitle('上次导出记录')
        if hasattr(self, "confirm"):
            self.confirm.setChecked(False)
            self.preview_browser.clear()
            self.update_export_button()

    def update_export_button(self, *unused):
        self.export_button.setEnabled(bool(self.preview_value and self.confirm.isChecked() and not self.job))

    def refresh_sources(self, *unused):
        rows = self.service.catalog()
        scope = self.scope.currentData()
        previous_node = self.nodes.currentData()
        self.nodes.blockSignals(True)
        self.nodes.clear()
        formal = [r for r in rows if not r['draft'] and not r['historical']]
        if scope == 'book':
            self.nodes.addItem(self.core.title, 'book')
        elif scope == 'volume':
            groups = {}
            for row in formal:
                groups.setdefault(str(row.get('volume_number', 'unplaced')), row['volume'])
            for key, title in groups.items():
                self.nodes.addItem(title, key)
        elif scope == 'chapter':
            for row in formal:
                self.nodes.addItem(row['title'], row.get('chapter_id', row['source_id']))
        index = self.nodes.findData(previous_node)
        self.nodes.setCurrentIndex(index if index >= 0 else 0)
        self.nodes.blockSignals(False)
        self.nodes.setVisible(scope in {'book', 'volume', 'chapter'})
        node = self.nodes.currentData()
        if scope in {'approved', 'book', 'volume', 'chapter'}:
            rows = formal
            if scope == 'volume':
                rows = [r for r in rows if str(r.get('volume_number', 'unplaced')) == node]
            elif scope == 'chapter':
                rows = [r for r in rows if r.get('chapter_id', r['source_id']) == node]
        elif scope == 'history':
            rows = [r for r in rows if r['historical']]
        elif scope == 'draft':
            rows = [r for r in rows if r['draft'] and (not self.current_document or r['source_id'] == self.current_document)]
        signature = (scope, node, self.core.title, tuple((r['source_id'], r['revision'], r['title'], r['version']) for r in rows))
        if signature == self._signature:
            return
        previous_checked = {r['source_id'] for r in self.sources.checked_rows()}
        same_scope = self._signature and self._signature[:2] == signature[:2]
        checked = previous_checked if same_scope else {r['source_id'] for r in rows if scope in {'approved', 'book', 'volume', 'chapter'} or scope == 'draft' and r['source_id'] == self.current_document}
        self._signature = signature
        self.sources.set_rows(self.core.title, rows, checked)
        self.source_preview.clear()
        self.scope_note.setText('勾选框决定导出范围；点击名称只预览，不改变勾选。可按全书、卷、章节或版本选择。正式、草稿和历史版本分别标明。')
        if not rows:
            self.scope_note.setText('本范围尚无对应版本。正式版本需作者批准收录；草稿与历史版本可在范围菜单中明确选择。')
        self.choices_changed()

    def choices_changed(self):
        rows = self.sources.checked_rows()
        available = {r['source_id']:r for r in rows}
        self.export_order = [key for key in self.export_order if key in available]
        self.export_order += [r['source_id'] for r in rows if r['source_id'] not in self.export_order]
        selected = self.order_list.currentItem()
        current = selected.data(Qt.UserRole) if selected else None
        self.order_list.clear()
        for key in self.export_order:
            row = available[key]
            item = QListWidgetItem(row['volume'] + ' · ' + row['title'] + ' · ' + row['version'])
            item.setData(Qt.UserRole, key)
            self.order_list.addItem(item)
            if key == current:
                self.order_list.setCurrentItem(item)
        self.selection_summary.setText(f"已勾选 {len(rows)} 个版本 · {sum(r['word_count'] for r in rows)} 字")
        self.invalidate()

    def preview_source(self, item=None, old=None):
        item = item or self.sources.currentItem()
        row = item.data(0, Qt.UserRole) if item else None
        if not row:
            self.source_preview.setPlainText('选择某个版本名称可阅读全文；勾选本组可选择其全部版本。')
            return
        try:
            from ..core import SafeTree, digest
            with SafeTree(self.core.project) as tree:
                raw = tree.read(row['relative'])
            if digest(raw) != row['revision']:
                raise CoreError('EXPORT_CHANGED', '版本刚刚变化，请重新读取导出列表。')
            self.source_preview.setPlainText(row['volume'] + ' · ' + row['title'] + ' · ' + row['version'] + f" · {row['word_count']} 字\n\n" + raw.decode('utf-8-sig'))
        except (ValueError, OSError) as error:
            self.source_preview.setPlainText('无法安全预览此版本，原文保持不变。' + str(error))

    def select_sources(self, source_ids):
        self.scope.setCurrentIndex(self.scope.findData('custom'))
        self.sources.blockSignals(True)
        for node in self.sources.leaves:
            node.setCheckState(0, Qt.Checked if node.data(0, Qt.UserRole)['source_id'] in source_ids else Qt.Unchecked)
        self.sources.sync_groups()
        self.sources.blockSignals(False)
        self.export_order = list(source_ids)
        self.choices_changed()

    def source_actions(self, item):
        if not self.sources.descendants(item) or self.job:
            return []
        row = item.data(0, Qt.UserRole)
        data = {'source_id':row['source_id']} if row else {'source_ids':[n.data(0, Qt.UserRole)['source_id'] for n in self.sources.descendants(item)]}
        values = [entry('取消勾选' if item.checkState(0) == Qt.Checked else '勾选导出', 'toggle', data)]
        if row:
            values += [entry('预览正文', 'preview', data), entry('查看版本信息', 'info', data)]
            if row['source_id'] in self.export_order:
                values += [entry('上移', 'up', data), entry('下移', 'down', data)]
        return values

    def source_action(self, key, data):
        if 'source_ids' in data:
            nodes = [n for n in self.sources.leaves if n.data(0, Qt.UserRole)['source_id'] in data['source_ids']]
            state = Qt.Unchecked if nodes and all(n.checkState(0) == Qt.Checked for n in nodes) else Qt.Checked
            self.sources.blockSignals(True)
            for node in nodes:
                node.setCheckState(0, state)
            self.sources.sync_groups()
            self.sources.blockSignals(False)
            self.choices_changed()
            return
        node = next((n for n in self.sources.leaves if n.data(0, Qt.UserRole)['source_id'] == data.get('source_id')), None)
        if node is None or self.job:
            return
        if key == 'toggle':
            node.setCheckState(0, Qt.Unchecked if node.checkState(0) == Qt.Checked else Qt.Checked)
        elif key == 'preview':
            self.preview_source(node)
        elif key == 'info':
            row = node.data(0, Qt.UserRole)
            InformationDialog('所选版本', row['title'] + '\n' + row['version'] + f"\n{row['word_count']} 字\n原文保持不变，历史版本需明确勾选后导出。", self).exec()
        elif key in {'up', 'down'}:
            self.move_source(-1 if key == 'up' else 1, data['source_id'])

    def move_source(self, direction, source_id=None):
        if source_id is None:
            item = self.order_list.currentItem()
            source_id = item.data(Qt.UserRole) if item else None
            if source_id is None and self.sources.currentRow() >= 0:
                source_id = self.sources.item(self.sources.currentRow()).data(Qt.UserRole)['source_id']
        if source_id not in self.export_order:
            return
        index = self.export_order.index(source_id)
        target = index + direction
        if 0 <= target < len(self.export_order):
            self.export_order[index], self.export_order[target] = self.export_order[target], self.export_order[index]
            self.choices_changed()
            self.order_list.setCurrentRow(target)

    def apply_template(self, *unused):
        values = {**DEFAULTS, **TEMPLATES[self.templates.currentText()]}
        for key in ("font", "font_size", "line_spacing", "paragraph_spacing", "first_indent", "chapter_break"):
            field, value = self.fields[key], values[key]
            if isinstance(field, QLineEdit):
                field.setText(value)
            elif isinstance(field, QCheckBox):
                field.setChecked(value)
            else:
                field.setValue(value)
        self.invalidate()

    def choose_cover(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择作者授权使用的本地封面", "", "图片 (*.png *.jpg *.jpeg)")
        if path:
            self.cover = Path(path)
            self.cover_label.setText(self.cover.name)
            self.invalidate()

    def clear_cover(self):
        self.cover = None
        self.cover_label.setText("未选择封面")
        self.invalidate()

    def config(self):
        return {key: field.isChecked() if isinstance(field, QCheckBox) else field.toPlainText() if isinstance(field, QPlainTextEdit) else field.text() if isinstance(field, QLineEdit) else field.value() for key, field in self.fields.items()}

    def selected(self):
        available = {r['source_id']:r for r in self.sources.checked_rows()}
        return [available[key] for key in self.export_order if key in available]

    def build_preview(self):
        def operation():
            self.invalidate()
            value = self.service.preview(self.selected(), self.config(), self.format.currentData(), self.cover)
            self.preview_value = value
            self.preview_browser.setHtml(value["html"])
            self.status.setText(("注意：包含未经批准草稿。\n" if value["draft"] else "仅所选正式版本。\n") + "\n".join(value["warnings"]) + "\n" + value["note"])
            self.tabs.setCurrentIndex(2)
        self.guarded(operation)

    def details(self):
        if self.preview_value:
            rows = self.preview_value["sources"]
            text = "\n".join(str(i + 1) + ". " + row["title"] + " · " + row["version"] for i, row in enumerate(rows))
            InformationDialog("导出来源及格式说明", text + "\n\n" + "\n".join(self.preview_value["warnings"]) + "\n" + self.preview_value["note"], self).exec()

    def save_export(self):
        if not self.preview_value or not self.confirm.isChecked() or self.job:
            return
        extension = self.preview_value["format"]
        path, _ = QFileDialog.getSaveFileName(self, "选择作品工程之外的导出位置（已有文件不会覆盖）", self.core.title + "." + extension, "导出文档 (*." + extension + ")", options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix("." + extension)
        value = self.preview_value
        if self.embedded:
            window = self.window()
            presenter = getattr(window, "presenter", None)
            if presenter is None:
                raise CoreError("EXPORT_INVALID", "导出工作区未连接作者界面。")
            self.job = presenter.start_job(lambda progress: self.service.export(value["handle"], target, True), self.exported, "正在导出作品…", after_finish=self.finished_job)
            self.tabs.setEnabled(False)
            self.update_export_button()
            self.status.setText("正在本机生成文档，请等待保存完成。")
            return
        self.job = Job(lambda progress: self.service.export(value["handle"], target, True), self)
        self.job.finished_value.connect(self.exported)
        self.job.failed.connect(self.error)
        self.job.finished.connect(self.finished_job)
        self.tabs.setEnabled(False)
        self.update_export_button()
        self.status.setText("正在本机生成文档，原始作品不会改动。请等待完成后关闭。")
        self.job.start()

    def exported(self, value):
        self.preview_value = None
        self.confirm.setChecked(False)
        self.last_result = value
        self.result_path.setText(value['path'])
        self.result_card.setTitle('本次导出完成')
        self.result_card.show()
        self.status.setText('导出完成，原始作品保持不变。文件位置和后续操作见结果卡片。')
        parent = self.parentWidget()
        while parent:
            if isinstance(parent, QScrollArea):
                self.layout().activate()
                parent.widget().layout().activate()
                parent.ensureWidgetVisible(self.result_card)
                break
            parent = parent.parentWidget()

    def open_result(self, folder=False):
        if self.last_result:
            target = Path(self.last_result['path'])
            if not target.is_file():
                raise CoreError('EXPORT_MISSING', '导出文件已被移动，请重新导出或查看原保存位置。')
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent if folder else target))):
                raise CoreError('EXPORT_OPEN_FAILED', '未能打开默认程序。请使用上方显示的保存位置。')

    def show_receipt(self):
        if self.last_result:
            import json
            from ..core import SafeTree
            target = Path(self.last_result['receipt'])
            with SafeTree(target.parent) as tree:
                value = json.loads(tree.read(target.name))
            InformationDialog('本次导出记录', json.dumps(value, ensure_ascii=False, indent=2), self).exec()

    def finished_job(self):
        job, self.job = self.job, None
        self.tabs.setEnabled(True)
        self.update_export_button()
        if not self.embedded:
            job.deleteLater()

    def reject(self):
        if not self.job:
            super().reject()
        else:
            self.status.setText("本地导出仍在进行，请等待完成；不会强行中断文件发布。")

    def closeEvent(self, event):
        if self.job:
            event.ignore()
        else:
            super().closeEvent(event)
