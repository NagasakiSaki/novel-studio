"""One action description feeds visible controls and Qt context menus."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QWidget


def entry(text, key, data=None, group=''):
    return {'text':text, 'key':key, 'data':dict(data or {}), 'group':group}


def populate(menu, entries, dispatch):
    groups = {}
    for value in entries:
        target = menu
        if value.get('group'):
            target = groups.setdefault(value['group'], None)
            if target is None:
                target = menu.addMenu(value['group'])
                groups[value['group']] = target
        action = target.addAction(value['text'])
        action.setData(value['key'])
        # A menu belongs to the object hit when it opened, never a later selection.
        key, data = value['key'], dict(value.get('data', {}))
        action.triggered.connect(lambda checked=False, k=key, d=data: dispatch(k, dict(d)))
    return menu


def attach_items(view, provider, dispatch, *, column=0, select=None):
    """Hit-test the event position; blank space deliberately has no object menu."""
    view.setContextMenuPolicy(Qt.CustomContextMenu)
    def build(item):
        if item is None:
            return None
        values = provider(item)
        if not values:
            return None
        if select:
            select(item)
        elif hasattr(view, 'setCurrentItem'):
            view.setCurrentItem(item)
        return populate(QMenu(view), values, dispatch)
    def requested(point):
        menu = build(view.itemAt(point))
        if menu:
            menu.exec(view.viewport().mapToGlobal(point))
            menu.deleteLater()
    view.object_menu = build
    view.customContextMenuRequested.connect(requested)
    keyboard = QAction('所选对象的操作', view)
    keyboard.setShortcut(QKeySequence('Shift+F10'))
    keyboard.setShortcutContext(Qt.WidgetWithChildrenShortcut)
    def keyboard_menu():
        item = view.currentItem()
        menu = build(item)
        if menu:
            menu.exec(view.viewport().mapToGlobal(view.visualItemRect(item).center()))
            menu.deleteLater()
    keyboard.triggered.connect(keyboard_menu)
    view.addAction(keyboard)
    view.object_menu_shortcut = keyboard


class SelectionActions(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Selection feedback must not move the object between right press and
        # the native context-menu release event.
        self.setMinimumHeight(40)
        self.body = QHBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel('选择对象后显示可用操作')
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        self.body.addWidget(self.label, 1)
        self.buttons = []

    def bind(self, title, entries, dispatch, maximum=3):
        for button in self.buttons:
            self.body.removeWidget(button)
            button.deleteLater()
        self.buttons = []
        self.label.setText(title)
        for value in entries[:maximum]:
            button = QPushButton(value['text'])
            key, data = value['key'], dict(value.get('data', {}))
            button.clicked.connect(lambda checked=False, k=key, d=data:dispatch(k, dict(d)))
            self.body.addWidget(button)
            self.buttons.append(button)
        if len(entries) > maximum:
            button = QPushButton('更多操作')
            button.setMenu(populate(QMenu(button), entries[maximum:], dispatch))
            self.body.addWidget(button)
            self.buttons.append(button)
