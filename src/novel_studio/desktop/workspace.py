"""Author-centred views over the existing workflow services and intent routes."""
from PySide6.QtCore import Qt, Signal, QDateTime
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QLineEdit, QTreeWidget, QTreeWidgetItem)
from ..core import CoreError
from ..workflow_review import direction_text
from ..workflow_plans import plan_text
from .chapter_tree import ChapterTree
from .workflow_display import plain, STATUS, PHASE, model_label
from .ui_state import EVENTS, next_step, outline_scope, scope_label
from .task_scope import choice_label, run_scope
from .context_menu import SelectionActions, attach_items, populate, entry
from .object_actions import chapter_actions, outline_actions
from .demo import OPENING, TITLE
from .project_ui import HomeRow, WritingExampleDialog


def command(text, action, emit, data=None, primary=False):
    button = QPushButton(text)
    button.setObjectName("primary" if primary else "quietButton")
    button.clicked.connect(lambda checked=False: emit(action, **(data or {})))
    return button


def menu_button(text, entries, emit):
    button = QPushButton(text)
    button.setObjectName("quietButton")
    menu = QMenu(button)
    for title, action, data in entries:
        item = menu.addAction(title)
        item.triggered.connect(lambda checked=False, key=action, values=data: emit(key, **(values() if callable(values) else values)))
    button.setMenu(menu)
    return button


