"""Book/volume/chapter/version check states are independent of row focus."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QAbstractItemView


class VersionCell:
    def __init__(self, node):
        self.node = node

    def data(self, role):
        return self.node.data(0, role)

    def text(self):
        return self.node.text(1)

    def checkState(self):
        return self.node.checkState(0)

    def setCheckState(self, state):
        self.node.setCheckState(0, state)


class ExportSelection(QTreeWidget):
    choices_changed = Signal()

    def __init__(self):
        super().__init__()
        self.leaves = []
        self.groups = []
        self.setColumnCount(4)
        self.setHeaderLabels(['导出', '作品 / 卷章 / 版本', '版本状态', '实际字数'])
        self.setTreePosition(1)
        self.setColumnWidth(0, 48)
        self.setColumnWidth(1, 300)
        self.setColumnWidth(2, 150)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.itemChanged.connect(self.changed)
        self.setAccessibleName('勾选导出范围；点击名称仅预览')

    def count(self):
        return len(self.leaves)

    def item(self, index):
        return VersionCell(self.leaves[index])

    def currentRow(self):
        current = self.currentItem()
        return self.leaves.index(current) if current in self.leaves else -1

    def setCurrentRow(self, index):
        if 0 <= index < len(self.leaves):
            self.setCurrentItem(self.leaves[index])

    def set_rows(self, title, rows, checked):
        self.blockSignals(True)
        self.clear()
        self.leaves, self.groups = [], []
        root = self.group(title)
        self.addTopLevelItem(root)
        volumes, chapters = {}, {}
        for row in rows:
            volume = row.get('volume', '未分卷')
            if volume not in volumes:
                volumes[volume] = self.group(volume)
                root.addChild(volumes[volume])
            cid = row.get('chapter_id') or row.get('target', row['relative'])
            key = (volume, cid)
            if key not in chapters:
                chapters[key] = self.group(row['title'])
                volumes[volume].addChild(chapters[key])
            node = QTreeWidgetItem(['', row['title'], row['version'], str(row['word_count'])])
            node.setData(0, Qt.UserRole, row)
            node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            node.setCheckState(0, Qt.Checked if row['source_id'] in checked else Qt.Unchecked)
            node.setToolTip(1, row['title'] + '\n' + row['version'] + '\n点击名称只预览；勾选框决定导出范围。')
            chapters[key].addChild(node)
            self.leaves.append(node)
        self.sync_groups()
        self.expandAll()
        self.blockSignals(False)

    def group(self, title):
        node = QTreeWidgetItem(['', title])
        node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        node.setCheckState(0, Qt.Unchecked)
        self.groups.append(node)
        return node

    def descendants(self, node):
        if node in self.leaves:
            return [node]
        return [leaf for i in range(node.childCount()) for leaf in self.descendants(node.child(i))]

    def sync_groups(self):
        for node in reversed(self.groups):
            leaves = self.descendants(node)
            count = sum(leaf.checkState(0) == Qt.Checked for leaf in leaves)
            node.setCheckState(0, Qt.Unchecked if not count else Qt.Checked if count == len(leaves) else Qt.PartiallyChecked)
            node.setText(3, str(sum(leaf.data(0, Qt.UserRole)['word_count'] for leaf in leaves)))

    def changed(self, node, column):
        if column != 0:
            return
        self.blockSignals(True)
        if node not in self.leaves:
            state = Qt.Checked if node.checkState(0) != Qt.Unchecked else Qt.Unchecked
            for leaf in self.descendants(node):
                leaf.setCheckState(0, state)
        self.sync_groups()
        self.blockSignals(False)
        self.choices_changed.emit()

    def choose(self, state):
        self.blockSignals(True)
        for node in self.leaves:
            checked = node.checkState(0) == Qt.Checked
            node.setCheckState(0, Qt.Checked if (not checked if state == 'invert' else state) else Qt.Unchecked)
        self.sync_groups()
        self.blockSignals(False)
        self.choices_changed.emit()

    def checked_rows(self):
        return [node.data(0, Qt.UserRole) for node in self.leaves if node.checkState(0) == Qt.Checked]
