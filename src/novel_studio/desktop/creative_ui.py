"""Chinese author settings in the existing Qt workbench, no model requests."""
import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSpinBox, QSplitter, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from ..core import CoreError, SafeTree, admin
from ..creative import TEMPLATES, WRITER_FIELDS, template, validate_profile
from ..aesthetics import FIELDS as EDITOR_FIELDS, TEMPLATES as EDITOR_TEMPLATES, OPTIONS as EDITOR_OPTIONS, template as editor_template
from ..lengths import DEFAULT_GOAL, KINDS
from ..text_metrics import METRICS
from .dialogs import InformationDialog


class GuardedPanel(QWidget):
    def guarded(self, operation):
        try:
            return operation()
        except (CoreError, ValueError, OSError, KeyError):
            import sys
            error = sys.exc_info()[1]
            message = str(error) if isinstance(error, CoreError) else "操作未完成。文件可能已变化、格式不正确，或不能安全访问；没有覆盖原文件。"
            QMessageBox.warning(self, "操作未完成", message)
            return None


class StylePanel(GuardedPanel):
    def __init__(self, core, document_id=None, parent=None, role="writer"):
        super().__init__(parent)
        self.core, self.service, self.document_id = core, core.creative, document_id
        self.role = role
        self.type_name = "主笔风格" if role == "writer" else "责编审美"
        self.kind = "writer_style" if role == "writer" else "editor_aesthetic"
        self.specs = WRITER_FIELDS if role == "writer" else EDITOR_FIELDS
        self.make_template = template if role == "writer" else editor_template
        self.loaded, self.baseline = None, None
        self.origin = None
        self.loading = False
        layout = QVBoxLayout(self)
        note = QLabel("保存档案会增加版本；应用到作品会固定选定版本。以后编辑档案不会悄悄改变已绑定版本。\n" +
            ("优先级：本轮作者指令 → 章节临时覆盖 → 主笔档案 → 项目默认。风格不能覆盖安全边界和已批准事实。" if role == "writer" else "审美与主笔风格独立。权重 0–10 只是审阅重点，不是文学评分；A 事实、B 质量判断与 C 偏好分开，C 不可标必改。"))
        note.setWordWrap(True)
        layout.addWidget(note)
        split = QSplitter()
        left = QWidget()
        tools = QVBoxLayout(left)
        self.templates = QComboBox()
        self.templates.addItems(TEMPLATES if role == "writer" else EDITOR_TEMPLATES)
        tools.addWidget(self.templates)
        self.new_button = QPushButton("从模板新建")
        self.new_button.clicked.connect(self.new_profile)
        tools.addWidget(self.new_button)
        self.include_archived = QCheckBox("显示已归档档案（可复制恢复）")
        self.include_archived.toggled.connect(self.refresh_profiles)
        tools.addWidget(self.include_archived)
        self.profiles = QListWidget()
        self.profiles.currentItemChanged.connect(self.select_profile)
        tools.addWidget(self.profiles, 1)
        grid = QGridLayout()
        actions = [("复制为新档案", self.copy_profile), ("归档／删除", self.archive_profile),
                   ("导入档案", self.import_profile), ("导出档案", self.export_profile)]
        for i, (text, handler) in enumerate(actions):
            item = QPushButton(text)
            item.clicked.connect(handler)
            grid.addWidget(item, i // 2, i % 2)
        tools.addLayout(grid)
        self.versions = QComboBox()
        tools.addWidget(self.versions)
        history = QPushButton("预览／恢复此历史版本")
        history.clicked.connect(self.history)
        tools.addWidget(history)
        split.addWidget(left)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        self.title = QLineEdit()
        self.title.setMaxLength(60)
        form.addRow("档案名称", self.title)
        self.version_label = QLabel("未保存的新档案")
        form.addRow("当前版本", self.version_label)
        self.fields, self.weights, self.options = {}, {}, {}
        for key, label, help_text, default in self.specs:
            entry = QLineEdit()
            entry.setMaxLength(800)
            entry.setToolTip(help_text)
            caption = QLabel(label)
            caption.setToolTip(help_text)
            if role == "editor":
                cell = QWidget()
                row = QHBoxLayout(cell)
                row.setContentsMargins(0, 0, 0, 0)
                weight = QSpinBox()
                weight.setRange(0, 10)
                weight.setPrefix("重点 ")
                weight.setToolTip("审阅重点，0 到 10；不会变成自动文学总分。必查事实不因权重低而忽略。")
                self.weights[key] = weight
                row.addWidget(entry, 1)
                row.addWidget(weight)
                form.addRow(caption, cell)
            else:
                form.addRow(caption, entry)
            self.fields[key] = entry
        if role == "editor":
            captions = {"strictness": "审稿严格程度", "tolerance": "容忍度", "intrusiveness": "建议侵入程度", "required_checks": "必须检查项", "tolerated": "可容忍问题", "protected": "禁止擅自修改的内容"}
            for key in EDITOR_OPTIONS:
                if key == "strictness":
                    item = QComboBox()
                    item.addItems(["严格", "适中", "宽松"])
                else:
                    item = QPlainTextEdit()
                    item.setMaximumHeight(85)
                self.options[key] = item
                form.addRow(captions[key], item)
        self.rules = QPlainTextEdit()
        self.rules.setPlaceholderText("可用自然语言补充本书约定。不要填写 API Key；这里的文字不能授予工具、文件或账号权限。")
        self.rules.setMinimumHeight(135)
        form.addRow("补充自然语言规则", self.rules)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_widget)
        split.addWidget(scroll)
        split.setStretchFactor(1, 3)
        layout.addWidget(split, 1)
        buttons = QHBoxLayout()
        self.diff_button = QPushButton("比较未保存差异")
        self.diff_button.clicked.connect(self.preview_changes)
        self.save_button = QPushButton("保存档案新版本")
        self.save_button.clicked.connect(self.save_profile)
        self.bind_button = QPushButton("应用此版本到本作品")
        self.bind_button.clicked.connect(self.bind_profile)
        for item in (self.diff_button, self.save_button, self.bind_button):
            buttons.addWidget(item)
        layout.addLayout(buttons)
        chapter = QGridLayout()
        self.override_button = QPushButton("将当前表单用于这份草稿的临时覆盖")
        self.override_button.setEnabled(document_id is not None)
        self.override_button.clicked.connect(self.override_chapter)
        self.clear_override_button = QPushButton("清除当前草稿覆盖")
        self.clear_override_button.setEnabled(document_id is not None)
        self.clear_override_button.clicked.connect(self.clear_override)
        unbind = QPushButton("取消作品档案绑定")
        unbind.clicked.connect(lambda: self.guarded(lambda: self.service.unbind(self.role)))
        chapter.addWidget(self.override_button, 0, 0, 1, 2)
        chapter.addWidget(self.clear_override_button, 1, 0)
        chapter.addWidget(unbind, 1, 1)
        layout.addLayout(chapter)
        if role == "editor":
            self.override_button.hide()
            self.clear_override_button.hide()
        self.status = QLabel("本机配置 · 不发送网络请求 · 不改写正文或正式作品")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.populate(self.make_template("细腻现实主义" if role == "writer" else "文学性优先"))
        self.refresh_profiles()
        binding = self.service.state()["bindings"].get(self.role)
        if binding:
            row = self.service.profile(binding["profile_id"], binding["version"])
            self.populate(row["value"], row)
            self.refresh_profiles(selected=binding["profile_id"])

    def value(self):
        value = {"format": "novel-studio-style-profile-v1" if self.role == "writer" else "novel-studio-aesthetic-profile-v1", "kind": self.kind, "title": self.title.text(),
                "fields": {key: field.text() for key, field in self.fields.items()}, "rules": self.rules.toPlainText()}
        if self.role == "editor":
            value.update(weights={key: spin.value() for key, spin in self.weights.items()}, options={key: item.currentText() if key == "strictness" else item.toPlainText() for key, item in self.options.items()})
        elif self.origin:
            value["origin"] = self.origin
        return value

    def dirty(self):
        return self.baseline is not None and self.value() != self.baseline

    def may_leave(self):
        return not self.dirty() or QMessageBox.question(self, "档案还有未保存修改", "离开将放弃表单内未保存的档案修改。是否继续？作品正文不受影响。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def populate(self, value, loaded=None):
        self.loading = True
        self.origin = value.get("origin")
        self.title.setText(value["title"])
        for key, field in self.fields.items():
            field.setText(value["fields"][key])
            field.setCursorPosition(0)
        if self.role == "editor":
            for key, spin in self.weights.items():
                spin.setValue(value["weights"][key])
            for key, item in self.options.items():
                item.setCurrentText(value["options"][key]) if key == "strictness" else item.setPlainText(value["options"][key])
        self.rules.setPlainText(value["rules"])
        self.loaded, self.baseline = loaded, value
        self.version_label.setText("v" + str(loaded["version"]) + " · 保留原始历史" if loaded else "未保存的新档案")
        self.versions.clear()
        if loaded:
            latest = self.service.profile(loaded["profile_id"])
            for version in range(latest["version"], 0, -1):
                self.versions.addItem("历史 v" + str(version), version)
        self.loading = False

    def refresh_profiles(self, *unused, selected=None):
        self.profiles.blockSignals(True)
        self.profiles.clear()
        binding = self.service.state()["bindings"].get(self.role)
        for row in self.service.profiles(self.include_archived.isChecked()):
            if row["kind"] != self.kind:
                continue
            title = row["title"] + " · v" + str(row["version"])
            if row.get("archived"):
                title += " · 已归档"
            if binding and binding["profile_id"] == row["profile_id"]:
                title += " · 本作品绑定 v" + str(binding["version"])
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, row["profile_id"])
            self.profiles.addItem(item)
            if selected == row["profile_id"]:
                self.profiles.setCurrentItem(item)
        self.profiles.blockSignals(False)

    def select_profile(self, current, previous):
        if self.loading or current is None:
            return
        if not self.may_leave():
            self.profiles.blockSignals(True)
            self.profiles.setCurrentItem(previous)
            self.profiles.blockSignals(False)
            return
        def operation():
            row = self.service.profile(current.data(Qt.UserRole))
            self.populate(row["value"], row)
        self.guarded(operation)

    def new_profile(self):
        if self.may_leave():
            self.populate(self.make_template(self.templates.currentText()))
            self.profiles.clearSelection()

    def copy_profile(self):
        value = self.value()
        value["title"] = value["title"][:50] + " · 副本"
        self.populate(value)

    def save_profile(self):
        def operation():
            row = self.service.save(self.value(), self.loaded["profile_id"] if self.loaded else None, self.loaded["revision"] if self.loaded else None)
            saved = self.service.profile(row["profile_id"], row["version"])
            self.populate(saved["value"], saved)
            self.refresh_profiles(selected=row["profile_id"])
            self.status.setText("档案已保存为 v" + str(row["version"]) + "；原绑定不自动变化，如需使用请点击应用。")
        self.guarded(operation)

    def bind_profile(self):
        if self.loaded is None or self.dirty():
            QMessageBox.information(self, "先保存档案", "请先保存风格修改，再应用明确的档案版本。")
            return
        def operation():
            self.service.bind(self.loaded["profile_id"], self.loaded["version"])
            self.refresh_profiles(selected=self.loaded["profile_id"])
            self.status.setText("本作品已绑定此版本。" + ("主笔" if self.role == "writer" else "责编") + "下次发送时生效，正文与正式作品未改动。")
        self.guarded(operation)

    def archive_profile(self):
        if self.loaded is None:
            return
        if QMessageBox.question(self, "归档／删除风格", "将从常用列表移除此档案，并取消本作品对它的绑定；全部版本保留，可显示归档档案后复制恢复。是否继续？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            def operation():
                self.service.archive(self.loaded["profile_id"])
                self.populate(self.make_template("自定义风格" if self.role == "writer" else "自定义审美"))
                self.refresh_profiles()
            self.guarded(operation)

    def import_profile(self):
        if not self.may_leave():
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择" + self.type_name + "档案（先预览，不自动应用）", "", "Novel Studio 档案 (*.json)")
        if not path:
            return
        def operation():
            source = Path(path)
            with SafeTree(source.parent) as tree:
                payload = tree.read(source.name)
            if len(payload) > 100000:
                raise CoreError("PROFILE_INVALID", "此风格文件过大，未导入。")
            value = validate_profile(json.loads(payload))
            if value["kind"] != self.kind:
                raise CoreError("PROFILE_INVALID", "主笔风格与责编审美是不同档案，请在对应角色的设置中导入。")
            self.populate(value)
            self.status.setText("导入内容已显示在表单中；尚未保存或应用。请查看后决定。")
        self.guarded(operation)

    def export_profile(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出当前档案（已有文件不会覆盖）", self.type_name + ".json", "Novel Studio 档案 (*.json)")
        if not path:
            return
        def operation():
            target = Path(path)
            value = self.service.check_profile(self.value())
            with SafeTree(target.parent) as tree:
                tree.create(target.name, admin.encode(value))
            self.status.setText("已导出档案字段和来源编号，不附带参考原件、项目文件或密钥。已有文件未被覆盖。")
        self.guarded(operation)

    def preview_changes(self):
        InformationDialog("风格差异 · 不改动正文", self.service.diff(self.baseline or {}, self.value()) or "没有未保存变化。", self).exec()

    def history(self):
        if not self.loaded or not self.may_leave():
            return
        def operation():
            row = self.service.profile(self.loaded["profile_id"], self.versions.currentData())
            latest = self.service.profile(self.loaded["profile_id"])
            InformationDialog("历史风格与最新版本差异", self.service.diff(latest["value"], row["value"]) or "与最新版本相同。", self).exec()
            self.populate(row["value"], latest)
            self.baseline = latest["value"]  # Historical preview must be saved before applying.
            self.status.setText("已载入历史 v" + str(row["version"]) + "；点击保存将追加一个新版本，不覆盖历史。")
        self.guarded(operation)

    def override_chapter(self):
        if not self.document_id:
            return
        if QMessageBox.question(self, "当前草稿的临时风格", "将表单中的全部风格维度和补充规则用于这份草稿，优先于全书档案。仅保存配置，不修改正文；不会自动传播到其他草稿。是否继续？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.guarded(lambda: self.service.override(self.document_id, self.value()["fields"], self.value()["rules"]))

    def clear_override(self):
        if self.document_id:
            self.guarded(lambda: self.service.clear_override(self.document_id))
            self.status.setText("已清除当前草稿的临时覆盖，下次创作使用作品档案及本轮指令。")


class LengthPanel(GuardedPanel):
    def __init__(self, core, document_id=None, parent=None):
        super().__init__(parent)
        self.core, self.service, self.document_id = core, core.lengths, document_id
        self.loading = False
        self.baseline_goal = None
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        note = QLabel("全书/分卷按每章选定的一个正文文件累计，不把多份修订稿相加；章节反映所选文件当前版本。\n场景采用明确行号，源版本变化后须重新选择范围。没有关联正文的条目会明确提示，不假装统计完整。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.metric_selector = QComboBox()
        for key, description in METRICS.items():
            self.metric_selector.addItem(description.split("：", 1)[0], key)
            self.metric_selector.setItemData(self.metric_selector.count() - 1, description, Qt.ToolTipRole)
        layout.addWidget(self.metric_selector)
        self.tree = QTreeWidget()
        self.tree.setMinimumHeight(180)
        self.tree.setHeaderLabels(["作品层级", "已选正文计数", "目标", "范围状态"])
        self.tree.currentItemChanged.connect(self.selected)
        layout.addWidget(self.tree, 1)
        additions = QHBoxLayout()
        for kind in ("volume", "chapter", "scene"):
            item = QPushButton("新增" + KINDS[kind])
            item.clicked.connect(lambda checked=False, value=kind: self.add_node(value))
            additions.addWidget(item)
        link = QPushButton("选择该章节／场景的正文")
        link.clicked.connect(self.link_source)
        additions.addWidget(link)
        layout.addLayout(additions)
        form = QFormLayout()
        self.minimum, self.target, self.maximum = (QSpinBox() for _ in range(3))
        for label, widget in (("建议下限", self.minimum), ("建议字数", self.target), ("建议上限", self.maximum)):
            widget.setRange(0, 20000000)
            widget.setSpecialValueText("未设置")
            form.addRow(label, widget)
        self.deviation = QDoubleSpinBox()
        self.deviation.setRange(0, 100)
        self.deviation.setSuffix(" %")
        form.addRow("未显式设上下限时的允许偏差", self.deviation)
        self.strict = QCheckBox("突出篇幅建议偏差（不阻断保存、审稿或继续创作）")
        self.inherit = QCheckBox("继承上级同一数值，不自动按章节均分")
        form.addRow(self.strict)
        form.addRow(self.inherit)
        layout.addLayout(form)
        actions = QHBoxLayout()
        save = QPushButton("保存目标与统计方式")
        save.clicked.connect(self.save_goal)
        active = QPushButton("将此条目设为当前创作目标")
        active.clicked.connect(self.activate)
        refresh = QPushButton("重新读取正文计数")
        refresh.clicked.connect(lambda: self.guarded(self.refresh_tree) if self.may_leave() else None)
        for item in (save, active, refresh):
            actions.addWidget(item)
        layout.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        layout.addWidget(self.progress)
        self.status = QLabel("选择条目设置目标。所有层级共用同一种本地统计方式。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh_tree()

    def node_id(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item else "book"

    def refresh_tree(self, select=None):
        selected = select or self.node_id()
        self.plan = self.service.plan()
        self.counts = self.service.counts()
        self.metric_selector.setCurrentIndex(max(0, self.metric_selector.findData(self.plan["metric"])))
        self.tree.blockSignals(True)
        self.tree.clear()
        items = {}
        ordered = sorted(self.plan["nodes"].items(), key=lambda pair: pair[1].get("order", 0))
        for key, node in ordered:
            value = self.counts["nodes"][key]
            title = KINDS[node["kind"]] + " · " + node["title"] + (" · 当前" if key == self.plan["active"] else "")
            status = {"UNSET": "未设置", "UNDER": "不足", "OVER": "超过", "WITHIN": "范围内"}[value["status"]]
            if value["missing_sources"]:
                status += " · " + str(len(value["missing_sources"])) + " 项正文未关联／范围失效"
            item = QTreeWidgetItem([title, str(value["count"]), str(value["target"]) if value["target"] is not None else "未设置", status])
            item.setData(0, Qt.UserRole, key)
            items[key] = item
        for key, node in ordered:
            if node["parent"] is None:
                self.tree.addTopLevelItem(items[key])
            else:
                items[node["parent"]].addChild(items[key])
            items[key].setExpanded(True)
        self.tree.expandAll()
        self.tree.resizeColumnToContents(0)
        self.tree.setCurrentItem(items.get(selected, items["book"]))
        self.tree.blockSignals(False)
        self.selected()

    def goal_value(self):
        return {"minimum": self.minimum.value() or None, "target": self.target.value() or None, "maximum": self.maximum.value() or None,
                "deviation_percent": self.deviation.value(), "strict": self.strict.isChecked(), "inherit": self.inherit.isChecked()}

    def dirty(self):
        return self.baseline_goal is not None and (self.goal_value() != self.baseline_goal or self.metric_selector.currentData() != self.plan["metric"])

    def may_leave(self):
        return not self.dirty() or QMessageBox.question(self, "目标还有未保存修改", "离开将放弃表单中尚未保存的字数目标或统计方式。是否继续？正文不受影响。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def selected(self, current=None, previous=None):
        if current is not None and previous is not None and not self.may_leave():
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(previous)
            self.tree.blockSignals(False)
            return
        key = self.node_id()
        goal = self.plan["nodes"][key]["goal"]
        self.metric_selector.setCurrentIndex(max(0, self.metric_selector.findData(self.plan["metric"])))
        self.minimum.setValue(goal["minimum"] or 0)
        self.target.setValue(goal["target"] or 0)
        self.maximum.setValue(goal["maximum"] or 0)
        self.deviation.setValue(goal["deviation_percent"])
        self.strict.setChecked(goal["strict"])
        self.inherit.setChecked(goal["inherit"])
        self.inherit.setEnabled(key != "book")
        self.baseline_goal = self.goal_value()
        value = self.counts["nodes"][key]
        progress = value["progress"]
        self.progress.setValue(min(1000, int(progress * 1000)) if progress is not None else 0)
        self.progress.setFormat(f"{progress:.1%}" if progress is not None else "尚未设置目标")
        self.status.setText("当前条目：" + self.plan["nodes"][key]["title"] + "；剩余：" + str(value["remaining"] if value["remaining"] is not None else "未设置") + "。" +
            ("存在未关联或失效的正文，累计仅反映已选有效版本。" if value["missing_sources"] else "已关联正文按统一口径统计。") + "\n" + self.counts["description"])

    def save_goal(self):
        key = self.node_id()
        goal = self.goal_value()
        def operation():
            self.service.goal(key, goal)
            self.service.set_metric(self.metric_selector.currentData())
            self.refresh_tree(key)
        self.guarded(operation)

    def activate(self):
        if not self.may_leave():
            return
        key = self.node_id()
        self.guarded(lambda: self.service.activate(key))
        self.refresh_tree(key)

    def add_node(self, kind):
        if not self.may_leave():
            return
        parent = "book" if kind == "volume" else self.node_id()
        title, accepted = QInputDialog.getText(self, "新增" + KINDS[kind], "名称（不创建、重命名或修改正文文件）：")
        if accepted and title:
            def operation():
                key = self.service.add(kind, title, parent)
                self.refresh_tree(key)
            self.guarded(operation)

    def link_source(self):
        if not self.may_leave():
            return
        key = self.node_id()
        node = self.plan["nodes"][key]
        if node["kind"] not in {"chapter", "scene"}:
            QMessageBox.information(self, "选择章节或场景", "请先在上方树中选择一个章节或场景。")
            return
        rows = [r for r in self.core.documents() if r["area"] in {"drafts", "canonical"}]
        labels = [str(i + 1) + ". " + r["display_title"] + (" · 草稿" if r["area"] == "drafts" else " · 正式作品") for i, r in enumerate(rows)]
        if not rows:
            QMessageBox.information(self, "还没有正文", "请先创建草稿，再关联目标；无需提前写入正式作品。")
            return
        selected, accepted = QInputDialog.getItem(self, "选择唯一正文版本", "同一章节只累计一个文件；多份修订稿不相加：", labels, 0, False)
        if not accepted:
            return
        row = rows[labels.index(selected)]
        start = end = None
        if node["kind"] == "scene":
            source = self.core.document(row["document_id"])
            count = len(source["text"].splitlines())
            InformationDialog("选择场景行号 · 源稿只读", "\n".join(str(i + 1) + "  " + line for i, line in enumerate(source["text"].splitlines())), self).exec()
            start, accepted = QInputDialog.getInt(self, "场景开始行", "从哪一行开始（包含此行）：", 1, 1, max(1, count))
            if not accepted:
                return
            end, accepted = QInputDialog.getInt(self, "场景结束行", "在哪一行结束（包含此行）：", start, start, max(start, count))
            if not accepted:
                return
        def operation():
            self.service.link(key, row["document_id"], start, end)
            self.refresh_tree(key)
        self.guarded(operation)


class WriterSettingsDialog(QDialog):
    def __init__(self, core, document_id=None, parent=None, tab=0):
        super().__init__(parent)
        self.setWindowTitle("主笔设置 · 风格档案与字数管理")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1150, screen.width() - 32), min(830, screen.height() - 40))
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.styles = StylePanel(core, document_id)
        self.lengths = LengthPanel(core, document_id)
        self.tabs.addTab(self.styles, "风格管理")
        self.tabs.addTab(self.lengths, "字数管理")
        self.tabs.setCurrentIndex(tab)
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox()
        buttons.addButton("关闭", QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def reject(self):
        if self.styles.may_leave() and self.lengths.may_leave():
            super().reject()


class EditorSettingsDialog(QDialog):
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.setWindowTitle("责编设置 · 独立审美与证据式审稿")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1150, screen.width() - 32), min(830, screen.height() - 40))
        layout = QVBoxLayout(self)
        self.aesthetics = StylePanel(core, parent=self, role="editor")
        layout.addWidget(self.aesthetics)
        buttons = QDialogButtonBox()
        buttons.addButton("关闭", QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def reject(self):
        if self.aesthetics.may_leave():
            super().reject()
