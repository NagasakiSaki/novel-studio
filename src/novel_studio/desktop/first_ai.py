"""Author-facing setup and local previews for the first AI-assisted chapter."""
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from ..core import CoreError
from ..providers import DESTINATIONS
from .provider_ui import ProviderDialog
from .workflow_display import plain


ROLES = (("writer", "主笔"), ("editor", "责编"))


def receiver(model):
    provider = model["provider"]
    return DESTINATIONS.get(provider, "Antigravity 官方宿主（下游模型接收方由宿主决定）")


def source_lines(sources, dynamic=False):
    lines = ["• " + name.removeprefix("Canonical/") for name in sources["documents"]]
    for row in sources["materials"]:
        detail = "完整预设" if row["context_mode"] == "full" else (
            "常驻规则与按章检索" if dynamic else f"选中 {len(row['sections'])} 个片段")
        lines.append(f"• {row['name']} · 第 {row['version']} 版 · {detail}")
    return "\n".join(lines) or "当前没有已批准的参考资料。"


class FirstCollaborationDialog(QDialog):
    def __init__(self, presenter):
        super().__init__(presenter.window)
        self.core, self.registry = presenter.core, presenter.providers
        self.intent = None
        self.prepared = None
        self.setObjectName("projectDialog")
        self.setWindowTitle("首次 AI 协作")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(750, screen.width() - 32), min(550, screen.height() - 44))
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(10)
        root.addWidget(plain("和 AI 一起试写一章", "dialogTitle"))
        self.role_info, self.configure_buttons = {}, {}
        for role, label in ROLES:
            row = QHBoxLayout()
            info = self.role_info[role] = plain("", "homeDescription")
            choose = self.configure_buttons[role] = QPushButton("配置" + label)
            choose.clicked.connect(lambda checked=False, selected=role: self.configure(selected))
            row.addWidget(info, 1)
            row.addWidget(choose)
            root.addLayout(row)
        self.stage = plain("", "section")
        root.addWidget(self.stage)
        self.idea = QPlainTextEdit()
        self.idea.setObjectName("composer")
        self.idea.setAccessibleName("故事构思")
        self.idea.setPlaceholderText("例如：一个修钟人发现镇上所有钟在同一分钟停摆。也可以从已批准的资料开始。")
        self.idea.setMaximumHeight(94)
        self.idea.setPlainText(presenter.window.workflow_panel.instruction.toPlainText())
        self.idea.textChanged.connect(self.invalidate)
        root.addWidget(self.idea)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setAccessibleName("发送范围预览")
        root.addWidget(self.summary, 1)
        root.addStretch()
        row = QHBoxLayout()
        close = QPushButton("返回作品")
        close.clicked.connect(self.reject)
        self.check = QPushButton("预览发送范围")
        self.check.clicked.connect(self.prepare)
        self.proceed = QPushButton()
        self.proceed.setObjectName("primary")
        self.proceed.clicked.connect(self.finish)
        row.addWidget(close)
        row.addStretch()
        row.addWidget(self.check)
        row.addWidget(self.proceed)
        root.addLayout(row)
        self.refresh()

    def configure(self, role):
        dialog = ProviderDialog(self.registry, self.core, role, self)
        dialog.exec()
        self.refresh()

    def refresh(self):
        state = self.core.workflow.store.read()
        run = state["runs"].get(state["active_run"])
        pending = any(row["status"] == "AUTHOR_APPROVAL_PENDING" for row in state["chapters"].values())
        approved = state["approved_outline"]
        latest = state["outline_order"][-1] if state["outline_order"] else None
        self.route = None
        if run and run["status"] not in {"COMPLETED", "STOPPED"}:
            self.route = ("查看当前创作任务", {"page": "create"}, "已有创作任务，可在创作页查看进度或处理问题。")
        elif pending:
            self.route = ("查看待确认章节", {"page": "chapters", "filter": "pending"}, "草稿和审稿已保存。选中章节即可阅读并决定是否收录。")
        elif latest and (not approved or latest != approved["version_id"]):
            self.route = ("查看并确认大纲", {"page": "outline"}, "已有大纲候选。查看内容并确认后，就可以试写一章。")
        self.stage_kind = "chapter" if approved else "outline"
        self.models_ready = True
        for role, label in ROLES:
            model = self.core.models.selected(role)
            detail = model.get("model_id")
            ready = bool(detail)
            line = f"{label} · {detail}\n{receiver(model)}" if ready else f"{label} · 尚未选择模型"
            if detail and model["provider"] != "antigravity":
                try:
                    self.registry.authorize_send(self.core, role, model["provider"])
                except CoreError:
                    ready = False
                    line += "\n请在配置中确认此角色的发送授权。"
            self.models_ready = self.models_ready and ready
            self.role_info[role].setText(line)
            self.configure_buttons[role].setEnabled(not run or run["status"] in {"COMPLETED", "STOPPED"})
        self.idea.setVisible(not self.route and self.stage_kind == "outline")
        self.check.setVisible(not self.route)
        self.check.setEnabled(self.models_ready)
        self.proceed.setText(self.route[0] if self.route else (
            "开始试写 1 章" if approved else "发送给责编，生成大纲"))
        self.invalidate()

    def invalidate(self):
        self.prepared = None
        self.proceed.setEnabled(bool(self.route))
        self.summary.hide()
        self.stage.setText(self.route[2] if self.route else (
            "先配置两个角色，再查看发送范围。" if not self.models_ready else
            "核对接收方和本次范围，再开始试写。" if self.stage_kind == "chapter" else
            "写下构思，或使用已批准资料。大纲生成后由你确认。"))

    def prepare(self):
        self.refresh()
        if self.route or not self.models_ready:
            return
        try:
            if self.stage_kind == "outline":
                idea = self.idea.toPlainText()
                binding, sources, model = self.core.workflow.planning.preview_generation(idea)
                text = f"接收方 · 责编 {model['model_id']}\n{receiver(model)}\n\n"
                text += "本次发送 · 故事构思、作品篇幅、责编审稿标准与下列资料，用于生成一份待确认的大纲。\n"
                text += source_lines(sources) + "\n\n模型服务可能计费。"
                self.prepared = ("outline_generate", {"instruction": idea, "preview_binding": binding})
            else:
                plan, sources, chapter = self.core.workflow.pipeline.preview_single_chapter()
                recipients = "\n".join(f"{label} · {plan['models'][role]['model_id']}\n{receiver(plan['models'][role])}"
                                       for role, label in ROLES)
                title = chapter["title"] or "标题待定"
                text = f"本次范围 · 第 {chapter['volume']} 卷第 {chapter['number']} 章《{title}》\n\n{recipients}\n\n"
                text += "会使用正式大纲、作者指导、角色设定和下列资料。已有前章时可引用前序摘要与近期正文。本章草稿供审稿和必要返修使用。按每步内容检索的片段可能不同。\n"
                text += source_lines(sources, dynamic=True)
                text += (f"\n\n规划、写作、审稿各 1 项，含返修最多 {plan['max_model_tasks']} 项模型任务。"
                         "一项任务可能包含多次官方请求，费用以供应商为准。需要作者裁决时会暂停。"
                         "草稿与审稿分开保存，正式收录由你批准。")
                if plan["warnings"]:
                    text += "\n\n" + "\n".join(plan["warnings"])
                self.prepared = ("workflow_start", {"first_preview": plan})
        except (OSError, ValueError) as error:
            text = str(error) if isinstance(error, CoreError) else "无法核对本次范围。请检查作品资料后重试。"
        self.summary.setPlainText(text)
        self.summary.show()
        self.proceed.setEnabled(self.prepared is not None)

    def finish(self):
        self.intent = ("ui_navigate", self.route[1]) if self.route else self.prepared
        if self.intent:
            self.accept()
