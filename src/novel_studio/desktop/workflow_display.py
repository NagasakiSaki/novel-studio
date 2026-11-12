"""Workflow display vocabulary and label rendering, independent of pages/controllers."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

STATUS = {"PLANNED": "尚未创作", "DRAFT_SAVED": "待审核", "REVIEW_COMPLETED": "整理后续方向",
    "PLAN_READY": "责编计划已备妥", "REVISION_NEEDED": "需要自动修订", "REVISION_READY": "修订方案已备妥",
    "AUTHOR_APPROVAL_PENDING": "待作者确认", "ARCHIVED": "正式章节", "RUNNING": "创作中", "PAUSED": "已暂停",
    "STOPPED": "已停止", "COMPLETED": "本次范围已完成", "BLOCKED": "等待作者决定", "FAILED": "步骤未完成",
    "PAUSE_REQUESTED": "保存本步结果后暂停", "STOP_REQUESTED": "保存本步结果后停止"}
PHASE = {"CHAPTER_WRITING": "主笔写作", "CHAPTER_LENGTH_CHECK": "本地核对字数", "CHAPTER_REVIEWING": "责编审稿并提出后续方向",
    "CHAPTER_PLANNING": "责编正在制定章节计划", "REVISION_PLANNING": "责编正在制定修订方案", "CHAPTER_REVISING": "主笔正在按方案修订",
    "REVIEW_DECISION": "检查是否需要作者裁决", "NEXT_CHAPTER_PLANNING": "保存下一章方向与精简指导", "CHAPTER_DONE": "保存本章进度"}


def plain(text, name=None):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    if name:
        label.setObjectName(name)
    return label


def model_label(core, role):
    value = core.models.selected(role) if core and core.models else {}
    names = {"antigravity": "Antigravity", "openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Gemini"}
    return names.get(value.get("provider"), "尚未选择") + " · " + str(value.get("model_id") or "请选择模型")