class WorkflowPanel(QWidget):
    action_requested = Signal(str, dict)
    view_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.core, self.loading, self.busy = None, False, False
        self.rows, self.current_version = [], None
        self.manual_scope = None
        self.next_action = None
        self.pages = QTabWidget()
        self.pages.tabBar().hide()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.pages)
        for title, builder in (("作品", self.build_home), ("大纲", self.build_outline), ("章节", self.build_chapters), ("创作", self.build_runtime)):
            self.pages.addTab(builder(), title)
        self.pages.currentChanged.connect(lambda index: self.view_changed.emit())
        self.bind(None)

    def emit(self, action, **data):
        self.action_requested.emit(action, data)

    def page_body(self, title):
        page = QWidget()
        body = QVBoxLayout(page)
        body.setContentsMargins(32, 26, 32, 24)
        body.setSpacing(16)
        heading = plain(title, "title")
        body.addWidget(heading)
        return page, body

    def build_home(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page = QWidget()
        page.setObjectName("homePage")
        scroll.setWidget(page)
        outer = QHBoxLayout(page)
        outer.setContentsMargins(28, 26, 28, 30)
        column = QWidget()
        column.setMaximumWidth(1180)
        outer.addStretch(1)
        outer.addWidget(column, 100)
        outer.addStretch(1)
        body = QVBoxLayout(column)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)

        lead = HomeRow(760, 16)
        hero = QFrame()
        hero.setObjectName("heroCard")
        content = QVBoxLayout(hero)
        content.setContentsMargins(26, 24, 26, 24)
        content.setSpacing(12)
        self.home_stage = plain("", "homeStage")
        content.addWidget(self.home_stage)
        top = QHBoxLayout()
        self.home_book = plain("", "homeTitle")
        top.addWidget(self.home_book, 1)
        self.project_more = menu_button("作品选项", [("新建作品", "new_project", {}), ("打开其他作品", "open_project", {})], self.emit)
        top.addWidget(self.project_more, 0, Qt.AlignTop)
        content.addLayout(top)
        self.home_hint = plain("", "homeDescription")
        content.addWidget(self.home_hint)
        self.home_task_scope = plain("", "homeScope")
        content.addWidget(self.home_task_scope)
        content.addStretch()
        actions = HomeRow(490, 8)
        self.start_button = QPushButton("新建作品")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.primary_action)
        actions.row.addWidget(self.start_button, 0, Qt.AlignLeft)
        self.home_open = command("打开已有作品", "open_project", self.emit)
        self.home_open.setObjectName("homeButton")
        actions.row.addWidget(self.home_open, 0, Qt.AlignLeft)
        self.home_ai = command("首次 AI 协作", "workflow_first_ai", self.emit)
        self.home_ai.setObjectName("homeButton")
        actions.row.addWidget(self.home_ai, 0, Qt.AlignLeft)
        self.home_manual = command("写草稿", "ui_navigate", self.emit, {"page": "files", "role": "writer"})
        self.home_manual.setObjectName("homeButton")
        actions.row.addWidget(self.home_manual, 0, Qt.AlignLeft)
        actions.row.addStretch()
        content.addWidget(actions)
        lead.row.addWidget(hero, 3)

        self.home_sample = QFrame()
        self.home_sample.setObjectName("sampleCard")
        sample = QVBoxLayout(self.home_sample)
        sample.setContentsMargins(24, 22, 24, 22)
        sample.setSpacing(12)
        sample.addWidget(plain("离线写作示例", "sampleKicker"))
        sample.addWidget(plain(TITLE, "sampleTitle"))
        sample.addWidget(plain(OPENING, "sampleExcerpt"))
        sample.addStretch()
        example = QPushButton("阅读草稿与审稿意见")
        example.setObjectName("sampleButton")
        example.clicked.connect(self.show_writing_example)
        sample.addWidget(example, 0, Qt.AlignLeft)
        lead.row.addWidget(self.home_sample, 2)
        body.addWidget(lead)

        self.home_actions = SelectionActions()
        self.home_actions.hide()
        body.addWidget(self.home_actions)

        self.project_summary = QFrame()
        self.project_summary.setObjectName("homeSection")
        summary = QVBoxLayout(self.project_summary)
        summary.setContentsMargins(20, 18, 20, 18)
        summary.setSpacing(12)
        summary.addWidget(plain("创作进度", "homeSectionTitle"))
        self.book_summary = plain("", "homeDescription")
        summary.addWidget(self.book_summary)
        self.book_progress = QProgressBar()
        self.book_progress.setAccessibleName("正式收录进度")
        self.book_progress.setTextVisible(False)
        self.book_progress.setRange(0, 100)
        summary.addWidget(self.book_progress)
        self.activity_section = QWidget()
        activity = QVBoxLayout(self.activity_section)
        activity.setContentsMargins(0, 2, 0, 0)
        activity.addWidget(plain("最近活动", "section"))
        self.recent_activity = plain("", "muted")
        activity.addWidget(self.recent_activity)
        summary.addWidget(self.activity_section)
        body.addWidget(self.project_summary)

        self.project_settings = HomeRow(710)
        self.writer_card = command("AI 主笔", "model_manager", self.emit, {"role": "writer"})
        self.editor_card = command("AI 责编", "model_manager", self.emit, {"role": "editor"})
        self.scale_card = command("篇幅建议", "workflow_scale", self.emit)
        for card in (self.writer_card, self.editor_card, self.scale_card):
            card.setObjectName("summaryCard")
            card.setMinimumHeight(62)
            card.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            self.project_settings.row.addWidget(card, 1)
        body.addWidget(self.project_settings)

        self.recent_section = QFrame()
        self.recent_section.setObjectName("homeSection")
        recent = QVBoxLayout(self.recent_section)
        recent.setContentsMargins(20, 16, 20, 16)
        recent.setSpacing(8)
        recent_header = QHBoxLayout()
        self.recent_heading = plain("最近作品", "homeSectionTitle")
        recent_header.addWidget(self.recent_heading, 1)
        self.recent_archives = command("已归档作品", "recent_archives", self.emit)
        recent_header.addWidget(self.recent_archives)
        recent.addLayout(recent_header)
        self.recent_projects = QListWidget()
        self.recent_projects.setAccessibleName("最近打开的作品")
        self.recent_projects.setMaximumHeight(150)
        self.recent_projects.itemActivated.connect(self.open_recent_item)
        recent.addWidget(self.recent_projects)
        self.recent_open = QPushButton("打开所选作品")
        self.recent_open.setEnabled(False)
        self.recent_open.clicked.connect(lambda: self.open_recent_item(self.recent_projects.currentItem()))
        self.recent_projects.currentItemChanged.connect(lambda item, previous: self.recent_open.setEnabled(item is not None))
        recent.addWidget(self.recent_open, 0, Qt.AlignLeft)
        self.recent_section.hide()
        body.addWidget(self.recent_section)
        body.addStretch(1)
        return scroll

    def show_writing_example(self):
        dialog = WritingExampleDialog(self)
        dialog.exec()

    def open_recent_item(self, item):
        if item is not None:
            self.emit("open_recent", path=item.data(Qt.UserRole))

    def primary_action(self):
        if self.next_action:
            self.emit(self.next_action["action"], **self.next_action["data"])
        elif self.core is None:
            self.emit("new_project")

    def build_outline(self):
        page, root = self.page_body("与责编一起，找到故事的方向")
        splitter = QSplitter()
        self.version_list = QListWidget()
        self.version_list.setMaximumWidth(250)
        self.version_list.setMinimumWidth(110)
        self.version_list.currentRowChanged.connect(self.select_visible_version)
        attach_items(self.version_list, self.outline_object_actions, lambda key, data:self.emit(key, **data))
        splitter.addWidget(self.version_list)
        right = QWidget()
        plan = QVBoxLayout(right)
        plan.setContentsMargins(12, 0, 0, 0)
        top = QHBoxLayout()
        self.outline_status = plain("构思可以留空。", "muted")
        top.addWidget(self.outline_status, 1)
        self.outline_more = menu_button("版本操作", [
            ("查看完整差异", "outline_diff", lambda: {"version_id": self.versions.currentData()}),
            ("恢复为新候选", "outline_restore", lambda: {"version_id": self.versions.currentData()}),
            ("编辑字段并另存", "outline_edit", lambda: {"version_id": self.versions.currentData()}),
            ("恢复已保存结果（不发请求）", "outline_recover", {})], self.emit)
        top.addWidget(self.outline_more)
        plan.addLayout(top)
        self.outline_object_bar = SelectionActions()
        plan.addWidget(self.outline_object_bar)
        self.versions = QComboBox(self)
        self.versions.hide()
        self.versions.currentIndexChanged.connect(self.show_version)
        self.outline_view = QPlainTextEdit()
        self.outline_view.setReadOnly(True)
        self.outline_view.setObjectName("outlineDocument")
        self.outline_view.setPlaceholderText("导入预设后，责编可以根据资料独立构思。每一轮修改都会保留版本。")
        outline_split = QSplitter()
        self.outline_tree = QTreeWidget()
        self.outline_tree.setHeaderLabel("大纲结构")
        self.outline_tree.setMinimumWidth(140)
        self.outline_tree.itemClicked.connect(self.jump_outline)
        outline_split.addWidget(self.outline_tree)
        outline_split.addWidget(self.outline_view)
        outline_split.setStretchFactor(1, 1)
        outline_split.setSizes([180, 600])
        plan.addWidget(outline_split, 1)
        self.instruction = QPlainTextEdit()
        self.instruction.setObjectName("composer")
        self.instruction.setPlaceholderText("和责编讨论：例如“修改第二卷”“重写第一卷第三章”“调整结局”……留空也能构思。")
        self.instruction.setMaximumHeight(112)
        self.instruction.textChanged.connect(self.update_scope_hint)
        plan.addWidget(self.instruction)
        actions = QHBoxLayout()
        self.scope_button = QPushButton("整书规划")
        self.scope_button.setToolTip("查看或明确选择本轮修改范围")
        self.scope_button.clicked.connect(self.choose_scope)
        actions.addWidget(self.scope_button)
        actions.addStretch()
        self.outline_send = QPushButton("与责编讨论")
        self.outline_send.clicked.connect(self.generate)
        actions.addWidget(self.outline_send)
        self.outline_approve = command("确认正式大纲", "outline_approve", self.emit, {})
        # The selected version is resolved at the moment the author clicks.
        self.outline_approve.clicked.disconnect()
        self.outline_approve.clicked.connect(lambda: self.emit("outline_approve", version_id=self.versions.currentData()))
        actions.addWidget(self.outline_approve)
        plan.addLayout(actions)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)
        self.scope, self.volume, self.chapter = QComboBox(self), QSpinBox(self), QSpinBox(self)
        for title, key in (("整书", "book"), ("指定卷", "volume"), ("指定章", "chapter")):
            self.scope.addItem(title, key)
        self.volume.setRange(1, 100)
        self.chapter.setRange(1, 1000)
        for control in (self.scope, self.volume, self.chapter):
            control.hide()
        return page

    def select_visible_version(self, row):
        if not self.loading and row >= 0:
            self.versions.setCurrentIndex(row)

    def show_version(self, *args):
        if self.loading or self.core is None or not self.versions.currentData():
            return
        try:
            record = self.core.workflow.planning.get(self.versions.currentData())
            self.current_version = record
            self.outline_view.setPlainText(record["text"])
            state = self.core.workflow.store.read()
            approved = state["approved_outline"]
            formal = bool(approved and approved["version_id"] == record["version_id"])
            request = record["instruction"] or "根据预设自主构思"
            if request.startswith("作者恢复历史大纲"):
                request = "作者恢复的历史规划，已另存为新候选"
            timestamp = QDateTime.fromSecsSinceEpoch(int(record["time"])).toString("yyyy-MM-dd HH:mm")
            run = state["runs"].get(state["active_run"])
            using = state["outlines"].get(run["outline_version"], {}).get("number") if run else None
            effective = state["outlines"].get(approved["version_id"], {}).get("number") if approved else None
            stale = sum(r.get("editor_plan") is not None and self.core.workflow.store.blob(r["editor_plan"])["outline_version"] != approved["version_id"] for r in state["chapters"].values()) if approved else 0
            self.outline_status.setText(self.versions.currentText() + " · " + timestamp + "\n" + request +
                f"\n当前正式依据：{'第' + str(effective) + '版' if effective else '尚未确认'}" + (f" · 本次任务使用第{using}版" if using else "") +
                (f"\n{stale} 份旧章节计划需按新大纲重新规划；旧稿保留，不静默换版。" if stale else ""))
            self.outline_tree.clear()
            parents = []
            for line, text in enumerate(record["text"].splitlines()):
                if text.startswith("#") and " " in text:
                    hashes, heading = text.split(" ", 1)
                    if not 1 <= len(hashes) <= 6 or set(hashes) != {"#"}:
                        continue
                    node = QTreeWidgetItem([heading])
                    node.setData(0, Qt.UserRole, line)
                    while parents and parents[-1][0] >= len(hashes):
                        parents.pop()
                    parents[-1][1].addChild(node) if parents else self.outline_tree.addTopLevelItem(node)
                    parents.append((len(hashes), node))
            self.outline_tree.expandToDepth(1)
            self.version_list.setCurrentRow(self.versions.currentIndex())
            self.outline_approve.setVisible(not formal)
            self.outline_approve.setEnabled(not self.busy)
            self.outline_approve.setObjectName("primary")
            self.outline_send.setObjectName("primary" if formal else "quietButton")
            for button in (self.outline_send, self.outline_approve):
                button.style().unpolish(button)
                button.style().polish(button)
            self.update_scope_hint()
        except (CoreError, ValueError, OSError) as error:
            self.current_version = None
            self.outline_approve.hide()
            self.outline_view.clear()
            self.outline_tree.clear()
            self.outline_status.setText("无法安全加载此版本：" + str(error))
        self.outline_more.setEnabled(self.current_version is not None)
        if hasattr(self, 'outline_object_bar'):
            values = self.outline_object_actions(self.version_list.currentItem()) if self.current_version else []
            self.outline_object_bar.setVisible(bool(values))
            self.outline_object_bar.bind('所选大纲', values, lambda key, data:self.emit(key, **data))

    def outline_object_actions(self, item):
        if item is None or self.core is None:
            return []
        vid = self.versions.itemData(self.version_list.row(item))
        if not vid:
            return []
        record = self.core.workflow.planning.get(vid)
        approved = self.core.workflow.store.read().get('approved_outline')
        return outline_actions(record, bool(approved and approved['version_id'] == vid), self.core.project_id, self.busy)

    def jump_outline(self, item, column=0):
        block = self.outline_view.document().findBlockByNumber(item.data(0, Qt.UserRole))
        cursor = self.outline_view.textCursor()
        cursor.setPosition(block.position())
        self.outline_view.setTextCursor(cursor)
        self.outline_view.ensureCursorVisible()

    def resolved_scope(self):
        if self.manual_scope:
            return dict(self.manual_scope)
        return outline_scope(self.instruction.toPlainText(), self.current_version["value"] if self.current_version else None)

    def update_scope_hint(self):
        try:
            self.scope_button.setText(scope_label(self.resolved_scope()) + "")
        except CoreError:
            self.scope_button.setText("请明确范围")

    def choose_scope(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("本轮修改范围")
        layout = QVBoxLayout(dialog)
        layout.addWidget(plain("只修改选定范围，范围外由现有大纲服务保持原样。选择“按本轮文字识别”可恢复自然语言范围。"))
        form = QFormLayout()
        kind, volume, chapter = QComboBox(), QSpinBox(), QSpinBox()
        for title, key in (("按本轮文字识别", "auto"), ("整书规划", "book"), ("只改指定卷", "volume"), ("只改指定章", "chapter")):
            kind.addItem(title, key)
        volume.setRange(1, 100)
        chapter.setRange(1, 1000)
        current = self.manual_scope or {"kind": "auto", "volume": 1, "chapter": 1}
        kind.setCurrentIndex(max(0, kind.findData(current["kind"])))
        volume.setValue(current.get("volume", 1))
        chapter.setValue(current.get("chapter", 1))
        for name, field in (("修改范围", kind), ("卷号", volume), ("章号", chapter)):
            form.addRow(name, field)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            selected = kind.currentData()
            self.manual_scope = None if selected == "auto" else {"kind": selected}
            if selected in {"volume", "chapter"}:
                self.manual_scope["volume"] = volume.value()
            if selected == "chapter":
                self.manual_scope["chapter"] = chapter.value()
            self.update_scope_hint()

    def generate(self):
        try:
            scope = self.resolved_scope()
        except CoreError as error:
            QMessageBox.warning(self, "请先明确修改范围", str(error))
            return
        self.emit("outline_generate", instruction=self.instruction.toPlainText(), base_version=self.versions.currentData(), scope=scope)

    def build_chapters(self):
        page, body = self.page_body("回到文字本身")
        split = QSplitter()
        left = QWidget()
        listing = QVBoxLayout(left)
        listing.setContentsMargins(0, 0, 12, 0)
        self.chapter_search = QLineEdit()
        self.chapter_search.setPlaceholderText("搜索章名或正文…")
        self.chapter_search.textChanged.connect(self.filter_chapters)
        listing.addWidget(self.chapter_search)
        self.chapter_filter = QComboBox()
        for title, key in (("全部章节", "all"), ("待作者确认", "pending"), ("待审核", "review"), ("正式章节", "formal"), ("尚未创作", "planned")):
            self.chapter_filter.addItem(title, key)
        self.chapter_filter.currentIndexChanged.connect(self.filter_chapters)
        listing.addWidget(self.chapter_filter)
        self.chapter_empty = plain("", "muted")
        listing.addWidget(self.chapter_empty)
        self.table = ChapterTree()
        self.table.setObjectName("chapterList")
        self.table.setHeaderLabels(["选择", "卷", "章", "标题", "状态", "字数"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.header().hide()
        for column in (1, 2, 4, 5):
            self.table.hideColumn(column)
        self.table.setColumnWidth(0, 28)
        self.table.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self.show_chapter)
        self.table.itemChanged.connect(self.update_acceptance)
        attach_items(self.table, self.chapter_object_actions, lambda key, data:self.emit(key, **data))
        listing.addWidget(self.table, 1)
        listing.addWidget(command("独立草稿与文件", "ui_navigate", self.emit, {"page": "files"}))
        split.addWidget(left)
        right = QWidget()
        reading = QVBoxLayout(right)
        reading.setContentsMargins(8, 0, 0, 0)
        top = QHBoxLayout()
        self.chapter_heading = plain("选择一章，安心阅读", "section")
        top.addWidget(self.chapter_heading, 1)
        self.chapter_more = menu_button("更多", [("打开草稿编辑", "workflow_document", lambda: {"chapter_id": self.selected_chapter()}),
            ("修改目录中的章节名或卷名", "workflow_rename", lambda: {"chapter_id": self.selected_chapter()}),
            ("调整目录归属与阅读顺序", "workflow_place", lambda: {"chapter_id": self.selected_chapter()}),
            ("查看本章旧稿与旧审稿", "workflow_chapter_history", lambda: {"chapter_id": self.selected_chapter()}),
            ("导出本章", "workflow_export_chapter", lambda: {"chapter_id": self.selected_chapter()}),
            ("采用当前草稿并重新审核", "workflow_revise", lambda: {"chapter_id": self.selected_chapter()}),
            ("管理后续指导", "workflow_guidance", {}), ("登记已有正式章节", "workflow_register", {}),
            ("恢复中断收录记录", "workflow_recover_batch", {}), ("版本历史与恢复", "history", {})], self.emit)
        top.addWidget(self.chapter_more)
        reading.addLayout(top)
        self.chapter_metadata = plain("", "muted")
        reading.addWidget(self.chapter_metadata)
        self.chapter_object_bar = SelectionActions()
        reading.addWidget(self.chapter_object_bar)
        self.chapter_more.menu().aboutToShow.connect(self.rebuild_chapter_menu)
        self.chapter_actions = QGridLayout()
        self.chapter_action_buttons = {}
        for index, (title, action) in enumerate((("修改正文", "workflow_document"), ("交给责编审核", "workflow_chapter_review"),
            ("与主笔讨论", "workflow_writer_chat"), ("与责编讨论", "workflow_editor_chat"), ("按责编建议修改", "workflow_repair"))):
            button = command(title, action, self.emit)
            button.clicked.disconnect()
            button.clicked.connect(lambda checked=False, key=action: self.chapter_primary(key))
            self.chapter_actions.addWidget(button, index // 3, index % 3)
            self.chapter_action_buttons[action] = button
            # These common actions are already visible in the shared selection bar.
            # Keep their routing for compatibility without showing a second row.
            if action in {'workflow_document', 'workflow_chapter_review', 'workflow_writer_chat', 'workflow_editor_chat'}:
                button.hide()
        reading.addLayout(self.chapter_actions)
        self.chapter_tabs = QTabWidget()
        self.chapter_views = {}
        for key, title in (("text", "正文"), ("plan", "责编计划"), ("review", "审稿"), ("repair", "修订过程"), ("direction", "下章方向"), ("history", "历史版本")):
            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setObjectName("chapterReading")
            self.chapter_views[key] = view
            self.chapter_tabs.addTab(view, title)
        reading.addWidget(self.chapter_tabs, 1)
        self.accept_button = QPushButton("收录为正式章节")
        self.accept_button.setObjectName("primary")
        self.accept_button.clicked.connect(lambda: self.emit("workflow_accept", chapter_ids=self.acceptance_ids()))
        reading.addWidget(self.accept_button, 0, Qt.AlignRight)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 800])
        body.addWidget(split, 1)
        return page

    def chapter_primary(self, key):
        row = next((r for r in self.rows if r['chapter_id'] == self.selected_chapter()), None)
        if not row:
            return
        formal = row['status'] == 'ARCHIVED' or not row.get('draft') and bool(row.get('canonical'))
        data = {'chapter_id':row['chapter_id'], '_project_id':self.core.project_id, 'source_kind':'canonical' if formal else 'drafts'}
        if key == 'workflow_document':
            self.emit('object_writer_revise' if formal else 'object_chapter_edit', **data)
        elif key == 'workflow_chapter_review':
            self.emit('object_editor_review', **data)
        else:
            self.emit(key, **data)

    def selected_chapter(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if not self.table.isRowHidden(row) else None
        return item.data(Qt.UserRole) if item else None

    def checked_chapters(self, *, visible_only=False):
        return [self.table.item(row, 0).data(Qt.UserRole) for row in range(self.table.rowCount())
                if (not visible_only or not self.table.isRowHidden(row)) and self.table.item(row, 0).checkState() == Qt.Checked]

    def acceptance_ids(self):
        return self.checked_chapters(visible_only=True) or ([self.selected_chapter()] if self.selected_chapter() else [])

    def filter_chapters(self, *args):
        kind = self.chapter_filter.currentData()
        query = self.chapter_search.text().strip().casefold()
        for index, row in enumerate(self.rows):
            show = kind == "all" or kind == "pending" and row["status"] == "AUTHOR_APPROVAL_PENDING" or kind == "formal" and row["status"] == "ARCHIVED" or kind == "review" and row["status"] in {"DRAFT_SAVED", "REVIEW_COMPLETED"} or kind == "planned" and row["status"] == "PLANNED"
            if query:
                content = row["title"] + " " + row["volume_title"]
                source = row.get("draft") or row.get("canonical")
                if source and self.core:
                    try:
                        content += " " + self.core.document(source["document_id"])["text"]
                    except (ValueError, OSError):
                        pass
                show = show and query in content.casefold()
            self.table.setRowHidden(index, not show)
        self.table.update_groups()
        first = next((i for i in range(self.table.rowCount()) if not self.table.isRowHidden(i)), -1)
        if self.table.currentRow() < 0 or self.table.isRowHidden(self.table.currentRow()):
            self.table.selectRow(first)
        if self.core is None:
            message = "先在作品页新建或打开作品。"
        elif query or kind != "all":
            message = "目录中没有匹配章节，可清空搜索或更换筛选。"
        else:
            message = "确认大纲后，章节会列在这里。手写稿件可在「独立草稿与文件」查看。"
        self.chapter_empty.setText(message)
        self.chapter_empty.setVisible(first < 0)
        self.show_chapter()

    def update_acceptance(self, *args):
        if self.loading:
            return
        ids = self.acceptance_ids()
        rows = [r for r in self.rows if r["chapter_id"] in ids]
        pending = bool(rows) and all(r["status"] == "AUTHOR_APPROVAL_PENDING" for r in rows)
        self.accept_button.setVisible(pending)
        self.accept_button.setEnabled(pending and not self.busy)
        self.accept_button.setText("收录为正式章节" if len(ids) <= 1 else f"收录选中的 {len(ids)} 章")

    def show_chapter(self):
        try:
            self._show_chapter()
        except (CoreError, ValueError, OSError, KeyError) as error:
            self.chapter_heading.setText("本章版本需要检查")
            self.chapter_metadata.setText("已保留原文件，未覆盖或自动重发模型请求。")
            for view in self.chapter_views.values():
                view.setPlainText("无法核验当前章的计划或稿件版本：" + str(error))
            for button in self.chapter_action_buttons.values():
                button.setEnabled(False)
                button.setToolTip("请先检查来源文件或从历史恢复有效版本。")
            self.accept_button.setEnabled(False)
        self.refresh_chapter_object()

    def chapter_object_actions(self, item):
        if not self.core:
            return []
        cid = item.data(0, Qt.UserRole)
        row = next((r for r in self.rows if r['chapter_id'] == cid), None)
        if row:
            return chapter_actions(row, self.core.project_id, self.busy)
        volume = item.data(3, Qt.UserRole)
        if isinstance(volume, dict) and volume.get('document_id'):
            data = {'document_id':volume['document_id'], '_project_id':self.core.project_id}
            return [entry('阅读原文', 'object_document_read', data), entry('导出所选正式版本', 'object_document_export', data),
                    entry('登记到章节目录（原文不改）', 'workflow_register', data)] if not self.busy else [entry('阅读原文', 'object_document_read', data)]
        if volume is None:
            return []
        data = {'volume':volume, '_project_id':self.core.project_id}
        values = [entry('打开本卷' if volume != 'book' else '阅读全书', 'object_volume_open', data),
                  entry('展开 / 收起', 'object_volume_expand', data), entry('查看创作进度', 'object_volume_progress', data)]
        if not self.busy:
            values.append(entry('导出全书' if volume == 'book' else '导出本卷', 'object_volume_export', data))
            if volume != 'book':
                values += [entry('调整卷名', 'object_volume_rename', data)]
                run = self.core.workflow.pipeline.active()
                if not run or run['status'] in {'COMPLETED', 'STOPPED'}:
                    values += [entry('在本卷新建章节草稿', 'object_volume_new_draft', data), entry('调整卷阅读顺序', 'object_volume_move', data)]
        return values

    def rebuild_chapter_menu(self):
        menu = self.chapter_more.menu()
        menu.clear()
        item = self.table.currentItem()
        if item:
            populate(menu, self.chapter_object_actions(item), lambda key, data:self.emit(key, **data))

    def refresh_chapter_object(self):
        item = self.table.currentItem()
        values = self.chapter_object_actions(item) if item and not item.isHidden() else []
        self.chapter_more.setEnabled(bool(values))
        title = '可用操作' if values else '选择章节或卷后显示可用操作'
        if item and not self.selected_chapter() and values:
            self.chapter_heading.setText(item.text(3))
            self.chapter_metadata.setText('所选分组或文件的操作显示在下方；正式作品不会直接改写。')
        self.chapter_object_bar.bind(title, values, lambda key, data:self.emit(key, **data))

    def _show_chapter(self):
        if self.loading:
            return
        row = next((r for r in self.rows if r["chapter_id"] == self.selected_chapter()), None)
        self.update_acceptance()
        if row is None or self.core is None:
            for view in self.chapter_views.values():
                view.setPlainText("选择目录中的章节后，这里会显示正文、责编计划、审稿和可用操作。")
            self.chapter_heading.setText("请选择章节")
            self.chapter_metadata.setText("展开卷目录，或清空搜索查看其他章节。")
            for button in self.chapter_action_buttons.values():
                button.setEnabled(False)
                button.setToolTip("请先选择章节。")
            return
        self.chapter_heading.setText(row["title"] or "本章标题将在创作后自动填写")
        count = (row.get("local_length") or {}).get("count")
        saved_plan = self.core.workflow.store.blob(row["editor_plan"]) if row.get("editor_plan") else None
        target = saved_plan["value"]["word_range"]["target"] if saved_plan else self.core.workflow.scale.goal(row["chapter_id"])["target"]
        source = row.get("canonical") if row["status"] == "ARCHIVED" else row.get("draft") or row.get('canonical')
        origin = "正式稿 · 只读" if source and source == row.get("canonical") else "草稿 · 工作副本" if source else "尚无正文"
        self.chapter_metadata.setText(f"第{row['volume']}卷 {row['volume_title']} · 第{row['number']}章   {STATUS.get(row['status'], '待检查')}\n{origin} · 本地字数 {count if count is not None else '尚未核对'} · 建议 {target or '未设'}")
        try:
            text = self.core.workflow.chapters.checked_document(source, "canonical" if source == row.get('canonical') else "drafts")["text"] if source else "本章尚未创作。"
            review_source = (row.get('formal_review') or row.get('review')) if row['status'] == 'ARCHIVED' else row.get('review')
            review = self.core.workflow.chapters.checked_document(review_source, "reports")["text"] if review_source else "本章尚无独立审稿意见。"
            if row['status'] == 'ARCHIVED' and review_source:
                review = ('正式稿独立审查\n\n' if row.get('formal_review') else '收录前草稿审稿记录（不是新一次正式稿审查）\n\n') + review
            self.chapter_views["text"].setPlainText(text)
            self.chapter_views["review"].setPlainText(review)
        except (ValueError, OSError) as error:
            self.chapter_views["text"].setPlainText("无法核验当前版本，请检查源稿或明确采用修订稿：" + str(error))
            self.chapter_views["review"].clear()
        self.chapter_views["direction"].setPlainText(direction_text(row["next_direction"]) if row.get("next_direction") else "下一章方向尚未准备。完成审稿后会自动传递给主笔，无需复制。")
        self.chapter_views["plan"].setPlainText(plan_text(saved_plan["value"]) if saved_plan else "等待责编制定本章执行计划。包括第一章在内，创作前都会先完成规划。")
        repairs = [self.core.workflow.store.blob(ref) for ref in row.get("revision_plans", [])]
        self.chapter_views["repair"].setPlainText("\n\n——\n\n".join(f"第{i + 1}轮\n" + plan_text(value["value"], True) for i, value in enumerate(repairs)) or "本章尚未自动返修。重要问题会先由责编给出方案，主笔修订并复审，最多两轮。")
        histories = row.get("revision_history", [])
        self.chapter_views["history"].setPlainText(f"当前：{'正式版本另行保护；当前草稿独立保存' if row.get('canonical') else '待确认草稿'}\n自动返修：{row.get('repair_rounds', 0)}轮\n独立审稿记录：{len(row.get('review_history', []))}份\n旧章节版本：{len(histories)}份\n\n" +
            "\n".join(f"旧版本 {i + 1} · 独立保存" for i, ref in enumerate(histories)) + "\n\n更多 → 查看本章旧稿与旧审稿，可阅读完整旧稿并恢复为待审草稿。恢复正式版本仍需查看差异并批准。")
        for action, button in self.chapter_action_buttons.items():
            needs_draft = action in {"workflow_document", "workflow_chapter_review", "workflow_repair"}
            enabled = not self.busy and (bool(row.get("draft") or action != 'workflow_repair' and row.get('canonical')) if needs_draft else True)
            button.setEnabled(enabled)
            button.setToolTip("当前模型步骤保存后即可操作。" if self.busy else "本章尚无草稿，请先开始创作。" if needs_draft and not row.get("draft") else "")
        self.chapter_action_buttons["workflow_repair"].setVisible(bool(row.get("repair_pending") or row.get("review_result") and self.core.workflow.store.blob(row["review_result"]).get("repair_flags")))
        self.chapter_action_buttons['workflow_document'].setText('提出修改要求（另存草稿）' if row['status']=='ARCHIVED' else '编辑草稿')
        self.view_changed.emit()

    def build_runtime(self):
        page, body = self.page_body("让主笔与责编接力创作")
        top = QHBoxLayout()
        top.addStretch()
        top.addWidget(command("主笔与责编对话", "ui_navigate", self.emit, {"page": "chat"}))
        self.run_more = menu_button("更多", [("重新请求未完成的步骤", "workflow_retry", {}), ("检查并恢复中断锁", "workflow_recover_locks", {}), ("管理后续创作指导", "workflow_guidance", {})], self.emit)
        top.addWidget(self.run_more)
        body.addLayout(top)
        self.status = plain("尚未开始创作", "heroTitle")
        self.alert = plain("先在作品页确认大纲，再选择本次创作范围。", "muted")
        body.addWidget(self.status)
        body.addWidget(self.alert)
        selection = QHBoxLayout()
        selection.addWidget(plain('本次任务范围'))
        self.task_scope = QComboBox()
        for title, mode in (('全书 · 按已确认大纲','book'),('后续指定卷数','volumes'),('后续指定章节数','chapters')):
            self.task_scope.addItem(title, mode)
        self.task_count = QSpinBox()
        self.task_count.setRange(1,1000)
        selection.addWidget(self.task_scope,1)
        selection.addWidget(self.task_count)
        body.addLayout(selection)
        self.task_scope_summary = plain('本次一键任务：全书（按已确认大纲）', 'section')
        body.addWidget(self.task_scope_summary)
        body.addWidget(plain('点击开始即按上方范围执行，无需再次确认。最多两轮草稿返修；未决审稿问题保留在报告中，继续后续草稿。正式收录仍由你批准。模型请求可能计费。', 'muted'))
        self.task_scope.currentIndexChanged.connect(self.update_task_choice)
        self.task_count.valueChanged.connect(self.update_task_choice)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        body.addWidget(self.progress)
        self.role_progress = plain("主笔与责编各自使用已保存的模型。", "section")
        body.addWidget(self.role_progress)
        self.run_word_count = plain("", "muted")
        body.addWidget(self.run_word_count)
        controls = QHBoxLayout()
        self.pause_button = command("暂停", "workflow_pause", self.emit, primary=True)
        self.resume_button = command("继续创作", "workflow_resume", self.emit, primary=True)
        self.stop_button = command("停止", "workflow_stop", self.emit)
        self.run_start = command("一键开始创作", "workflow_start", self.emit, primary=True)
        self.review_pending_button = command("查看待确认章节", "ui_navigate", self.emit, {"page": "chapters", "filter": "pending"})
        self.retry_button = command('重试当前步骤（可能追加计费）','workflow_retry',self.emit)
        for button in (self.pause_button, self.resume_button, self.stop_button, self.run_start, self.retry_button, self.review_pending_button):
            controls.addWidget(button)
        controls.addStretch()
        body.addLayout(controls)
        self.last_review = plain("完成一章后，独立审稿结果会保留在章节工作区。", "muted")
        body.addWidget(self.last_review)
        self.log_toggle = QPushButton("查看任务记录")
        body.addWidget(self.log_toggle, 0, Qt.AlignLeft)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.hide()
        self.log_toggle.clicked.connect(lambda: self.log.setVisible(not self.log.isVisible()))
        body.addWidget(self.log, 1)
        body.addStretch()
        return page

    def task_choice(self):
        return {'mode':self.task_scope.currentData(), 'count':self.task_count.value(),
                'creative_mode':'continuous','review_policy':'continue_drafts'}

    def update_task_choice(self, *unused):
        self.task_count.setVisible(self.task_scope.currentData() != 'book')
        text = '本次一键任务：' + choice_label(self.task_scope.currentData(),self.task_count.value())
        self.task_scope_summary.setText(text)
        self.home_task_scope.setText(text)
        if self.core and not self.busy:
            state=self.core.workflow.store.read()
            run=state['runs'].get(state['active_run'])
            self.bind_runtime(state,run)
        if self.start_button.text().startswith('一键开始创作'):
            self.start_button.setText('一键开始创作 · '+choice_label(self.task_scope.currentData(),self.task_count.value()))

    def bind(self, core, busy=False, documents=None):
        changed = self.core is not None and (core is None or self.core.project_id != core.project_id)
        if changed:
            self.instruction.clear()
            self.manual_scope = None
            self.current_version = None
            self.chapter_filter.setCurrentIndex(0)
            self.chapter_search.clear()
            self.outline_view.clear()
            self.outline_tree.clear()
        self.core, self.busy = core, busy
        if core is not None and getattr(self,'scope_project',None) != core.project_id:
            pref=core.workflow.store.read().get('creation_preferences',{})
            self.task_scope.blockSignals(True);self.task_count.blockSignals(True)
            self.task_scope.setCurrentIndex(max(0,self.task_scope.findData(pref.get('mode','book'))))
            self.task_count.setValue(pref.get('count',1))
            self.task_scope.blockSignals(False);self.task_count.blockSignals(False)
            self.scope_project=core.project_id
        self.task_count.setVisible(self.task_scope.currentData() != 'book')
        if core is None:
            self.task_scope.setEnabled(False);self.task_count.setEnabled(False);self.retry_button.hide()
            self.task_scope_summary.setText('尚未打开作品')
            self.home_task_scope.setText('尚未打开作品')
        self.home_sample.setVisible(core is None)
        self.home_actions.setVisible(core is not None)
        self.project_summary.setVisible(core is not None)
        self.project_settings.setVisible(core is not None)
        self.home_stage.setVisible(core is not None or busy)
        self.project_more.setVisible(core is not None)
        self.project_more.setEnabled(not busy)
        self.home_open.setVisible(core is None)
        self.home_open.setEnabled(not busy)
        self.home_ai.setVisible(core is not None and not busy)
        self.recent_section.setEnabled(not busy)
        self.outline_send.setEnabled(core is not None and not busy)
        for card in (self.writer_card, self.editor_card, self.scale_card):
            card.setEnabled(core is not None and not busy)
        self.chapter_more.setEnabled(core is not None and not busy)
        self.run_more.setEnabled(core is not None and not busy)
        if core is None:
            self.rows = []
            self.table.setRowCount(0)
            self.versions.clear()
            self.version_list.clear()
            self.outline_view.clear()
            self.outline_tree.clear()
            self.current_version = None
            self.home_book.setText("开始写你的小说")
            self.home_stage.setText("正在打开作品")
            self.home_hint.setText("先在本机写草稿，准备好后再让 AI 主笔和责编参与。作品保存在你选择的文件夹。")
            self.home_manual.hide()
            self.book_progress.setValue(0)
            self.start_button.setText("新建作品")
            self.start_button.setEnabled(not busy)
            self.next_action = None
            self.outline_approve.hide()
            self.outline_more.setEnabled(False)
            self.outline_object_bar.hide()
            self.filter_chapters()
            self.outline_send.setObjectName("primary")
            self.bind_runtime(None, None)
            return
        state = core.workflow.store.read()
        selected_version, selected_chapter = self.versions.currentData(), self.selected_chapter()
        checked = set(self.checked_chapters())
        self.loading = True
        self.versions.clear()
        self.version_list.clear()
        versions = core.workflow.planning.versions()
        for record in versions:
            value = core.workflow.planning.get(record["version_id"])
            formal = bool(state["approved_outline"] and state["approved_outline"]["version_id"] == record["version_id"])
            title = f"第{record['number']}版" + (" · 当前正式" if formal else " · 草稿" if value["model"].get("provider") == "author" else " · 待确认")
            self.versions.addItem(title, record["version_id"])
            name = next((line.lstrip("# ") for line in value["text"].splitlines() if line.strip()), "作品大纲")[:36]
            summary = (value["value"].get("synopsis") or value["value"].get("concept") or "完整内容见右侧")[:48]
            self.version_list.addItem(name + "\n" + title + "\n" + QDateTime.fromSecsSinceEpoch(int(value["time"])).toString("MM-dd HH:mm") + "\n" + summary)
        self.versions.setCurrentIndex(max(0, self.versions.findData(selected_version)))
        self.rows = sorted(state["chapters"].values(), key=lambda row: (row.get("directory", {}).get("volume", row["volume"]), row.get("directory", {}).get("position", row["number"]), row["ordinal"]))
        if documents is None:
            documents = core.documents()
        self.table.populate(core.title, self.rows, STATUS, checked, core.workflow.chapters.unindexed(documents))
        selected_index = -1
        for index, row in enumerate(self.rows):
            if row["chapter_id"] == selected_chapter:
                selected_index = index
        self.loading = False
        self.show_version()
        if not versions:
            self.outline_view.clear()
            self.outline_tree.clear()
            self.current_version = None
            self.outline_status.setText("还没有大纲。写下故事构思，或从已批准资料开始。")
            self.outline_approve.hide()
            self.outline_more.setEnabled(False)
            self.outline_object_bar.hide()
            self.outline_send.setObjectName("primary")
        self.filter_chapters()
        if selected_index >= 0 and not self.table.isRowHidden(selected_index):
            self.table.selectRow(selected_index)
        self.show_chapter()
        next_action = next_step(state, busy)
        activity = getattr(getattr(self.window(), "presenter", None), "job_message", "") or ""
        if busy and ("大纲" in activity or "规划" in activity):
            next_action = {"label": "查看大纲规划进度", "action": "ui_navigate", "data": {"page": "outline"}, "stage": "责编正在规划大纲", "hint": "完成后会保留为新候选，不会自动替换正式大纲。"}
        elif busy and "导出" in activity:
            next_action = {"label": "查看导出进度", "action": "ui_navigate", "data": {"page": "export"}, "stage": "正在保存导出文件", "hint": "请等待本地文件生成完成，原始作品不会改动。"}
        self.next_action = next_action
        self.home_book.setText(core.title)
        self.home_stage.setText(next_action["stage"])
        self.home_hint.setText(next_action["hint"])
        writing_is_primary = next_action["data"].get("page") == "files"
        self.home_manual.setVisible(not writing_is_primary and not busy)
        self.start_button.setText(next_action["label"])
        if next_action['action']=='workflow_start':
            self.start_button.setText('一键开始创作 · '+choice_label(self.task_scope.currentData(),self.task_count.value()))
        self.start_button.setEnabled(not busy or next_action["action"] == "ui_navigate")
        formal = sum(bool(r.get("canonical")) for r in self.rows)
        drafts = sum(row["area"] == "drafts" for row in documents)
        execution = core.workflow.execution.describe(state)
        total = execution["chapters"]
        closed = {v["number"] for v in (core.workflow.execution.current(state) or {}).get("volumes", []) if v["closed"] or v["detailed"]}
        complete_volumes = sum(volume in closed and all(r.get("canonical") for r in self.rows if r["volume"] == volume) for volume in {r["volume"] for r in self.rows})
        metrics = [f"草稿 {drafts} 份", f"正式收录 {formal} 章"]
        if total:
            metrics.append(f"{'全书规划' if execution['count_known'] else '当前目录'} {total} 章")
        if complete_volumes:
            metrics.append(f"已完成 {complete_volumes} 卷")
        self.book_summary.setText(" · ".join(metrics))
        self.book_progress.setValue(round(formal / total * 100) if total else 0)
        self.book_progress.setVisible(bool(total))
        self.book_progress.setToolTip("正式收录进度 · " + str(formal) + " / " + str(total) + ("（当前目录）" if not execution["count_known"] else ""))
        scale = state["scale"]
        for role, card, title in (("writer", self.writer_card, "AI 主笔"), ("editor", self.editor_card, "AI 责编")):
            selected = core.models.selected(role) if core.models else {}
            catalog = core.models.catalog_for(role) if core.models else []
            name = next((r["display_name"] for r in catalog if r["model_id"] == selected.get("model_id")), "已保存选择" if selected.get("model_id") else "请选择模型")
            card.setText(title + "\n" + name)
            card.setToolTip(model_label(core, role))
        self.scale_card.setText(f"篇幅建议\n每章约 {scale['chapter_target']} 字")
        self.scale_card.setToolTip(f"全局建议 {scale['total_chapters'] or '待定'} 章 / {scale['total_volumes'] or '待定'} 卷，以作品大纲为准")
        activities = [EVENTS[e["operation"]] for e in state["events"] if e["operation"] in EVENTS]
        self.activity_section.setVisible(bool(activities))
        self.recent_activity.setText("\n".join("· " + item for item in activities[-4:][::-1]))
        self.bind_runtime(state, state["runs"].get(state["active_run"]))
        self.view_changed.emit()

    def bind_runtime(self, state, run):
        status = run["status"] if run else None
        idle=run is None or status in {'COMPLETED','STOPPED'}
        if not idle and run.get('mode') in {'book','volumes','chapters'}:
            self.task_scope.blockSignals(True);self.task_count.blockSignals(True)
            self.task_scope.setCurrentIndex(self.task_scope.findData(run['mode']))
            self.task_count.setValue(run.get('requested_count',1))
            self.task_scope.blockSignals(False);self.task_count.blockSignals(False)
            self.task_count.setVisible(run['mode']!='book')
        self.task_scope.setEnabled(self.core is not None and not self.busy and idle)
        self.task_count.setEnabled(self.task_scope.isEnabled())
        task_text = ('上次一键任务：' if idle else '当前一键任务：') + run_scope(run) if run else '准备执行：' + choice_label(self.task_scope.currentData(),self.task_count.value())
        if run:
            task_text += ' · 已完成 '+str(len(run['completed_chapters']))+' 章'
            pending=sum(bool(state['chapters'][cid].get('review_pending_author')) for cid in run['chapter_ids'])
            if pending:task_text+=' · '+str(pending)+' 章有审稿问题待查看'
        elif self.core and state.get('approved_outline'):
            approved=self.core.workflow.planning.approved()
            volumes=len(approved['value']['volumes'])
            task_text += ' · 大纲 '+str(volumes)+' 卷' if volumes else ' · 分卷随故事形成'
        self.task_scope_summary.setText(task_text)
        self.home_task_scope.setText(task_text)
        self.home_task_scope.setVisible(bool(run or (state and state["approved_outline"])))
        self.pause_button.setVisible(status == "RUNNING")
        local_context_failure = status == 'FAILED' and run.get('active_attempt') is None and (run.get('error') or {}).get('code') == 'CONTEXT_LIMIT'
        self.resume_button.setVisible(status in {"PAUSED", "BLOCKED"} or local_context_failure)
        self.resume_button.setEnabled(not self.busy)
        self.resume_button.setText('保留当前草稿并继续' if run and (run.get('block') or {}).get('kind')=='review' else '继续创作')
        self.retry_button.setVisible(status=='FAILED' and not local_context_failure)
        self.retry_button.setEnabled(not self.busy)
        self.stop_button.setVisible(status in {"RUNNING", "PAUSED", "BLOCKED", "FAILED", "PAUSE_REQUESTED"})
        self.run_start.setVisible(run is None or status in {"COMPLETED", "STOPPED"})
        self.run_start.setEnabled(self.core is not None and not self.busy)
        self.review_pending_button.setVisible(bool(status == "COMPLETED" and any(
            state["chapters"].get(cid, {}).get("status") == "AUTHOR_APPROVAL_PENDING" for cid in run["completed_chapters"])))
        if run is None:
            self.status.setText("尚未开始创作")
            self.alert.setText("确认大纲后，点击开始即执行上方范围，无需再次确认。")
            self.role_progress.setText("主笔与责编各自使用已保存的模型。")
            self.run_word_count.clear()
            self.log.clear()
            self.progress.setValue(0)
            self.progress.setFormat("尚未开始本次创作")
            return
        total, done = len(run["chapter_ids"]), len(run["completed_chapters"])
        dynamic = run.get("execution_scope", {}).get("dynamic", False)
        self.progress.setRange(0, 100)
        self.progress.setValue(round(done / total * 100) if total else 0)
        self.progress.setFormat(f"本次进度 · {done} / {total} 章" if not dynamic else f"已完成 {done} 章 · " + ("本次范围已结束" if status in {"COMPLETED", "STOPPED"} else "按大纲逐步形成目录"))
        if dynamic and status == "RUNNING":
            self.progress.setRange(0, 0)
        if run["index"] < total:
            chapter = state["chapters"][run["chapter_ids"][run["index"]]]
            phase = self.core.workflow.pipeline.phase(chapter)
            actor = PHASE.get(phase, "正在保存进度")
            self.status.setText(f"{actor} · 第{chapter['volume']}卷 第{chapter['number']}章" if status == "RUNNING" else STATUS.get(status, "任务待检查"))
            writer_state = "正在修订" if phase == "CHAPTER_REVISING" else "正在写作" if phase == "CHAPTER_WRITING" else "草稿已保存" if chapter.get("draft") else "等待责编计划"
            editor_state = "正在规划" if phase == "CHAPTER_PLANNING" else "正在制定修订方案" if phase == "REVISION_PLANNING" else "正在复审" if phase == "CHAPTER_REVIEWING" and chapter.get("repair_rounds") else "正在审阅" if phase == "CHAPTER_REVIEWING" else "按阶段接力"
            self.role_progress.setText("主笔：" + writer_state + "    责编：" + editor_state)
            self.run_word_count.setText("本章已核对字数：" + str((chapter.get("local_length") or {}).get("count", "待正文保存后统计")))
        else:
            self.status.setText("本次创作任务已完成" if status == "COMPLETED" else STATUS.get(status, "任务已保存"))
            self.role_progress.setText("主笔草稿与责编意见均已保留。")
        issue = run.get("block") or run.get("error") or {}
        self.alert.setText(issue.get("message", run.get("assist_notice", "本步已保存，可查看章节后继续。") if status == "PAUSED" else "每章先规划，最多两轮草稿返修。未决问题在报告中保留，正式收录仍由作者决定。"))
        reviewed = [state["chapters"][key] for key in run["chapter_ids"] if state["chapters"][key].get("review")]
        self.last_review.setText(("最近审稿：第" + str(reviewed[-1]["number"]) + "章 · " + reviewed[-1]["title"] + "，可在章节工作区原位查看。") if reviewed else "责编完成后，审稿和下一章方向会自动保存。")
        lines = [EVENTS[e["operation"]] for e in state["events"] if e["operation"] in EVENTS]
        self.log.setPlainText("\n".join(lines[-35:]) + "\n\n重新打开作品不会自动恢复收费请求。")
