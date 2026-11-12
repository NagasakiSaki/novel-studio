"""Single-window workbench. Signals carry intent; this layer never writes novels."""
from __future__ import annotations

import base64
from PySide6.QtCore import QByteArray, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence, QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDockWidget, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMenu, QPlainTextEdit, QPushButton, QSplitter,
    QProgressBar, QStackedWidget, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from . import demo
from .theme import apply_theme


def label(text="", name=None):
    item = QLabel(text)
    item.setTextFormat(Qt.PlainText)
    if name:
        item.setObjectName(name)
    return item


def button(text, name=None):
    item = QPushButton(text)
    if name:
        item.setObjectName(name)
    return item


class WorkbenchViews(QMainWindow):
    action_requested = Signal(str, dict)
    layout_changed = Signal(dict)

    def __init__(self, demo_mode=False):
        super().__init__()
        self.demo_mode = demo_mode
        self.role = "writer"
        self.theme = "light"
        self.document_id = None
        self.document_revision = None
        self.document_area = None
        self.loading_document = False
        self.setObjectName("NovelStudioWorkbench")
        self.setWindowTitle("Novel Studio · 统一创作工作台")
        self.resize(1440, 920)
        self.setMinimumSize(640, 420)
        self._header()
        self._navigation()
        self._center()
        self._inspector()
        self._status()
        self._shortcuts()
        apply_theme(QApplication.instance(), self.theme)
        if demo_mode:
            self.populate_demo()
        else:
            self.project_title.setText("尚未打开作品")
            self.project_note.setText("作品保存在本机 · 正式版本由作者批准")
            self.book_name.setText("从一部新作品开始")
            self.chapter_title.setText("欢迎来到 Novel Studio")
            self.editor.setPlaceholderText("创建或打开作品后，在这里编辑草稿。")
        self.draft_changed(False)

    def emit_action(self, name, **data):
        self.action_requested.emit(name, {"role": self.role, **data})

    def _header(self):
        header = QWidget()
        header.setObjectName("header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(22, 13, 22, 13)
        layout.setSpacing(13)
        mark = label("N", "mark")
        mark.setFixedSize(36, 36)
        mark.setAlignment(Qt.AlignCenter)
        layout.addWidget(mark)
        self.brand_label = label("NOVEL STUDIO", "brand")
        layout.addWidget(self.brand_label)
        self.header_divider = QFrame()
        self.header_divider.setFrameShape(QFrame.VLine)
        layout.addWidget(self.header_divider)
        self.project_title = label("", "section")
        self.project_title.setMaximumWidth(280)
        layout.addWidget(self.project_title)
        layout.addStretch()
        self.files_button = button("作品")
        layout.addWidget(self.files_button)
        self.roles = QButtonGroup(self)
        self.role_buttons = {}
        for key, text in [("writer", "主笔"), ("editor", "责编")]:
            item = button(text, "role")
            item.setCheckable(True)
            item.setChecked(key == self.role)
            item.clicked.connect(lambda checked=False, value=key: self.switch_role(value))
            self.roles.addButton(item)
            self.role_buttons[key] = item
            layout.addWidget(item)
        self.theme_button = button("深色")
        self.theme_button.clicked.connect(self.toggle_theme)
        layout.addWidget(self.theme_button)
        self.view_button = button("视图")
        layout.addWidget(self.view_button)
        self.diagnostics_button = button("设置与诊断")
        self.diagnostics_button.clicked.connect(lambda: self.emit_action("diagnostics"))
        layout.addWidget(self.diagnostics_button)
        self.setMenuWidget(header)

    def _navigation(self):
        self.nav_dock = QDockWidget("作品", self)
        self.nav_dock.setObjectName("navigationDock")
        self.nav_dock.setFeatures(QDockWidget.DockWidgetClosable)
        content = QWidget()
        content.setObjectName("navigation")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(15, 16, 15, 16)
        layout.setSpacing(12)
        layout.addWidget(label("WORKSPACE", "eyebrow"))
        self.book_name = label("", "section")
        self.book_name.setWordWrap(True)
        layout.addWidget(self.book_name)
        self.project_note = label("", "muted")
        self.project_note.setWordWrap(True)
        layout.addWidget(self.project_note)
        row = QHBoxLayout()
        self.new_project_button = button("新建作品")
        self.open_project_button = button("打开")
        self.new_project_button.clicked.connect(lambda: self.emit_action("new_project"))
        self.open_project_button.clicked.connect(lambda: self.emit_action("open_project"))
        row.addWidget(self.new_project_button)
        row.addWidget(self.open_project_button)
        layout.addLayout(row)
        self.workflow_button = button("作品工作台 · 一键创作", "primary")
        self.workflow_button.clicked.connect(lambda: self.emit_action("workflow_dashboard"))
        layout.addWidget(self.workflow_button)
        self.search = QLineEdit()
        self.search.setPlaceholderText("查找章节或资料")
        self.search.textChanged.connect(self.filter_tree)
        layout.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.itemClicked.connect(self.select_tree_item)
        layout.addWidget(self.tree, 1)
        self.import_button = button("导入作者资料")
        self.import_button.clicked.connect(lambda: self.emit_action("import_material"))
        layout.addWidget(self.import_button)
        self.history_button = button("版本历史与恢复")
        self.history_button.clicked.connect(lambda: self.emit_action("history"))
        layout.addWidget(self.history_button)
        self.new_draft_button = button("新建草稿")
        self.new_draft_button.clicked.connect(lambda: self.emit_action("new_draft"))
        layout.addWidget(self.new_draft_button)
        self.writer_settings_button = button("主笔设置 · 风格与字数")
        self.writer_settings_button.clicked.connect(lambda: self.emit_action("writer_settings"))
        layout.addWidget(self.writer_settings_button)
        self.editor_settings_button = button("责编设置 · 审美与审稿标准")
        self.editor_settings_button.clicked.connect(lambda: self.emit_action("editor_settings"))
        layout.addWidget(self.editor_settings_button)
        self.style_analysis_button = button("文风分析与抽象风格")
        self.style_analysis_button.clicked.connect(lambda: self.emit_action("style_analysis"))
        layout.addWidget(self.style_analysis_button)
        self.nav_dock.setWidget(content)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.nav_dock)
        self.nav_dock.setMinimumWidth(218)

    def _center(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        controls = QWidget()
        control_layout = QHBoxLayout(controls)
        control_layout.setContentsMargins(22, 12, 22, 12)
        self.model_selector = QComboBox()
        self.model_selector.setObjectName("modelSelector")
        self.model_selector.setMinimumWidth(200)
        self.model_selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.model_selector.addItem("尚未读取官方模型列表", None)
        self.model_selector.setEnabled(False)
        self.model_selector.activated.connect(lambda index: self.emit_action("set_model", model_id=self.model_selector.itemData(index)))
        control_layout.addWidget(self.model_selector, 1)
        self.model_manager_button = button("模型管理")
        self.model_manager_button.clicked.connect(lambda: self.emit_action("model_manager"))
        control_layout.addWidget(self.model_manager_button)
        self.refresh_models_button = button("刷新")
        self.refresh_models_button.clicked.connect(lambda: self.emit_action("refresh_models"))
        control_layout.addWidget(self.refresh_models_button)
        self.effort_selector = QComboBox()
        self.effort_selector.setObjectName("effortSelector")
        self.effort_selector.addItem("官方默认", None)
        self.effort_selector.setEnabled(False)
        self.effort_selector.activated.connect(lambda index: self.emit_action("set_effort", effort=self.effort_selector.itemData(index)))
        control_layout.addWidget(self.effort_selector)
        self.connection_button = button("测试连接")
        self.connection_button.clicked.connect(lambda: self.emit_action("test_connection"))
        control_layout.addWidget(self.connection_button)
        self.run_label = label("就绪", "pill")
        control_layout.addWidget(self.run_label)
        layout.addWidget(controls)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("workspaceTabs")
        self.document_page = self._document_page()
        self.tabs.addTab(self.document_page, "文稿")
        self.chat_stack = QStackedWidget()
        self.chat_pages = {}
        self.chat_outputs = {}
        self.chat_inputs = {}
        self.send_buttons = {}
        for key in ("writer", "editor"):
            page = self._chat_page(key)
            self.chat_pages[key] = page
            self.chat_stack.addWidget(page)
        self.tabs.addTab(self.chat_stack, "主笔与责编对话")
        self.review_output = QPlainTextEdit()
        self.review_output.setReadOnly(True)
        self.review_output.setPlaceholderText("责编的独立报告将显示在这里。")
        self.review_page = QWidget()
        review_layout = QVBoxLayout(self.review_page)
        self.review_title = label("独立审稿报告", "title")
        self.review_title.setWordWrap(True)
        review_layout.addWidget(self.review_title)
        review_layout.setContentsMargins(26, 22, 26, 18)
        review_layout.addWidget(label("证据与作者裁决", "title"))
        review_layout.addWidget(self.review_output, 1)
        self.feedback_input = QPlainTextEdit()
        self.feedback_input.setObjectName("composer")
        self.feedback_input.setPlaceholderText("您决定采纳哪些意见？只会把选定范围交给主笔。")
        self.feedback_input.setMaximumHeight(100)
        review_layout.addWidget(self.feedback_input)
        self.feedback_button = button("把选定意见交给主笔", "primary")
        self.feedback_button.clicked.connect(lambda: self.emit_action("feedback", text=self.feedback_input.toPlainText()))
        review_layout.addWidget(self.feedback_button, 0, Qt.AlignRight)
        self.tabs.addTab(self.review_page, "审稿与意见")
        from .material_ui import MaterialPanel
        self.material_panel = MaterialPanel()
        self.material_panel.action_requested.connect(lambda action, data: self.emit_action(action, **data))
        self.tabs.addTab(self.material_panel, "资料库")
        from .workflow_ui import WorkflowPanel
        self.workflow_panel = WorkflowPanel()
        self.workflow_panel.action_requested.connect(lambda action, data: self.emit_action(action, **data))
        self.tabs.addTab(self.workflow_panel, "作品工作台")
        if not self.demo_mode:
            self.tabs.setCurrentWidget(self.workflow_panel)
        self.metrics_button = button("查看用量与缓存趋势")
        self.metrics_button.clicked.connect(lambda: self.emit_action("usage"))
        review_layout.addWidget(self.metrics_button, 0, Qt.AlignRight)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(content)

    def _document_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QWidget()
        top = QVBoxLayout(heading)
        top.setContentsMargins(38, 26, 32, 18)
        top.addWidget(label("MANUSCRIPT", "eyebrow"))
        row = QHBoxLayout()
        self.chapter_title = label("", "title")
        self.chapter_title.setWordWrap(True)
        row.addWidget(self.chapter_title, 1)
        self.dirty_label = label("", "muted")
        row.addWidget(self.dirty_label)
        top.addLayout(row)
        self.document_note = label("草稿与正式版本分离 · 正式变更须您阅读差异并批准", "muted")
        self.document_note.setWordWrap(True)
        top.addWidget(self.document_note)
        layout.addWidget(heading)
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("manuscript")
        self.editor.setTabChangesFocus(False)
        self.editor.document().modificationChanged.connect(self.draft_changed)
        self.count_timer = QTimer(self)
        self.count_timer.setSingleShot(True)
        self.count_timer.timeout.connect(self.measure_current)
        self.count_metric = "novel_visible_chars_v1"
        self.editor.textChanged.connect(self.update_count)
        layout.addWidget(self.editor, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(25, 12, 25, 15)
        self.count_label = label("0 字", "muted")
        footer.addWidget(self.count_label)
        footer.addStretch()
        self.save_button = button("保存草稿")
        self.save_button.clicked.connect(lambda: self.emit_action("save_draft", document_id=self.document_id,
                                                                   revision=self.document_revision, text=self.editor.toPlainText()))
        footer.addWidget(self.save_button)
        self.handoff_button = button("交给责编")
        self.handoff_button.clicked.connect(lambda: self.emit_action("handoff", document_id=self.document_id))
        footer.addWidget(self.handoff_button)
        self.approval_button = button("预览定稿差异", "primary")
        self.approval_button.clicked.connect(lambda: self.emit_action("preview_promotion", document_id=self.document_id))
        footer.addWidget(self.approval_button)
        layout.addLayout(footer)
        goal_row = QHBoxLayout()
        self.word_goal_label = label("字数目标尚未设置 · 可在主笔设置中管理", "muted")
        self.word_goal_label.setWordWrap(True)
        self.word_progress = QProgressBar()
        self.word_progress.setRange(0, 1000)
        self.word_progress.setMaximumWidth(180)
        self.word_progress.setFormat("未设目标")
        self.word_settings_button = button("字数设置")
        self.word_settings_button.clicked.connect(lambda: self.emit_action("writer_settings", tab=1))
        goal_row.setContentsMargins(25, 0, 25, 10)
        goal_row.addWidget(self.word_goal_label, 1)
        goal_row.addWidget(self.word_progress)
        goal_row.addWidget(self.word_settings_button)
        layout.addLayout(goal_row)
        return page

    def _chat_page(self, key):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 18)
        layout.addWidget(label("主笔对话" if key == "writer" else "责编对话", "title"))
        layout.addWidget(label("对话、模型、资料副本和权限分别保存", "muted"))
        output = QPlainTextEdit()
        output.setReadOnly(True)
        output.setPlaceholderText("尚未发起真实模型请求。")
        layout.addWidget(output, 1)
        composer = QPlainTextEdit()
        composer.setObjectName("composer")
        composer.setPlaceholderText("用自然语言说明您希望完成的任务…")
        composer.setMaximumHeight(140)
        layout.addWidget(composer)
        row = QHBoxLayout()
        row.addWidget(label("生成内容先进入工作副本，正式作品由您决定", "muted"), 1)
        send = button("发送给主笔" if key == "writer" else "发送给责编", "primary")
        send.clicked.connect(lambda: self.emit_action("send", text=composer.toPlainText()))
        row.addWidget(send)
        layout.addLayout(row)
        self.chat_outputs[key] = output
        self.chat_inputs[key] = composer
        self.send_buttons[key] = send
        return page

    def _inspector(self):
        self.inspector_dock = QDockWidget("资料检查器", self)
        self.inspector_dock.setObjectName("inspectorDock")
        self.inspector_dock.setFeatures(QDockWidget.DockWidgetClosable)
        content = QWidget()
        content.setObjectName("inspector")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(19, 18, 19, 18)
        layout.setSpacing(15)
        layout.addWidget(label("CONTEXT", "eyebrow"))
        layout.addWidget(label("本次参考资料", "section"))
        self.context_label = label("尚未准备设定副本", "muted")
        self.context_label.setWordWrap(True)
        layout.addWidget(self.context_label)
        self.context_button = button("更新资料副本")
        self.context_button.clicked.connect(lambda: self.emit_action("context_snapshot"))
        layout.addWidget(self.context_button)
        self.material_context_button = button("预览本轮资料范围")
        self.material_context_button.clicked.connect(lambda: self.emit_action("preview_material_context"))
        layout.addWidget(self.material_context_button)
        self.context_list = QListWidget()
        layout.addWidget(self.context_list, 1)
        layout.addWidget(label("作者控制", "section"))
        text = label("AI 只在工作副本中处理内容。\n\n正式定稿、人物与世界观正典的更新，需要您查看差异并明确批准。", "muted")
        text.setWordWrap(True)
        layout.addWidget(text)
        self.inspector_dock.setWidget(content)
        self.addDockWidget(Qt.RightDockWidgetArea, self.inspector_dock)
        self.inspector_dock.setMinimumWidth(238)
        self.resizeDocks([self.nav_dock, self.inspector_dock], [235, 270], Qt.Horizontal)

    def _status(self):
        self.status_text = label("本机作品 · 尚未发起模型请求", "muted")
        self.statusBar().addWidget(self.status_text, 1)
        self.metrics = {}
        for key, text in [("input_tokens", "输入"), ("output_tokens", "输出"),
                          ("cache_read_tokens", "缓存读取"), ("hit_rate", "命中率"), ("latency", "耗时")]:
            item = label(text+" —", "muted")
            item.setContentsMargins(8, 4, 8, 4)
            item.setToolTip("尚无可核验数据；缺失值不会显示成 0。")
            self.metrics[key] = item
            self.statusBar().addPermanentWidget(item)

    def _shortcuts(self):
        for text, shortcut, action in [
            ("保存草稿", QKeySequence.Save, self.save_button.click),
            ("切换主笔", "Ctrl+1", lambda: self.switch_role("writer")),
            ("切换责编", "Ctrl+2", lambda: self.switch_role("editor")),
            ("专注阅读", "Ctrl+Shift+F", self.toggle_focus),
        ]:
            item = QAction(text, self)
            item.setShortcut(shortcut)
            item.triggered.connect(action)
            self.addAction(item)
        view = QMenu(self)
        view.addAction(self.nav_dock.toggleViewAction())
        view.addAction(self.inspector_dock.toggleViewAction())
        self.usage_action = view.addAction("Token 用量与缓存趋势")
        self.usage_action.triggered.connect(lambda: self.emit_action("usage"))
        self.cost_action = view.addAction("费用估算与模型价格")
        self.cost_action.triggered.connect(lambda: self.emit_action("costs"))
        self.view_button.setMenu(view)
        files = QMenu(self)
        for text, name in [("新建作品", "new_project"), ("打开作品", "open_project"), ("新建草稿", "new_draft"), ("导入作者资料", "import_material"), ("作品导出", "export_manuscript"), ("本角色对话记录", "transcripts"), ("版本历史与恢复", "history")]:
            item = files.addAction(text)
            item.triggered.connect(lambda checked=False, action=name: self.emit_action(action))
        self.files_button.setMenu(files)

    def switch_role(self, value):
        self.role = value
        self.role_buttons[value].setChecked(True)
        self.chat_stack.setCurrentWidget(self.chat_pages[value])
        self.tabs.setCurrentWidget(self.chat_stack)
        self.editor.setReadOnly(value == "editor" or self.document_area != "drafts")
        self.save_button.setEnabled(value == "writer" and self.document_area == "drafts" and not self.demo_mode)
        self.emit_action("role_selected")

    def toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
        self.theme_button.setText("浅色" if self.theme == "dark" else "深色")
        apply_theme(QApplication.instance(), self.theme)
        self.layout_changed.emit(self.capture_layout())

    def toggle_focus(self):
        show = not self.nav_dock.isVisible() and not self.inspector_dock.isVisible()
        self.nav_dock.setVisible(show)
        self.inspector_dock.setVisible(show)

    def capture_layout(self):
        return {"theme": self.theme, "docks": base64.b64encode(bytes(self.saveState(1))).decode("ascii"),
                "role": self.role, "tab": self.tabs.currentIndex(), "width": self.width(), "height": self.height()}

    def restore_layout(self, state):
        if state.get("theme") in ("light", "dark") and state["theme"] != self.theme:
            self.toggle_theme()
        if isinstance(state.get("docks"), str) and len(state["docks"]) <= 16000:
            try:
                self.restoreState(QByteArray(base64.b64decode(state["docks"], validate=True)), 1)
            except (ValueError, TypeError):
                pass
        screen = QApplication.primaryScreen().availableGeometry()
        width = state.get("width", 1440)
        height = state.get("height", 920)
        if type(width) is int and type(height) is int:
            self.resize(max(640, min(width, screen.width() - 24)), max(420, min(height, screen.height() - 40)))
        if state.get("role") in ("writer", "editor"):
            self.switch_role(state["role"])
        if type(state.get("tab")) is int and 0 <= state["tab"] < self.tabs.count():
            self.tabs.setCurrentIndex(state["tab"])

    def set_document(self, identifier, title, text, area="drafts", revision=None):
        self.loading_document = True
        self.document_id, self.document_revision, self.document_area = identifier, revision, area
        self.chapter_title.setText(title)
        self.editor.setPlainText(text)
        cursor = QTextCursor(self.editor.document())
        cursor.select(QTextCursor.Document)
        block = QTextBlockFormat()
        block.setLineHeight(170, QTextBlockFormat.ProportionalHeight.value)
        cursor.mergeBlockFormat(block)
        self.editor.document().setModified(False)
        self.editor.setReadOnly(self.role == "editor" or area != "drafts")
        notes = {"drafts": "草稿 · 保存后可继续编辑，正式收录由作者批准",
                 "canonical": "正式版本 · 只读，更新须核对差异并批准",
                 "reports": "独立审稿报告 · 只读",
                 "proposals": "变更提案 · 只有作者批准后才更新正式资料",
                 "writer_context": "主笔资料副本 · 只读", "editor_context": "责编稿件副本 · 只读"}
        self.document_note.setText(notes.get(area, "请选择要阅读或编辑的文件。"))
        self.save_button.setEnabled(self.role == "writer" and area == "drafts" and not self.demo_mode)
        self.handoff_button.setEnabled(area == "drafts" and not self.demo_mode)
        self.approval_button.setEnabled(area == "drafts" and not self.demo_mode)
        self.loading_document = False
        self.draft_changed(False)

    def draft_changed(self, changed):
        self.dirty_label.setText("未保存" if changed else ("合成示例" if self.demo_mode else "已载入"))

    def closeEvent(self, event):
        presenter = getattr(self, "presenter", None)
        if presenter:
            if presenter.job is not None:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(self, "这一回合仍在运行", "请等待官方请求结束后再关闭。关闭客户端不能保证服务端立即停止。")
                event.ignore()
                return
            if not presenter.confirm_discard():
                event.ignore()
                return
            if presenter.workflow_auto:
                presenter.workflow_auto = False
                presenter.workflow_timer.stop()
                if presenter.core:
                    run = presenter.core.workflow.pipeline.active()
                    if run and run["status"] == "RUNNING":
                        presenter.core.workflow.pipeline.control(run["run_id"], "pause")
            presenter.save_layout()
            if presenter.core:
                presenter.core.clear_read_caches()
        event.accept()

    def resizeEvent(self, event):
        compact = self.width() < 1040
        if hasattr(self, "brand_label"):
            self.brand_label.setVisible(not compact)
            self.header_divider.setVisible(not compact)
            self.project_title.setVisible(not compact)
        if compact and hasattr(self, "inspector_dock"):
            self.inspector_dock.hide()
            self.nav_dock.hide()
        super().resizeEvent(event)

    def update_count(self):
        self.count_timer.start(250)

    def measure_current(self):
        try:
            from ..text_metrics import measure
            value = measure(self.editor.toPlainText(), self.count_metric)
            self.count_label.setText(str(value["count"]) + " · " + value["description"].split("：", 1)[0])
            self.count_label.setToolTip(value["description"] + "\n" + value["counting_note"])
            presenter = getattr(self, "presenter", None)
            if presenter:
                presenter.show_word_goal(value["count"])
        except (ValueError, OSError, ImportError):
            self.count_label.setText("本地字数暂不可用，请检查组件")

    def filter_tree(self, query):
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            visible = False
            for j in range(parent.childCount()):
                child = parent.child(j)
                match = query.casefold() in child.text(0).casefold()
                child.setHidden(not match)
                visible |= match
            parent.setHidden(bool(query) and not visible and query.casefold() not in parent.text(0).casefold())
        item = self.tree.currentItem()
        if item and (item.isHidden() or (item.parent() and item.parent().isHidden())):
            self.tree.setCurrentItem(None)

    def select_tree_item(self, item, column):
        value = item.data(0, Qt.UserRole)
        if value:
            self.emit_action("select_document", document_id=value)

    def populate_demo(self):
        self.project_title.setText(demo.TITLE+" · 合成示例")
        self.book_name.setText(demo.TITLE)
        self.project_note.setText("界面原型 · 示例资料不来自私人作品")
        for title, children in [
            ("章节与草稿", [demo.CHAPTER, "第二章 · 来信"]),
            ("人物", ["林舟 · 修理工", "周岚 · 档案员"]),
            ("世界观与大纲", ["白榆镇", "文风与叙事约定"]),
            ("时间线与伏笔", ["六月四日返镇", "铜钥匙 · 尚未回收"]),
            ("独立审稿报告", ["第一章 · 示例审稿意见"]),
            ("正式版本", ["第一章 · 示例旧稿"]),
        ]:
            parent = QTreeWidgetItem([title])
            for child in children:
                parent.addChild(QTreeWidgetItem([child]))
            self.tree.addTopLevelItem(parent)
            parent.setExpanded(True)
        self.set_document("demo-chapter", demo.CHAPTER, demo.TEXT)
        self.review_output.setPlainText(demo.REVIEW)
        self.context_label.setText("合成资料副本\n只供原型展示，不代表宿主权限实测")
        self.context_list.addItems(["人物与知情边界\n林舟尚不知道蓝信", "世界观与文风\n克制、安静，不作道德总结", "作者待决\n钟楼建造年份", "未回收伏笔\n铜钥匙"])
        self.status_text.setText("合成示例 · 未连接模型 · 未产生 Token 数据")
        for item in [self.save_button, self.handoff_button, self.approval_button,
                     self.feedback_button, self.context_button, self.connection_button, *self.send_buttons.values()]:
            item.setEnabled(False)
            item.setToolTip("这是合成界面原型；实际服务将在后续阶段接入。")


from .shell import WorkbenchShell


class Workbench(WorkbenchShell, WorkbenchViews):
    """One window: native UI 2.0 shell reusing the existing bound views."""
