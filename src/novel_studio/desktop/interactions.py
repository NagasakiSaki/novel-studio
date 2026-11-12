"""Selection feedback across pages; UI-only, no model or file mutation logic."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu, QPushButton
from .context_menu import SelectionActions, attach_items, entry, populate
from .ui_state import transcript_text
from ..core import CoreError


class ObjectInteractions:
    def __init__(self, window):
        self.w, self.ranges = window, {}
        self.book_bar = window.workflow_panel.home_actions
        self.material_bar = SelectionActions()
        window.material_panel.layout().insertWidget(2, self.material_bar)
        archives = QPushButton('资料回收区')
        archives.clicked.connect(lambda: self.dispatch('object_material_archives', self.data()))
        window.material_panel.layout().itemAt(1).layout().addWidget(archives)
        self.document_bar = SelectionActions()
        window.document_page.layout().insertWidget(1, self.document_bar)
        self.file_bar = SelectionActions()
        window.file_explorer.layout().insertWidget(3, self.file_bar)
        self.text_bar = SelectionActions()
        window.document_page.layout().addWidget(self.text_bar)
        self.message_bar = SelectionActions()
        window.chat_container.layout().insertWidget(2, self.message_bar)
        attach_items(window.tree, self.document_actions, self.dispatch)
        window.tree.currentItemChanged.connect(lambda:self.refresh_document())
        attach_items(window.material_panel.tree, self.material_actions, self.dispatch)
        window.material_panel.selection_changed.connect(self.refresh_material)
        attach_items(window.workflow_panel.recent_projects, self.recent_actions, self.dispatch)
        attach_items(window.workflow_panel.outline_tree, lambda item:window.workflow_panel.outline_object_actions(window.workflow_panel.version_list.currentItem()), self.dispatch,
                     select=lambda item:window.workflow_panel.jump_outline(item))
        self.attach_label(window.workflow_panel.home_book, self.book_actions)
        self.attach_label(window.workflow_panel.task_scope_summary, self.task_actions)
        for view in [window.editor, window.review_output, window.workflow_panel.outline_view, *window.workflow_panel.chapter_views.values(), window.material_panel.content]:
            self.attach_text(view)
        window.editor.selectionChanged.connect(self.refresh_text)
        for role, view in window.chat_outputs.items():
            self.attach_text(view, role)
            view.cursorPositionChanged.connect(lambda r=role:self.refresh_message(r))
        window.workflow_panel.view_changed.connect(self.refresh)
        self.refresh()

    def core(self):
        return getattr(getattr(self.w, 'presenter', None), 'core', None)

    def data(self, **values):
        core = self.core()
        return {'_project_id':core.project_id, **values} if core else values

    def dispatch(self, key, data):
        if key == 'copy':
            QApplication.clipboard().setText(data['text'])
        else:
            self.w.emit_action(key, **data)

    def attach_label(self, widget, provider):
        widget.setContextMenuPolicy(Qt.CustomContextMenu)
        def requested(point):
            values = provider()
            if values:
                menu = populate(QMenu(widget), values, self.dispatch)
                menu.exec(widget.mapToGlobal(point))
                menu.deleteLater()
        widget.customContextMenuRequested.connect(requested)

    def book_actions(self):
        core = self.core()
        if not core:
            return []
        values = [entry('查看作品信息', 'object_project_info', self.data()), entry('查看章节', 'ui_navigate', self.data(page='chapters'))]
        if not self.w.presenter.job:
            values += [entry('导出作品', 'object_volume_export', self.data(volume='book')),
                entry('重命名作品（不移动文件夹）', 'object_project_rename', self.data(), '作品管理'),
                entry('复制完整备份（ZIP）', 'object_project_backup', self.data(), '作品管理'),
                entry('从作品列表移除（保留文件）', 'object_project_remove', self.data(), '作品管理')]
        return values

    def recent_actions(self, item):
        path = item.data(Qt.UserRole)
        return [entry('打开作品', 'open_recent', {'path':path}), entry('查看保存位置', 'recent_info', {'path':path}),
                entry('归档到最近作品之外（保留文件）', 'recent_archive', {'path':path})] if path else []

    def document_row(self, item):
        core = self.core()
        identifier = item.data(0, Qt.UserRole) if item else None
        if not core or not identifier:
            return None
        try:
            return core.document(identifier)
        except CoreError as error:
            if error.code != 'PROJECT_SCOPE_DENIED':
                raise
            return None

    def document_actions(self, item):
        row = self.document_row(item)
        if not row:
            return []
        core = self.core()
        data = self.data(document_id=row['document_id'])
        values = [entry('阅读' if row['area'] != 'drafts' else '打开草稿', 'object_document_read', data)]
        chapter = next((c for c in core.workflow.chapters.catalog() if any(c.get(k, {}).get('document_id') == row['document_id'] for k in ('draft', 'canonical') if c.get(k))), None)
        if chapter:
            from .object_actions import chapter_actions
            values = chapter_actions(chapter, core.project_id, bool(self.w.presenter.job), 'canonical' if row['area'] == 'canonical' else 'drafts')
        elif row['area'] == 'drafts' and not self.w.presenter.job:
            values += [entry('交给责编（独立副本）', 'handoff', data), entry('查看正式版本差异并批准', 'preview_promotion', data), entry('导出这份草稿', 'object_document_export', data)]
        elif row['area'] == 'canonical' and row['relative'].startswith('Canonical/Final-Manuscript/'):
            values += [entry('导出正式版本', 'object_document_export', data), entry('查看正式版本历史', 'history', data)]
        return values

    def material_actions(self, item):
        value = item.data(0, Qt.UserRole)
        if not value or not self.core():
            return []
        identifier, section = value
        data = self.data(material_id=identifier)
        values = [entry('预览原文', 'object_material_preview', data), entry('查看资料信息', 'object_material_info', data)]
        if not self.w.presenter.job:
            record = self.core().materials.get(identifier)
            values += [entry('更新资料', 'update_material', data), entry('调整常驻与检索范围', 'material_policy', data),
                       entry('查看版本历史与恢复', 'history', data, '版本与管理'),
                       entry('重命名显示名称', 'object_material_rename', data, '版本与管理'),
                       entry('修改资料类型', 'object_material_type', data, '版本与管理'),
                       entry('从创作上下文排除' if record.get('context_enabled', True) else '启用于创作上下文', 'object_material_toggle', data),
                       entry('查看本轮引用范围', 'preview_material_context', self.data(), '版本与管理'),
                       entry('删除到资料回收区（保留原件）', 'object_material_retire', data, '版本与管理')]
        return values

    def task_actions(self):
        panel = self.w.workflow_panel
        core = self.core()
        run = core.workflow.pipeline.active() if core else None
        if not run:
            return []
        values = []
        for button, key in [(panel.pause_button, 'workflow_pause'), (panel.resume_button, 'workflow_resume'),
                (panel.retry_button, 'workflow_retry'), (panel.stop_button, 'workflow_stop')]:
            if not button.isHidden() and button.isEnabled():
                values.append(entry(button.text(), key, self.data(_run_id=run['run_id'])))
        return values

    def refresh(self):
        self.book_bar.bind('作品操作', self.book_actions(), self.dispatch, maximum=2)
        self.refresh_material()
        self.refresh_document()
        self.refresh_text()

    def refresh_material(self):
        tree = self.w.material_panel.tree
        item = tree.currentItem()
        values = self.material_actions(item) if item else []
        self.material_bar.bind('所选资料的操作' if values else '选择一份资料后，可预览、更新或调整使用范围', values, self.dispatch)

    def refresh_document(self):
        item = self.w.tree.currentItem()
        selected = self.document_actions(item)
        self.file_bar.bind("已选 · " + item.text(0) if selected else "选择草稿、正式稿或审稿报告。", selected, self.dispatch)
        current = None
        for i in range(self.w.tree.topLevelItemCount()):
            group = self.w.tree.topLevelItem(i)
            for j in range(group.childCount()):
                node = group.child(j)
                if node.data(0, Qt.UserRole) == self.w.document_id:
                    current = node
        values = self.document_actions(current)
        self.document_bar.bind('当前稿件的操作' if values else '打开文件后显示阅读、编辑与版本操作', values, self.dispatch)

    def document_chapter(self):
        core = self.core()
        identifier = self.w.document_id
        return next((r['chapter_id'] for r in core.workflow.chapters.catalog() if any(r.get(k, {}).get('document_id') == identifier for k in ('draft', 'canonical') if r.get(k))), None) if core and identifier else None

    def quote_actions(self, text, chapter_id=None):
        if not text or not self.core():
            return []
        data = self.data(text=text, **({'chapter_id':chapter_id} if chapter_id else {}))
        return [entry('引用给主笔', 'object_quote_writer', data), entry('引用给责编', 'object_quote_editor', data),
                entry('让主笔重写选段', 'object_quote_rewrite', data), entry('让主笔润色选段', 'object_quote_polish', data),
                entry('让责编分析选段', 'object_quote_review', data)]

    def refresh_text(self):
        text = self.w.editor.textCursor().selectedText().replace('\u2029', '\n')
        values = self.quote_actions(text, self.document_chapter())
        self.text_bar.setVisible(bool(values))
        self.text_bar.bind(f'已选中 {len(text)} 个字符，仅引用选段' if values else '', values, self.dispatch, maximum=2)

    def attach_text(self, view, role=None):
        view.setContextMenuPolicy(Qt.CustomContextMenu)
        def build(point):
            menu = view.createStandardContextMenu(point)
            selected = view.textCursor().selectedText().replace('\u2029', '\n')
            cid = self.document_chapter() if view is self.w.editor else self.w.workflow_panel.selected_chapter() if view in self.w.workflow_panel.chapter_views.values() else None
            if role:
                row = next((r for r in self.ranges.get(role, []) if r['start'] <= view.textCursor().selectionStart() < r['end']), None)
                cid = row.get('chapter_id') if row else None
            values = self.quote_actions(selected, cid)
            if role and not selected:
                values = self.message_actions(role, view.cursorForPosition(point).position())
            if view is self.w.workflow_panel.outline_view and not selected:
                values = self.w.workflow_panel.outline_object_actions(self.w.workflow_panel.version_list.currentItem())
            if values:
                menu.addSeparator()
                populate(menu, values, self.dispatch)
            return menu
        def requested(point):
            menu = build(point)
            menu.exec(view.viewport().mapToGlobal(point))
            menu.deleteLater()
        view.standard_object_menu = build
        view.customContextMenuRequested.connect(requested)

    def bind_history(self, role, history, state):
        ranges, start = [], 0
        for item in history:
            rendered = transcript_text(item, role, state).replace('\r\n', '\n').replace('\r', '\n')
            assistant = item.get('assistant', '').replace('\r\n', '\n').replace('\r', '\n')
            boundary = len(rendered) - len(assistant) - 3
            cid = item.get('chapter_id')
            request = next((a for a in state.get('author_instructions', []) if a['id'] == item.get('author_instruction_id')), None)
            cid = request.get('chapter_id') if request else cid
            for begin, end in [(0, max(0, boundary - 2)), (max(0, boundary), len(rendered))]:
                text = rendered[begin:end]
                if text.strip():
                    ranges.append({'start':start + len(rendered[:begin].encode('utf-16-le'))//2,
                        'end':start + len(rendered[:end].encode('utf-16-le'))//2, 'text':text, 'chapter_id':cid})
            start += len((rendered + '\n\n').encode('utf-16-le'))//2
        self.ranges[role] = ranges

    def message_actions(self, role, position):
        row = next((r for r in self.ranges.get(role, []) if r['start'] <= position < r['end']), None)
        if not row or not self.core():
            return []
        data = self.data(text=row['text'], **({'chapter_id':row['chapter_id']} if row.get('chapter_id') else {}))
        values = [entry('复制这条消息', 'copy', {'text':row['text']}), entry('引用到主笔输入框', 'object_quote_writer', data),
                  entry('引用到责编输入框', 'object_quote_editor', data), entry('据此起草修改要求', 'object_quote_rewrite', data)]
        if row.get('chapter_id'):
            values.append(entry('查看关联章节', 'object_chapter_read', self.data(chapter_id=row['chapter_id'])))
        return values

    def refresh_message(self, role):
        view = self.w.chat_outputs[role]
        values = self.message_actions(role, view.textCursor().position())
        self.message_bar.bind('消息的可用操作' if values else '点击一条消息，或选中文字后右键引用；发送前可以编辑要求', values, self.dispatch, maximum=2)
