"""Read-only projections of existing domain state, never a second workflow engine."""
import re
from ..core import CoreError


def transcript_text(entry, role, state):
    """Keep original author words/scope visible; raw request stays in history."""
    request_id = entry.get("author_instruction_id")
    match = re.match(r"^【作者指令 ([a-f0-9]{32})】\n", entry.get("user", ""))
    request_id = request_id or (match[1] if match else None)
    author = next((r for r in state.get("author_instructions", []) if r["id"] == request_id), None)
    record = author or entry
    scope = "整部作品" if record.get("scope") == "book" else "历史对话 · 当时范围未记录"
    if record.get("scope") == "chapter":
        chapter = state.get("chapters", {}).get(record.get("chapter_id"))
        scope = f"第{chapter['volume']}卷第{chapter['number']}章" if chapter else "原章节（目前未能关联）"
    operation = {"discuss": "讨论", "revise": "作者要求修订", "review": "作者要求复审", "plan": "作者要求规划", "book_guidance": "全书指导"}.get(record.get("operation"), "对话")
    text = author["text"] if author else entry.get("user", "")
    return "【" + scope + " · " + operation + "】\n作者：" + text + "\n\n" + ("主笔：" if role == "writer" else "责编：") + entry.get("assistant", "")

EVENTS = {"outline_saved": "责编保存了新的大纲候选", "outline_approved": "作者确认了正式大纲",
    "chapter_planned": "责编章节计划已保存，可以交给主笔", "revision_needed": "责编发现重要问题，准备修订方案",
    "revision_plan_saved": "责编修订方案已保存", "chapter_revised": "主笔已保存修订稿，准备复审",
    "author_instruction_saved": "已收到作者直接指令", "author_instruction_executed": "作者指令已执行",
    "run_started": "开始本次连续创作", "chapter_written": "主笔完成了一章草稿", "chapter_reviewed": "责编保存了审稿意见",
    "next_direction_saved": "下一章方向已准备", "chapter_completed": "一章已完成，等待作者确认", "run_completed": "本次创作已完成",
    "run_control": "已记录暂停或停止要求", "run_resumed": "继续已保存的创作任务", "run_blocked": "任务暂停，等待作者裁决",
    "step_failed": "当前步骤未完成，原稿已保留", "accept_completed": "作者批准的章节已正式收录", "scale_saved": "作品规模已保存",
    "chapter_author_revision": "作者采用了修订稿", "accept_revision_index": "正式版本记录已更新"}


def next_step(state, busy=False):
    run = state["runs"].get(state["active_run"])
    if busy:
        return {"label": "查看当前进度", "action": "ui_navigate", "data": {"page": "create"}, "stage": "正在处理当前任务", "hint": "完成的内容会自动保存，运行中的请求不会重复发送。"}
    if run and run["status"] in {"RUNNING", "PAUSE_REQUESTED", "STOP_REQUESTED"}:
        return {"label": "查看创作进度", "action": "ui_navigate", "data": {"page": "create"}, "stage": "创作进行中", "hint": "主笔与责编按已确认范围协作，正式收录仍由您决定。"}
    if run and run["status"] == "PAUSED":
        return {"label": "继续创作", "action": "workflow_resume", "data": {}, "stage": "创作已暂停", "hint": "从已保存步骤继续；不会自动重写已完成章节。"}
    if run and run["status"] in {"FAILED", "BLOCKED"}:
        return {"label": "查看需要处理的问题", "action": "ui_navigate", "data": {"page": "create"}, "stage": "等待您决定", "hint": "先查看原因和已有结果，再决定继续或明确重试。"}
    pending = [r for r in state["chapters"].values() if r["status"] == "AUTHOR_APPROVAL_PENDING"]
    if pending:
        return {"label": f"查看待确认的 {len(pending)} 章", "action": "ui_navigate", "data": {"page": "chapters", "filter": "pending"}, "stage": "章节等待作者确认", "hint": "阅读正文和独立审稿，再批准满意的版本。"}
    newest = state["outline_order"][-1] if state["outline_order"] else None
    approved = state["approved_outline"]
    if newest and (not approved or newest != approved["version_id"]):
        return {"label": "查看并确认大纲", "action": "ui_navigate", "data": {"page": "outline"}, "stage": "大纲等待确认", "hint": "新候选保留历史，确认前不会替换正式创作依据。"}
    if not approved:
        return {"label": "写草稿", "action": "ui_navigate", "data": {"page": "files", "role": "writer"},
                "stage": "开始写作", "hint": "可以直接写一章。需要 AI 协作时，再去大纲页构思故事。"}
    rows = list(state["chapters"].values())
    execution = state.get("execution_plans", {}).get(approved["version_id"], {})
    if rows and all(r.get("canonical") and r["status"] == "ARCHIVED" for r in rows) and (not execution or execution.get("story_complete") or all(v["detailed"] for v in execution["volumes"])):
        return {"label": "查看完整作品", "action": "ui_navigate", "data": {"page": "chapters", "filter": "formal"}, "stage": "规划章节已正式收录", "hint": "可以阅读、导出，或由您主动安排新的修订。"}
    return {"label": "一键开始创作", "action": "workflow_start", "data": {}, "stage": "已准备好开始创作", "hint": "责编先规划，再写作、审稿与必要返修；正式收录仍由您决定。"}


def chinese_number(text):
    if text.isdecimal():
        return int(text)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    total, digit = 0, 0
    for char in text:
        if char in digits:
            digit = digits[char]
        elif char in {"十", "百", "千"}:
            total += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            digit = 0
        else:
            raise CoreError("OUTLINE_SCOPE", "无法确定卷章范围，请从范围菜单明确选择。")
    return total + digit


def outline_scope(text, value=None):
    pattern = r"第?([0-9零一二两三四五六七八九十百千]+)"
    volumes = re.findall(pattern + "卷", text)
    chapters = re.findall(pattern + "章", text)
    if not volumes and not chapters:
        return {"kind": "book"}
    if len(set(volumes)) > 1 or len(set(chapters)) > 1 or re.search(r"(?:不要|不改|不修改|勿改|保持)[^。；\n]{0,12}" + pattern + r"[卷章]", text):
        raise CoreError("OUTLINE_SCOPE", "这段要求涉及多个范围或有否定表述，请先在范围菜单选择整书或明确卷章，不会自动扩大修改范围。")
    volume = chinese_number(volumes[0]) if volumes else None
    chapter = chinese_number(chapters[0]) if chapters else None
    if volume is None:
        candidates = [v["number"] for v in (value or {}).get("volumes", []) if any(c["number"] == chapter for c in v["chapters"])]
        if len(candidates) != 1:
            raise CoreError("OUTLINE_SCOPE", "同一章号可能属于不同卷，请补充卷号，或从范围菜单选择。")
        volume = candidates[0]
    if not 1 <= volume <= 100 or chapter is not None and not 1 <= chapter <= 1000:
        raise CoreError("OUTLINE_SCOPE", "卷章编号超出当前范围，请核对后再发送。")
    return {"kind": "chapter", "volume": volume, "chapter": chapter} if chapter else {"kind": "volume", "volume": volume}


def scope_label(scope):
    return "整书规划" if scope["kind"] == "book" else f"第{scope['volume']}卷" + (f" · 第{scope['chapter']}章" if scope["kind"] == "chapter" else "")
