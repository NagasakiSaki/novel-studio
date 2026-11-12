"""UI 2.0 composition of existing views. No new model or filesystem services."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QStackedWidget, QVBoxLayout, QWidget, QFrame)
from .chrome import AnimatedPanel, Navigation, icon
from .theme import apply_theme


class WorkbenchShell:
    def _header(self):
        header = QWidget()
        header.setObjectName("header")
        self.header_layout = QHBoxLayout(header)
        self.header_layout.setContentsMargins(18, 10, 18, 10)
        self.nav_toggle = QPushButton()
        self.nav_toggle.setIcon(icon("menu"))
        self.nav_toggle.setObjectName("iconButton")
        self.nav_toggle.setToolTip("展开或收起导航")
        self.nav_toggle.setAccessibleName("展开或收起导航")
        self.nav_toggle.clicked.connect(self.toggle_navigation)
        self.header_layout.addWidget(self.nav_toggle)
        self.brand_label = QLabel("NOVEL STUDIO")
        self.brand_label.setObjectName("brand")
        self.header_layout.addWidget(self.brand_label)
        self.header_divider = QLabel("/")
        self.header_layout.addWidget(self.header_divider)
        self.project_title = QLabel()
        self.project_title.setTextFormat(Qt.PlainText)
        self.project_title.setMaximumWidth(250)
        self.header_layout.addWidget(self.project_title)
        self.breadcrumb = QLabel("作品")
        self.breadcrumb.setObjectName("muted")
        self.header_layout.addWidget(self.breadcrumb)
        self.header_layout.addStretch()
        self.theme_button = QPushButton("深色")
        self.theme_button.clicked.connect(self.toggle_theme)
        self.header_layout.addWidget(self.theme_button)
        self.drawer_toggle = QPushButton("详情")
        self.drawer_toggle.setToolTip("按需查看当前页面信息")
        self.drawer_toggle.clicked.connect(self.toggle_drawer)
        self.header_layout.addWidget(self.drawer_toggle)
        self.roles = QButtonGroup(self)
        self.role_buttons = {}
        for role, text in (("writer", "主笔"), ("editor", "责编")):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setChecked(role == self.role)
            button.clicked.connect(lambda checked=False, key=role: self.switch_role(key))
            self.roles.addButton(button)
            self.role_buttons[role] = button
        self.setMenuWidget(header)

    def _navigation(self):
        # Retain the existing document explorer and action contracts; it will
        # live in a relevant central view rather than occupy every page.
        super()._navigation()

    def _center(self):
        super()._center()
        self.center_view = self.takeCentralWidget()
        self.model_controls = self.center_view.layout().itemAt(0).widget()
        self.center_view.layout().removeWidget(self.model_controls)
        self.model_controls.setParent(None)
        self.tabs.tabBar().hide()
        index = self.tabs.indexOf(self.chat_stack)
        self.tabs.removeTab(index)
        self.chat_container = QWidget()
        chat = QVBoxLayout(self.chat_container)
        chat.setContentsMargins(18, 10, 18, 0)
        row = QHBoxLayout()
        for button in self.role_buttons.values():
            row.addWidget(button)
        row.addStretch()
        chat.addLayout(row)
        scope_row = QHBoxLayout()
        self.chat_scope = QComboBox()
        self.chat_scope.addItem("整部作品", "book")
        self.chat_scope.addItem("当前选中章节", "chapter")
        self.chat_operation = QComboBox()
        self.chat_chapter_id = None
        scope_row.addWidget(QLabel("影响范围"))
        scope_row.addWidget(self.chat_scope)
        scope_row.addWidget(QLabel("本次操作"))
        scope_row.addWidget(self.chat_operation, 1)
        chat.addLayout(scope_row)
        self.chat_scope_hint = QLabel("选择角色、范围和操作后发送；正式收录始终另行批准。")
        self.chat_scope_hint.setWordWrap(True)
        self.chat_scope_hint.setTextFormat(Qt.PlainText)
        chat.addWidget(self.chat_scope_hint)
        self.chat_scope.currentIndexChanged.connect(self.refresh_chat_scope)
        self.chat_operation.currentIndexChanged.connect(self.refresh_chat_scope)
        self.set_chat_operations()
        chat.addWidget(self.chat_stack, 1)
        for page in self.chat_pages.values():
            # Role and scope already appear above the conversation; remove the
            # duplicate inner heading so the composer fits compact windows.
            for index in (0, 1):
                page.layout().itemAt(index).widget().hide()
            page.layout().setContentsMargins(18, 6, 18, 12)
        # Removing a tab explicitly hides its page. Reparenting it into the
        # unified chat workspace does not clear that explicit hidden state.
        self.chat_stack.show()
        self.tabs.insertTab(index, self.chat_container, "创作对话")

    def _inspector(self):
        super()._inspector()
        old_navigation, old_inspector = self.nav_dock, self.inspector_dock
        self.file_explorer = old_navigation.widget()
        self.file_explorer.setParent(None)
        old_navigation.setWidget(None)
        self.context_view = old_inspector.widget()
        self.context_view.setParent(None)
        old_inspector.setWidget(None)
        for dock in (old_navigation, old_inspector):
            self.removeDockWidget(dock)
            dock.hide()
            dock.deleteLater()
        self.nav_dock = Navigation(self)
        self.nav_dock.selected.connect(self.navigate)
        self.nav_dock.settled.connect(self.persist_shell)
        self.inspector_dock = AnimatedPanel(self, width=300)
        self.inspector_dock.setObjectName("informationDrawer")
        drawer = QVBoxLayout(self.inspector_dock)
        drawer.setContentsMargins(15, 18, 15, 15)
        top = QHBoxLayout()
        self.drawer_title = QLabel("作品信息")
        self.drawer_title.setObjectName("section")
        top.addWidget(self.drawer_title, 1)
        close = QPushButton("×")
        close.setToolTip("收起详情")
        close.clicked.connect(self.toggle_drawer)
        top.addWidget(close)
        drawer.addLayout(top)
        self.drawer_metadata = QPlainTextEdit()
        self.drawer_metadata.setReadOnly(True)
        self.drawer_metadata.setMaximumHeight(300)
        drawer.addWidget(self.drawer_metadata)
        drawer.addWidget(self.context_view, 1)
        self.inspector_dock.settled.connect(self.persist_shell)
        self.files_index = self.tabs.addTab(self.file_explorer, "独立草稿与文件")
        self.clean_file_explorer()
        self.settings_page = self.build_settings()
        self.tabs.addTab(self.settings_page, "设置")
        self.export_page = self.build_export()
        self.tabs.addTab(self.export_page, "导出")
        self.shell_splitter = QSplitter(Qt.Horizontal)
        self.shell_splitter.setHandleWidth(4)
        self.shell_splitter.setChildrenCollapsible(False)
        self.shell_splitter.addWidget(self.nav_dock)
        self.shell_splitter.addWidget(self.center_view)
        self.shell_splitter.addWidget(self.inspector_dock)
        self.shell_splitter.setStretchFactor(1, 1)
        self.shell_splitter.setSizes([210, 1100, 0])
        self.shell_splitter.splitterMoved.connect(self.remember_widths)
        self.nav_dock.extent_changed.connect(self.rebalance_panels)
        self.inspector_dock.extent_changed.connect(self.rebalance_panels)
        self.nav_dock.settled.connect(self.rebalance_panels)
        self.inspector_dock.settled.connect(self.rebalance_panels)
        self.setCentralWidget(self.shell_splitter)
        self.inspector_dock.set_expanded(False, False)
        self.tabs.currentChanged.connect(self.sync_navigation)
        self.workflow_panel.pages.currentChanged.connect(self.sync_navigation)
        self.workflow_panel.view_changed.connect(self.refresh_shell)
        self.material_panel.selection_changed.connect(self.refresh_shell)
        self.navigation_page = "home"
        self.tabs.setCurrentWidget(self.workflow_panel)
        self.nav_dock.highlight("home")
        # A child of the view being moved cannot remain in the settings row.
        self.header_layout.insertWidget(self.header_layout.count() - 2, self.run_label)
        from .interactions import ObjectInteractions
        self.interactions = ObjectInteractions(self)

    def build_settings(self):
        page = QWidget()
        root = QHBoxLayout(page)
        root.setContentsMargins(26, 24, 26, 24)
        self.settings_categories = QListWidget()
        self.settings_categories.setMaximumWidth(170)
        self.settings_categories.addItems(["模型", "作品规模", "主笔风格", "责编审美", "用量与费用", "高级与安装"])
        root.addWidget(self.settings_categories)
        self.settings_content = QStackedWidget()
        root.addWidget(self.settings_content, 1)
        actions = [
            [("主笔模型与供应商", "model_manager", {"role": "writer"}), ("责编模型与供应商", "model_manager", {"role": "editor"})],
            [("调整作品规模与创作目标", "workflow_scale", {})],
            [("管理主笔风格", "writer_settings", {}), ("参考文风分析", "style_analysis", {})],
            [("管理责编审美与审稿标准", "editor_settings", {})],
            [("用量与缓存详情", "usage", {}), ("费用与价格设置", "costs", {})],
            [("兼容性与版本", "compatibility", {}), ("诊断日志", "diagnostic_logs", {}), ("开发者诊断与安装", "diagnostics", {}), ("版本历史与恢复", "history", {})],
        ]
        for index, group in enumerate(actions):
            content = QWidget()
            body = QVBoxLayout(content)
            heading = QLabel(self.settings_categories.item(index).text())
            heading.setObjectName("title")
            body.addWidget(heading)
            for text, action, data in group:
                button = QPushButton(text)
                button.clicked.connect(lambda checked=False, key=action, value=data: self.emit_action(key, **value))
                body.addWidget(button)
            if index == 0:
                hint = QLabel("两个角色分别保存，当前作品继续沿用；只有您主动操作才查询或连接模型。")
                hint.setWordWrap(True)
                body.addWidget(hint)
                native = QPushButton("Antigravity 连接与快捷参数")
                body.addWidget(native)
                self.model_controls.hide()
                body.addWidget(self.model_controls)
                native.clicked.connect(lambda: self.model_controls.setVisible(not self.model_controls.isVisible()))
                self.settings_role = QComboBox()
                self.settings_role.addItem("主笔", "writer")
                self.settings_role.addItem("责编", "editor")
                self.settings_role.activated.connect(self.settings_role_selected)
                old = self.model_controls.layout()
                while old.count():
                    item = old.takeAt(0)
                    if item.widget():
                        item.widget().setParent(self.model_controls)
                form_body = QWidget()
                form = QFormLayout(form_body)
                form.addRow("当前角色", self.settings_role)
                form.addRow("模型", self.model_selector)
                form.addRow("思考程度", self.effort_selector)
                row = QHBoxLayout()
                row.addWidget(self.refresh_models_button)
                row.addWidget(self.connection_button)
                form.addRow(row)
                old.addWidget(form_body)
                self.model_manager_button.setParent(self.retired_toolbar)
            if index == 5:
                self.reduce_motion = QCheckBox("减少界面动效")
                self.reduce_motion.toggled.connect(self.motion_changed)
                body.addWidget(self.reduce_motion)
            body.addStretch()
            self.settings_content.addWidget(content)
        self.settings_categories.currentRowChanged.connect(self.settings_content.setCurrentIndex)
        self.settings_categories.setCurrentRow(0)
        return page

    def build_export(self):
        page = QWidget()
        body = QVBoxLayout(page)
        body.setContentsMargins(44, 38, 44, 28)
        title = QLabel("把作品带到阅读者面前")
        title.setObjectName("title")
        body.addWidget(title)
        note = QLabel("选择正式作品、分卷或章节，查看内容预览后保存。支持 Word、PDF、EPUB、Markdown、TXT、HTML、RTF 和 ODT。")
        note.setWordWrap(True)
        body.addWidget(note)
        self.export_placeholder = note
        self.export_content = QWidget()
        self.export_content.setLayout(QVBoxLayout())
        self.export_content.layout().setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.export_content, 1)
        self.export_widget = None
        body.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        return scroll

    def bind_export_ui(self):
        presenter = getattr(self, "presenter", None)
        core = getattr(presenter, "core", None)
        if core is None:
            return
        if self.export_widget is not None and self.export_widget.core.project_id != core.project_id:
            self.export_widget.deleteLater()
            self.export_widget = None
        if self.export_widget is None:
            from .export_ui import ExportDialog
            self.export_widget = ExportDialog(core, self.document_id if self.document_area == "drafts" else None, self.export_content, embedded=True)
            self.export_content.layout().addWidget(self.export_widget)
            self.export_widget.show()
            self.export_placeholder.hide()
        elif not self.export_widget.job:
            self.export_widget.current_document = self.document_id if self.document_area == "drafts" else None
            self.export_widget.refresh_sources()
        self.export_widget.setEnabled(presenter.job is None)

    def clean_file_explorer(self):
        # Preserve existing action objects for Presenter enable/intent contracts;
        # their visible entry points now live at the relevant object/settings.
        self.retired_toolbar = QWidget(self)
        self.retired_toolbar.hide()
        layout = self.file_explorer.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().setParent(self.retired_toolbar)
            elif item.layout():
                row = item.layout()
                while row.count():
                    nested = row.takeAt(0)
                    if nested.widget():
                        nested.widget().setParent(self.retired_toolbar)
                row.deleteLater()
        self.book_name.setParent(self.file_explorer)
        self.project_note.setParent(self.file_explorer)
        self.book_name.show()
        self.project_note.show()
        layout.addWidget(self.book_name)
        layout.addWidget(self.project_note)
        row = QHBoxLayout()
        files_home = QPushButton("返回作品")
        files_home.clicked.connect(lambda: self.navigate("home"))
        row.addWidget(files_home)
        files_chapters = QPushButton("章节目录")
        files_chapters.clicked.connect(lambda: self.navigate("chapters"))
        row.addWidget(files_chapters)
        self.resume_document = QPushButton("继续当前草稿")
        self.resume_document.hide()
        self.resume_document.clicked.connect(lambda: self.show_document_page(self.document_page, "files"))
        row.addWidget(self.resume_document)
        self.new_draft_button.setParent(self.file_explorer)
        self.new_draft_button.show()
        self.new_draft_button.setObjectName("primary")
        row.addWidget(self.new_draft_button)
        layout.addLayout(row)
        for widget in (self.search, self.tree):
            widget.setParent(self.file_explorer)
            widget.show()
            layout.addWidget(widget)
        # Only draft save remains prominent; legacy promotion and manual
        # handoff remain reachable through this document's contextual menu.
        self.handoff_button.setParent(self.retired_toolbar)
        self.approval_button.setParent(self.retired_toolbar)
        self.word_settings_button.setParent(self.retired_toolbar)
        self.save_button.setObjectName("primary")
        self.document_more = QPushButton("稿件操作")
        menu = QMenu(self.document_more)
        self.document_actions = []
        for title, source in (("交给责编（建立独立副本）", self.handoff_button), ("预览正式版本差异", self.approval_button), ("本章字数与风格", self.word_settings_button)):
            action = menu.addAction(title)
            action.triggered.connect(source.click)
            self.document_actions.append((action, source))
        menu.aboutToShow.connect(self.refresh_document_actions)
        self.document_more.setMenu(menu)
        self.document_origin = "files"
        self.document_back_buttons = []
        for page in (self.document_page, self.review_page):
            back = QPushButton("返回草稿与文件")
            back.clicked.connect(self.return_from_document)
            page.layout().insertWidget(0, back, 0, Qt.AlignLeft)
            self.document_back_buttons.append(back)
        self.document_page.layout().addWidget(self.document_more, 0, Qt.AlignRight)
        self.chat_more = QPushButton("对话记录")
        chat_menu = QMenu(self.chat_more)
        item = chat_menu.addAction("查看当前角色的完整对话记录")
        item.triggered.connect(lambda: self.emit_action("transcripts"))
        self.chat_more.setMenu(chat_menu)
        guidance = chat_menu.addAction("管理已保存的全书指导")
        guidance.triggered.connect(lambda checked=False: self.emit_action("workflow_author_guidance"))
        self.chat_container.layout().itemAt(0).layout().addWidget(self.chat_more)

    def refresh_document_actions(self):
        for action, source in self.document_actions:
            action.setEnabled(source.isEnabled())

    def show_document_page(self, page, return_to=None):
        current = self.tabs.currentWidget()
        if return_to:
            self.document_origin = return_to
        elif current == self.file_explorer:
            self.document_origin = "files"
        elif current not in (self.document_page, self.review_page):
            self.document_origin = self.navigation_page
        origin_label = "草稿与文件" if self.document_origin == "files" else self.nav_dock.labels[self.document_origin]
        for button in self.document_back_buttons:
            button.setText("返回" + origin_label)
        self.tabs.setCurrentWidget(page)

    def return_from_document(self):
        self.navigate(self.document_origin)

    def _status(self):
        super()._status()
        self.status_text.setText("本机作品 · 本地记录已加载")
        self.usage_details = QWidget()
        row = QVBoxLayout(self.usage_details)
        row.setContentsMargins(0, 0, 0, 0)
        for item in self.metrics.values():
            self.statusBar().removeWidget(item)
            row.addWidget(item)
        self.inspector_dock.layout().addWidget(self.usage_details)

    def _shortcuts(self):
        for text, key, action in (("保存草稿", QKeySequence.Save, self.save_button.click), ("专注阅读", "Ctrl+Shift+F", self.toggle_focus)):
            item = QAction(text, self)
            item.setShortcut(key)
            item.triggered.connect(action)
            self.addAction(item)
        self.usage_action = QAction("用量与缓存详情", self)
        self.usage_action.triggered.connect(lambda: self.emit_action("usage"))
        self.cost_action = QAction("费用估算", self)
        self.cost_action.triggered.connect(lambda: self.emit_action("costs"))

    def settings_role_selected(self, index):
        self.switch_role(self.settings_role.itemData(index))
        self.navigate("settings")

    def switch_role(self, value):
        self.role = value
        self.role_buttons[value].setChecked(True)
        self.chat_stack.setCurrentWidget(self.chat_pages[value])
        self.set_chat_operations()
        self.tabs.setCurrentWidget(self.chat_container)
        self.editor.setReadOnly(value == "editor" or self.document_area != "drafts")
        self.save_button.setEnabled(value == "writer" and self.document_area == "drafts" and not self.demo_mode)
        self.emit_action("role_selected")

    def set_chat_operations(self):
        if not hasattr(self, "chat_operation"):
            return
        self.chat_operation.blockSignals(True)
        self.chat_operation.clear()
        options = [("讨论 · 不替换正文", "discuss")]
        options += [("执行本章修订 · 保存新草稿", "revise")] if self.role == "writer" else [("重新审核本章", "review"), ("重新制定本章计划", "plan")]
        options += [("保存全书指导 · 后续任务生效", "book_guidance")]
        for text, key in options:
            self.chat_operation.addItem(text, key)
        self.chat_operation.blockSignals(False)
        self.refresh_chat_scope()

    def refresh_chat_scope(self, *args):
        core = getattr(getattr(self, "presenter", None), "core", None)
        if core is None:
            self.chat_scope_hint.setText("请先打开作品；作者直接指令与审稿建议分别保存。")
            return
        cid = self.chat_chapter_id or self.workflow_panel.selected_chapter()
        row = core.workflow.store.read()["chapters"].get(cid)
        scope = f"第{row['volume']}卷第{row['number']}章 · {row['title'] or '标题待生成'}" if row and self.chat_scope.currentData() == "chapter" else "尚未选择章节" if self.chat_scope.currentData() == "chapter" else "整部作品"
        self.chat_scope_hint.setText(("主笔" if self.role == "writer" else "责编") + " · " + scope + "\n作者直接指令优先于责编建议；修改只进入草稿，正式大纲和正式作品另行批准。")
        if getattr(self, 'chat_source_chapter_id', None) == cid and getattr(self, 'chat_source_kind', None):
            self.chat_scope_hint.setText(self.chat_scope_hint.text() + ('\n本次来源：正式版本（只读）；修改另存草稿，审查另存报告。' if self.chat_source_kind == 'canonical' else '\n本次来源：当前草稿。'))

    def navigate(self, key):
        self.navigation_page = key
        if key in {"home", "outline", "chapters", "create"}:
            self.tabs.setCurrentWidget(self.workflow_panel)
            self.workflow_panel.pages.setCurrentIndex({"home": 0, "outline": 1, "chapters": 2, "create": 3}[key])
        elif key == "materials":
            self.tabs.setCurrentWidget(self.material_panel)
        elif key == "settings":
            self.tabs.setCurrentWidget(self.settings_page)
        elif key == "export":
            self.bind_export_ui()
            self.tabs.setCurrentWidget(self.export_page)
        elif key == "files":
            self.tabs.setCurrentWidget(self.file_explorer)
        elif key == "chat":
            self.tabs.setCurrentWidget(self.chat_container)
        self.sync_navigation()

    def sync_navigation(self, *args):
        if not hasattr(self, "navigation_page"):
            return
        current = self.tabs.currentWidget()
        if current == self.workflow_panel:
            key = ("home", "outline", "chapters", "create")[self.workflow_panel.pages.currentIndex()]
        else:
            key = {self.material_panel: "materials", self.settings_page: "settings", self.export_page: "export", self.chat_container: "chat"}.get(current, "chapters")
        self.navigation_page = key
        self.nav_dock.highlight(key)
        page_label = {self.file_explorer: "草稿与文件", self.document_page: "文稿",
                      self.review_page: "审稿与意见"}.get(current, self.nav_dock.labels[key])
        self.breadcrumb.setText(page_label)
        self.refresh_shell()
        if key == "chat":
            self.refresh_chat_scope()

    def refresh_shell(self):
        if not hasattr(self, "drawer_metadata"):
            return
        key = self.navigation_page
        self.drawer_title.setText(self.nav_dock.labels[key] + "信息")
        self.context_view.setVisible(key in {"materials", "create", "chat"})
        self.usage_details.setVisible(key in {"create", "settings", "chat"}) if hasattr(self, "usage_details") else None
        core = getattr(getattr(self, "presenter", None), "core", None)
        if core is None:
            self.drawer_metadata.setPlainText("打开作品后，这里会显示当前页面的信息。")
            return
        state = core.workflow.store.read()
        approved = state["approved_outline"]
        lines = [core.title, "", "正式大纲：" + ("已确认" if approved else "尚未确认")]
        if key == "outline":
            lines += ["历史版本：" + str(len(state["outlines"])), "新候选不自动替换正式大纲。"]
            lines += [self.workflow_panel.versions.currentText(), self.workflow_panel.scope_button.text()]
        elif key == "chapters":
            rows = list(state["chapters"].values())
            lines += ["正式收录：" + str(sum(bool(r.get("canonical")) for r in rows)) + " 章", "审稿不等于作者批准。"]
            current = next((r for r in rows if r["chapter_id"] == self.workflow_panel.selected_chapter()), None)
            if current:
                lines += [f"第{current['volume']}卷 · 第{current['number']}章", current["title"], self.workflow_panel.chapter_metadata.text()]
        elif key == "create":
            run = state["runs"].get(state["active_run"])
            lines += ["本次任务：" + ("已保存，可查看创作页" if run else "尚未开始"), "写作与审核分别使用独立角色。"]
            lines += [self.workflow_panel.status.text(), self.workflow_panel.alert.text()]
        elif key == "chat":
            lines += [self.chat_scope_hint.text(), "本次操作：" + self.chat_operation.currentText(), "完整历史在对话记录中，主笔与责编分开保存。"]
        elif key == "export":
            lines += ["默认选择正式作品；草稿须明确选择。", "导出不改变原文，不覆盖同名目标。"]
        elif key == "settings":
            lines += ["设置属于当前作品，两个角色独立保存。", "连接测试与模型发送均须主动操作。"]
        elif key == "materials":
            lines += [self.material_panel.info.text() if self.material_panel.current else "选择一份资料查看来源、版本与批准范围。"]
        else:
            lines += ["作品和备份保存在您自己的工作区。", "正式变更始终由作者确认。"]
        self.drawer_metadata.setPlainText("\n".join(lines))

    def toggle_navigation(self):
        self.nav_dock.set_expanded(not self.nav_dock.expanded)

    def motion_changed(self, reduced):
        for panel in (self.nav_dock, self.inspector_dock):
            panel.animation.setDuration(0 if reduced else 170)
        self.persist_shell()

    def toggle_drawer(self):
        self.refresh_shell()
        self.inspector_dock.set_expanded(not self.inspector_dock.expanded)

    def toggle_focus(self):
        self.nav_dock.set_expanded(not self.nav_dock.expanded)
        self.inspector_dock.set_expanded(False)

    def remember_widths(self, *args):
        if self.nav_dock.expanded:
            self.nav_dock.expanded_width = max(172, min(360, self.nav_dock.width()))
        if self.inspector_dock.expanded:
            self.inspector_dock.expanded_width = max(240, min(420, self.inspector_dock.width()))
        self.persist_shell()

    def rebalance_panels(self):
        if not hasattr(self, "shell_splitter"):
            return
        left = min(self.nav_dock.maximumWidth(), self.nav_dock.expanded_width)
        right = min(self.inspector_dock.maximumWidth(), self.inspector_dock.expanded_width) if self.inspector_dock.isVisible() else 0
        self.shell_splitter.setSizes([left, max(1, self.shell_splitter.width() - left - right - (8 if right else 4)), right])

    def persist_shell(self):
        if hasattr(self, "shell_splitter"):
            self.layout_changed.emit(self.capture_layout())

    def capture_layout(self):
        return {"ui_version": 2, "theme": self.theme, "role": self.role, "page": getattr(self, "navigation_page", "home"),
            "sidebar_expanded": self.nav_dock.expanded, "sidebar_width": self.nav_dock.expanded_width,
            "drawer_expanded": self.inspector_dock.expanded, "drawer_width": self.inspector_dock.expanded_width,
            "width": self.width(), "height": self.height(), "reduce_motion": self.reduce_motion.isChecked()}

    def restore_layout(self, state):
        self.reduce_motion.setChecked(state.get("reduce_motion") is True)
        if state.get("theme") in {"light", "dark"} and state["theme"] != self.theme:
            self.toggle_theme()
        for panel, key, lower, upper in ((self.nav_dock, "sidebar", 172, 360), (self.inspector_dock, "drawer", 240, 420)):
            width = state.get(key + "_width")
            if type(width) is int:
                panel.expanded_width = max(lower, min(upper, width))
            expanded = state.get(key + "_expanded", key == "sidebar")
            panel.set_expanded(expanded if type(expanded) is bool else key == "sidebar", False)
        screen = QApplication.primaryScreen().availableGeometry()
        width, height = state.get("width", 1380), state.get("height", 900)
        if type(width) is int and type(height) is int:
            self.resize(max(720, min(width, screen.width() - 24)), max(460, min(height, screen.height() - 40)))
        if state.get("role") in {"writer", "editor"}:
            self.switch_role(state["role"])
        page = state.get("page")
        self.navigate(page if isinstance(page, str) and page in self.nav_dock.labels else "home")

    def toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
        self.theme_button.setText("浅色" if self.theme == "dark" else "深色")
        apply_theme(QApplication.instance(), self.theme)
        self.nav_dock.update_style(self.theme == "dark")
        self.nav_toggle.setIcon(icon("menu", self.theme == "dark"))
        self.persist_shell()

    def resizeEvent(self, event):
        if hasattr(self, "nav_dock") and hasattr(self.nav_dock, "expanded") and self.width() < 1080:
            if self.nav_dock.expanded:
                self.nav_dock.set_expanded(False, False)
            if self.inspector_dock.expanded:
                self.inspector_dock.set_expanded(False, False)
        if hasattr(self, "brand_label"):
            self.brand_label.setVisible(self.width() >= 900)
            self.header_divider.setVisible(self.width() >= 900)
        # Bypass old responsive code which hid both navigation entry points.
        from PySide6.QtWidgets import QMainWindow
        QMainWindow.resizeEvent(self, event)

    def closeEvent(self, event):
        super().closeEvent(event)
        if event.isAccepted():
            self.nav_dock.animation.stop()
            self.inspector_dock.animation.stop()
            self.count_timer.stop()
