"""Native book/volume/chapter tree with the existing selection contract."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
from ..document_titles import numbered_title


class TreeCell:
    def __init__(self, node, column):
        self.node, self.column = node, column

    def data(self, role):
        return self.node.data(self.column, role)

    def text(self):
        return self.node.text(self.column)

    def checkState(self):
        return self.node.checkState(self.column)

    def setCheckState(self, value):
        self.node.setCheckState(self.column, value)


class ChapterTree(QTreeWidget):
    def __init__(self):
        super().__init__()
        self.leaves = []
        self.groups = []
        self.setColumnCount(6)
        self.setTreePosition(3)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(False)
        self.setIndentation(15)

    def rowCount(self):
        return len(self.leaves)

    def currentRow(self):
        current = self.currentItem()
        return self.leaves.index(current) if current in self.leaves else -1

    def selectRow(self, row):
        self.setCurrentItem(self.leaves[row] if 0 <= row < len(self.leaves) else None)

    def item(self, row, column):
        return TreeCell(self.leaves[row], column) if 0 <= row < len(self.leaves) else None

    def setRowHidden(self, row, hidden):
        self.leaves[row].setHidden(hidden)

    def isRowHidden(self, row):
        return not 0 <= row < len(self.leaves) or self.leaves[row].isHidden()

    def setRowCount(self, count):
        # Used only for clearing by legacy view callers.
        if count == 0:
            self.leaves = []
            self.groups = []
            self.clear()

    def populate(self, title, rows, statuses, checked, unplaced=()):
        expanded = {g.data(3, Qt.UserRole) for g in self.groups if g.isExpanded()}
        first = not self.groups
        self.setRowCount(0)
        book = QTreeWidgetItem(["", "", "", title])
        book.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        book.setData(3, Qt.UserRole, 'book')
        self.addTopLevelItem(book)
        book.setExpanded(True)
        volumes = {}
        for row in rows:
            directory = row.get("directory", {})
            volume, number = directory.get("volume", row["volume"]), directory.get("position", row["number"])
            if volume not in volumes:
                group = QTreeWidgetItem(["", "", "", numbered_title(volume, directory.get('volume_title', row['volume_title']) or '卷名待定', '卷')])
                group.setData(3, Qt.UserRole, volume)
                group.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                book.addChild(group)
                self.groups.append(group)
                volumes[volume] = group
                group.setExpanded(first or volume in expanded)
            count = (row.get("local_length") or {}).get("count")
            text = numbered_title(number, row['title'] or '标题待生成') + f"\n{statuses.get(row['status'], '待检查')} · {str(count) + ' 字' if count is not None else '尚未统计'}"
            node = QTreeWidgetItem(["", str(row["volume"]), str(row["number"]), text, statuses.get(row["status"], "待检查"), str(count or 0)])
            node.setData(0, Qt.UserRole, row["chapter_id"])
            node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            node.setCheckState(0, Qt.Checked if row["chapter_id"] in checked else Qt.Unchecked)
            node.setToolTip(3, f"创作身份：第{row['volume']}卷第{row['number']}章。目录调整不改变原始编号、正式文件或历史。")
            volumes[volume].addChild(node)
            self.leaves.append(node)
        if unplaced:
            group = QTreeWidgetItem(["", "", "", f"待整理章节（{len(unplaced)}）"])
            group.setFlags(Qt.ItemIsEnabled)
            book.addChild(group)
            for row in unplaced:
                node = QTreeWidgetItem(["", "", "", row["display_title"] + "\n尚未关联目录，原文件保留"])
                node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                node.setData(3, Qt.UserRole, {'document_id':row['document_id']})
                group.addChild(node)

    def update_groups(self):
        for group in self.groups:
            group.setHidden(all(group.child(i).isHidden() for i in range(group.childCount())))
