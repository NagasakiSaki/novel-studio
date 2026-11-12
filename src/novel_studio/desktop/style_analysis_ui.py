"""Reuse imported documents and existing style forms; no file re-upload picker."""
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget,
    QVBoxLayout, QWidget)
from ..core import CoreError
from ..style_analysis import StyleAnalysisService, STRENGTHS, OBSERVATION_LABELS, copied_fragments
from ..creative import template
from ..providers import DESTINATIONS
from .creative_ui import StylePanel
from .dialogs import InformationDialog
from .jobs import Job


class StyleAnalysisDialog(QDialog):
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core, self.service = core, StyleAnalysisService(core)
        self.job, self.record = None, None
        self.scene_baseline = ""
        self.displayed_revisions = {}
        self.setWindowTitle("文风分析 · 原资料复用、抽象风格与独立预览")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1200, screen.width() - 32), min(870, screen.height() - 40))
        layout = QVBoxLayout(self)
        note = QLabel("本地分析不联网。可选模型分析与预览会先确认接收方/资料范围及额度。\n只提取抽象风格，不复刻独特句子；不会自动覆盖当前风格、草稿或正式作品。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tabs = QTabWidget()
        source_page = QWidget()
        sources_layout = QVBoxLayout(source_page)
        self.selectors, self.starts, self.ends, self.source_previews = {}, {}, {}, {}
        self.source_tabs = QTabWidget()
        catalog = self.service.catalog()
        for role, title in (("primary", "主参考（必选）"), ("auxiliary", "辅助参考（可选，不能覆盖主风格）")):
            selector = QComboBox()
            selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            selector.setMinimumContentsLength(24)
            selector.addItem("不使用辅助参考" if role == "auxiliary" else "选择已导入资料或现有章节…", None)
            for row in catalog:
                selector.addItem(row["title"] + " · " + row["kind"], row["source_id"])
            form = QFormLayout()
            form.addRow(title, selector)
            row = QHBoxLayout()
            start, end = QSpinBox(), QSpinBox()
            start.setRange(1, 1000000)
            end.setRange(1, 1000000)
            row.addWidget(QLabel("开始行"))
            row.addWidget(start)
            row.addWidget(QLabel("结束行"))
            row.addWidget(end)
            form.addRow("本次分析范围（包含两端）", row)
            sources_layout.addLayout(form)
            preview = QPlainTextEdit()
            preview.setReadOnly(True)
            preview.setPlaceholderText("从现有资料库选择，不需要重新选择原文件。原文只读，行号用于选取本次范围。")
            self.source_tabs.addTab(preview, "主参考原文" if role == "primary" else "辅助参考原文")
            self.selectors[role], self.starts[role], self.ends[role], self.source_previews[role] = selector, start, end, preview
            selector.currentIndexChanged.connect(lambda index, value=role: self.source_changed(value))
        sources_layout.addWidget(self.source_tabs, 1)
        self.local_button = QPushButton("本地量化分析 · 不联网")
        self.model_button = QPushButton("可选：模型解释抽象风格（使用额度）")
        self.local_button.clicked.connect(lambda: self.begin_analysis(False))
        self.model_button.clicked.connect(lambda: self.begin_analysis(True))
        row = QHBoxLayout()
        row.addWidget(self.local_button)
        row.addWidget(self.model_button)
        sources_layout.addLayout(row)
        self.tabs.addTab(source_page, "1 · 选择现有资料与范围")
        report_page = QWidget()
        report_layout = QVBoxLayout(report_page)
        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setPlaceholderText("句段统计和近似指标标为本地实测；词汇、视角和节奏解释标为模型主观判断。")
        report_layout.addWidget(self.report_view, 1)
        self.history_selector = QComboBox()
        history_row = QHBoxLayout()
        history_row.addWidget(self.history_selector, 1)
        load = QPushButton("加载此分析版本")
        load.clicked.connect(self.load_history)
        compare = QPushButton("比较分析版本差异")
        compare.clicked.connect(self.compare_history)
        history_row.addWidget(load)
        history_row.addWidget(compare)
        report_layout.addLayout(history_row)
        self.tabs.addTab(report_page, "2 · 实测与主观分析")
        profile_page = QWidget()
        profile_layout = QVBoxLayout(profile_page)
        strength_row = QHBoxLayout()
        self.strength = QComboBox()
        for key, title in STRENGTHS.items():
            self.strength.addItem(title, key)
        self.strength.setCurrentIndex(1)
        strength_row.addWidget(QLabel("抽象风格采用程度"))
        strength_row.addWidget(self.strength, 1)
        build = QPushButton("生成／更新待编辑档案（不自动应用）")
        build.clicked.connect(self.build_profile)
        strength_row.addWidget(build)
        profile_layout.addLayout(strength_row)
        self.profile_editor = StylePanel(core)
        profile_layout.addWidget(self.profile_editor, 1)
        self.tabs.addTab(profile_page, "3 · 编辑抽象规则与保存档案")
        preview_page = QWidget()
        preview_layout = QVBoxLayout(preview_page)
        self.scene = QPlainTextEdit()
        self.scene.setPlaceholderText("写一个用于比较的自写或合成场景。仅发送这里的场景与当前待编辑风格，不发送原参考全文。")
        self.scene.setMaximumHeight(120)
        preview_layout.addWidget(self.scene)
        self.preview_button = QPushButton("生成当前风格的独立预览（使用额度）")
        self.preview_button.clicked.connect(self.preview)
        preview_layout.addWidget(self.preview_button)
        self.preview_view = QPlainTextEdit()
        self.preview_view.setReadOnly(True)
        self.preview_view.setPlaceholderText("预览留在此窗口，不写入草稿或正式作品。可修改规则后用同一场景再次预览。")
        self.previous_preview = QPlainTextEdit()
        self.previous_preview.setReadOnly(True)
        self.previous_preview.setPlaceholderText("上一次预览，用于比较；不会覆盖正文。")
        previews = QSplitter()
        previews.addWidget(self.previous_preview)
        previews.addWidget(self.preview_view)
        preview_layout.addWidget(previews, 1)
        history_row = QHBoxLayout()
        self.preview_history = QComboBox()
        history_row.addWidget(self.preview_history, 1)
        load_preview = QPushButton("加载历史预览与场景（不改当前规则）")
        load_preview.clicked.connect(self.load_preview)
        history_row.addWidget(load_preview)
        preview_layout.addLayout(history_row)
        compare_rules = QPushButton("比较项目原风格与待编辑规则")
        compare_rules.clicked.connect(self.compare_rules)
        preview_layout.addWidget(compare_rules)
        self.tabs.addTab(preview_page, "4 · 场景预览与比较")
        layout.addWidget(self.tabs, 1)
        self.status = QLabel("尚未分析。默认不联网；分析后需作者编辑、保存和明确应用。")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox()
        buttons.addButton("关闭", QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_history()
        self.refresh_previews()

    def guarded(self, operation):
        try:
            return operation()
        except Exception as error:
            self.error(error)
            return None

    def error(self, error):
        self.status.setText(str(error) if isinstance(error, CoreError) else "操作未完成。资料、模型返回格式或本机访问可能有变化；没有改写原文或自动重试。")

    def source_changed(self, role):
        source_id = self.selectors[role].currentData()
        if source_id is None:
            self.source_previews[role].clear()
            return
        def operation():
            source = self.service.source_detail(source_id)
            self.displayed_revisions[role] = source["source_revision"]
            maximum = max(source["total_lines"], 1)
            self.starts[role].setMaximum(maximum)
            self.ends[role].setMaximum(maximum)
            self.starts[role].setValue(1)
            self.ends[role].setValue(maximum)
            self.source_previews[role].setPlainText("\n".join(str(i + 1) + "  " + line for i, line in enumerate(source["text"].splitlines())))
        self.guarded(operation)

    def selected_sources(self):
        values = []
        for role in ("primary", "auxiliary"):
            source_id = self.selectors[role].currentData()
            if source_id is None:
                if role == "primary":
                    raise CoreError("SOURCE_REQUIRED", "请先选择主参考资料；不需要重新导入原文件。")
                continue
            selected = self.service.select(source_id, self.starts[role].value(), self.ends[role].value())
            if selected["source_revision"] != self.displayed_revisions.get(role):
                raise CoreError("SOURCE_CHANGED", "资料在预览后已经变化，请重新选择并检查范围；未发送模型请求。")
            values.append(selected)
        return values

    def begin_analysis(self, model):
        def operation():
            selected = self.selected_sources()
            if model and not self.confirm_model("文风分析", "只发送本页选择的主／辅参考范围及本地量化统计，不发送其他作品文件。"):
                return
            if not self.profile_editor.may_leave():
                return
            self.background(lambda: self.service.analyze(selected, model=model), self.analysis_ready)
        self.guarded(operation)

    def confirm_model(self, title, scope):
        chosen = self.core.models.selected("writer")
        if not chosen.get("model_id"):
            raise CoreError("MODEL_REQUIRED", "请先在工作台为主笔选择模型；本地量化分析仍可直接使用。")
        destination = DESTINATIONS.get(chosen["provider"], "Antigravity 官方宿主")
        return QMessageBox.question(self, title + " · 确认模型用量", "接收方：" + destination + "\n模型：" + chosen["model_id"] + "\n" + scope + "\n本次启动一次独立操作，可能消耗额度或独立 API 费用；宿主内部执行由官方管理。不沿用创作会话，本程序不自动重试或修改正文。是否继续？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def background(self, operation, ready):
        if self.job:
            return
        self.job = Job(lambda notify: operation(), self)
        self.tabs.setEnabled(False)
        self.job.finished_value.connect(lambda value: self.guarded(lambda: ready(value)))
        self.job.failed.connect(self.error)
        self.job.finished.connect(self.job_finished)
        self.status.setText("处理中，请等待。本操作不会改写源稿。")
        self.job.start()

    def job_finished(self):
        old, self.job = self.job, None
        old.deleteLater()
        self.tabs.setEnabled(True)

    def analysis_ready(self, record):
        self.record = record
        provider = record["model"].get("provider")
        parts = ["分析版本：v" + str(record["version"]), "分析方式：" + ("仅本地统计，未请求模型" if provider == "local" else str(provider) + " · " + str(record["model"].get("actual_model") or record["model"].get("requested_model"))), ""]
        for index, source in enumerate(record["selections"]):
            stats = source["local_features"]
            parts += [("主参考" if index == 0 else "辅助参考") + "：" + source["title"] + "，第 " + str(source["start_line"]) + "–" + str(source["end_line"]) + " 行", "【本地实测及定义】", "有效字符：" + str(stats["count"]["count"]), stats["count"]["description"]]
            for key, label in (("sentences", "按句末标点划分的片段"), ("paragraphs", "按 Markdown 语法识别的段落")):
                data = stats[key]
                parts += [label + "：" + str(data["samples"]) + " 个；平均 " + str(round(data["mean"] or 0, 1)) + " 字；中位数 " + str(data["median"]) + " 字；最短 " + str(data["min"]) + "，最长 " + str(data["max"]), "长度分布：" + " / ".join(k + " 字：" + str(v) for k, v in data["bins"].items()), stats["definitions"][key]]
            parts += ["字形多样性：" + f"{stats['distinct_character_ratio']:.1%}", stats["definitions"]["distinct_character_ratio"], "引号内文字比例（近似）：" + f"{stats['quoted_text_ratio_approx']:.1%}", stats["definitions"]["quoted_text_ratio_approx"], "暂不量化：" + stats["not_measured"], ""]
        for name, part in record["analysis"].items():
            if part:
                parts += ["【主参考的解释】" if name == "primary" else "【辅助参考的解释】", "以下为主观解释；仅本地模式时尚未请求模型，不作这些判断。", *[OBSERVATION_LABELS[key] + "：" + text for key, text in part["observations"].items()], "不确定性：" + part["limitations"], ""]
        self.report_view.setPlainText("\n".join(parts))
        self.tabs.setCurrentIndex(1)
        self.status.setText("分析已保留为新版本。原文、当前风格绑定和正文没有变化；可到第三步生成待编辑档案。")
        self.refresh_history(record["analysis_id"])

    def refresh_history(self, selected=None):
        self.history_selector.clear()
        for row in sorted(self.service.records(), key=lambda r: (r["title"], r["version"]), reverse=True):
            self.history_selector.addItem(row["title"] + " · 分析 v" + str(row["version"]), row["analysis_id"])
        if selected:
            self.history_selector.setCurrentIndex(max(0, self.history_selector.findData(selected)))

    def load_history(self):
        if self.history_selector.currentData():
            self.guarded(lambda: self.analysis_ready(self.service.get(self.history_selector.currentData())))

    def compare_history(self):
        if self.record and self.history_selector.currentData():
            def operation():
                previous = self.service.get(self.history_selector.currentData())
                InformationDialog("分析版本差异 · 不自动替换档案", self.core.creative.diff(previous["analysis"], self.record["analysis"]), self).exec()
            self.guarded(operation)

    def build_profile(self):
        if not self.record:
            self.status.setText("请先分析资料或加载已有分析版本。")
            return
        if not self.profile_editor.may_leave():
            return
        def operation():
            value = self.service.profile(self.record, self.strength.currentData(), enforce_copy=False)
            self.profile_editor.populate(value)
            warning = copied_fragments(value, self.record["selections"])
            self.status.setText("已生成待编辑档案，尚未保存或应用。" + ("检测到长片段与参考相同，保存/导出/预览前请改成抽象描述。" if warning else "主参考优先，辅助只补充未知维度；来源和分析版本保留。"))
        self.guarded(operation)

    def compare_rules(self):
        def operation():
            bound = self.core.creative.state()["bindings"].get("writer")
            original = self.core.creative.profile(bound["profile_id"], bound["version"])["value"] if bound else template("自定义风格")
            InformationDialog("项目原风格与待编辑规则", self.core.creative.diff(original, self.profile_editor.value()), self).exec()
        self.guarded(operation)

    def preview(self):
        def operation():
            profile = self.core.creative.check_profile(self.profile_editor.value())
            scene = self.scene.toPlainText()
            if not scene.strip():
                raise CoreError("PREVIEW_INVALID", "请先输入一个用于比较的场景。")
            if not self.confirm_model("独立文风预览", "只发送本页场景和待编辑抽象风格规则，不发送参考全文。"):
                return
            self.background(lambda: self.service.preview(profile, scene), self.preview_ready)
        self.guarded(operation)

    def preview_ready(self, result):
        self.previous_preview.setPlainText(self.preview_view.toPlainText())
        self.preview_view.setPlainText(result["output"]["text"])
        self.scene_baseline = self.scene.toPlainText()
        self.refresh_previews(result["preview_id"])
        self.status.setText("预览已显示并保留本机历史，未保存为草稿或定稿。修改规则后可用同一场景再次比较；每次模型预览单独使用额度。")

    def refresh_previews(self, selected=None):
        self.preview_history.clear()
        for row in sorted(self.service.previews(), key=lambda r: r["number"], reverse=True):
            self.preview_history.addItem("预览 " + str(row["number"]) + " · " + row["title"], row["preview_id"])
        if selected:
            self.preview_history.setCurrentIndex(max(0, self.preview_history.findData(selected)))

    def may_leave_scene(self):
        return self.scene.toPlainText() == self.scene_baseline or QMessageBox.question(self, "预览场景还有新输入", "关闭或加载历史将放弃尚未生成并保留的新场景输入。是否继续？已有预览历史不受影响。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def load_preview(self):
        if not self.preview_history.currentData() or not self.may_leave_scene():
            return
        def operation():
            value = self.service.get_preview(self.preview_history.currentData())
            self.scene.setPlainText(value["scene"])
            self.scene_baseline = value["scene"]
            self.previous_preview.setPlainText(self.preview_view.toPlainText())
            self.preview_view.setPlainText(value["output"]["text"])
            self.status.setText("已加载历史场景和预览，当前待编辑规则没有变化。")
        self.guarded(operation)

    def reject(self):
        if self.job:
            self.status.setText("操作仍在进行，请等待结束后关闭；中断客户端不保证服务端停止。")
            return
        if self.profile_editor.may_leave() and self.may_leave_scene():
            super().reject()

    def closeEvent(self, event):
        if self.job:
            event.ignore()
        elif self.profile_editor.may_leave() and self.may_leave_scene():
            event.accept()
            super().reject()
        else:
            event.ignore()
