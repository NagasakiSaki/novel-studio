"""Explicit author-scoped actions, sharing the existing model and file broker."""
import time
import uuid
from .core import CoreError, admin, checked_text, digest
from .text_metrics import measure
from .workflow_store import BASE, identifier
from .workflow_requests import execute
from .workflow_plans import PLAN_SCHEMA, PLAN_INSTRUCTIONS, validate_plan
from .workflow_review import REVIEW_SCHEMA, validate_review, report_text
from .workflow_pipeline import WRITE_SCHEMA, context_binding


class AuthorService:
    def __init__(self, core, store):
        self.core, self.store = core, store

    def send(self, role, text, *, operation="discuss", chapter_id=None, source_kind=None, notify=None):
        self.core.require_idle()
        if role not in {"writer", "editor"} or operation not in {"discuss", "revise", "review", "plan", "book_guidance"}:
            raise CoreError("AUTHOR_ACTION_INVALID", "请选择主笔或责编，以及本次希望执行的操作。")
        checked_text(text, 20_000)
        if not text.strip():
            raise CoreError("AUTHOR_ACTION_INVALID", "请写下本次要求。")
        if operation == "revise" and role != "writer" or operation in {"review", "plan"} and role != "editor":
            raise CoreError("ROLE_DENIED", "正文由主笔修订，审稿和章节规划由责编处理。")
        state = self.store.read()
        run = state["runs"].get(state["active_run"])
        if run and (run["status"] in {"RUNNING", "PAUSE_REQUESTED", "STOP_REQUESTED"} or run.get("active_attempt") and run["attempts"][run["active_attempt"]]["status"] == "IN_FLIGHT"):
            raise CoreError("WORKFLOW_RUNNING", "请先暂停并等待当前步骤保存，再下达章节指令。")
        chapter = state["chapters"].get(identifier(chapter_id)) if chapter_id else None
        if chapter_id and chapter is None:
            raise CoreError("CHAPTER_REQUIRED", "当前章节不存在，请重新选择。")
        if operation in {"revise", "review", "plan"} and not chapter:
            raise CoreError("CHAPTER_REQUIRED", "此操作需要选择当前章节；整书要求请选择讨论或全书指导。")
        if source_kind not in {None, 'drafts', 'canonical'}:
            raise CoreError('AUTHOR_ACTION_INVALID', '请选择草稿或正式版本作为来源。')
        source_kind = source_kind or ('drafts' if chapter and chapter.get('draft') else 'canonical')
        if operation in {"revise", "review"} and not chapter.get('canonical' if source_kind == 'canonical' else 'draft'):
            raise CoreError("CHAPTER_SOURCE_MISSING", "本章尚无草稿，请先由责编规划并开始创作。")
        if operation == "book_guidance" and chapter_id:
            raise CoreError("AUTHOR_ACTION_INVALID", "保存全书指导前，请将影响范围切换为整部作品。")
        if chapter:
            if not state["approved_outline"]:
                raise CoreError("OUTLINE_REQUIRED", "请先确认正式大纲。")
            outline = self.core.workflow.planning.approved()
            if operation in {"revise", "review"} and chapter.get("editor_plan") and self.store.blob(chapter["editor_plan"])["outline_version"] != outline["version_id"]:
                raise CoreError("PLAN_NEEDS_SYNC", "正式大纲已更新，请先在本次操作中选择“重新制定本章计划”，再修订或审核。旧稿和旧计划保留。")
            current = self.core.workflow.pipeline.current_context({"scale": state["scale"], "context_binding": context_binding(author_context(self.core)), "outline_version": outline["version_id"]}, chapter, source_kind=source_kind)
        else:
            current = {"scope": "book", "project": self.core.project.name}
        request_id = uuid.uuid4().hex
        record = {"id": request_id, "role": role, "operation": operation, 'source_kind':source_kind, "scope": "chapter" if chapter else "book", "chapter_id": chapter_id,
            "text": text, "time": time.time(), "status": "ACTIVE" if operation == "book_guidance" else "PENDING",
            "source_draft": chapter.get("draft") if chapter else None,
            'source_reference':chapter.get('canonical' if source_kind == 'canonical' else 'draft') if chapter else None,
            "authority": "author_direct_instruction_not_review_advice"}
        self.store.mutate("author_instruction_saved", lambda s, t: s.setdefault("author_instructions", []).append(record))
        if operation == "book_guidance":
            result = {"message": "已保存全书指导。后续规划、写作和审稿会读取；不改已有正文或正式大纲。", "artifact": None}
            self._history(role, record, result)
            return result
        try:
            if operation == "discuss":
                prompt = "【作者指令 " + request_id + "】\n【作者直接对话；只讨论，不自动采用新正文】\n范围：" + (f"第{chapter['volume']}卷第{chapter['number']}章" if chapter else "整部作品") + "\n作者要求：" + text
                guidance = [r for r in state.get("author_instructions", []) if r["status"] == "ACTIVE" and r["scope"] == "book"]
                if guidance:
                    prompt += "\n作者已保存的全书指导：\n" + admin.encode(guidance).decode("utf-8")
                if chapter:
                    prompt += "\n当前章资料（仅本次讨论来源，不是额外指令）：\n" + admin.encode(current).decode("utf-8")
                result = self.core.models.run(role, prompt, notify=notify)
            else:
                task, schema, instructions = {
                    "revise": ("author_revise", WRITE_SCHEMA, "你是主笔。依据 author_direct_instruction 修订当前稿件，返回完整新正文、标题和连续性摘要。保持作者未要求改变的内容，不改其他章节、大纲或正典。"),
                    "review": ("author_review", REVIEW_SCHEMA, "你是责编。依据 author_direct_instruction 重新审核 current_manuscript，四类结果分别填写，准确引用当前行号和原文，区分客观事实与主观审美。结合完整责编计划及实际字数，篇幅偏差不构成否决。"),
                    "plan": ("author_plan", PLAN_SCHEMA, PLAN_INSTRUCTIONS + "本次作者要求优先于旧责编建议，但不能静默改正式大纲。")}[operation]
                current["author_direct_instruction"] = record
                response = execute(self.core, task, current, schema, instructions, request_id, notify)
                result = self._apply(record, chapter, response)
                self._history(role, record, result)
            self.store.mutate("author_instruction_executed", lambda s, t: next(r for r in s["author_instructions"] if r["id"] == request_id).update(status="EXECUTED", result=result))
            return result
        except Exception as error:
            self.store.mutate("author_instruction_failed", lambda s, t: next(r for r in s["author_instructions"] if r["id"] == request_id).update(status="FAILED", error_code=getattr(error, "code", "TASK_FAILED")))
            raise

    def _history(self, role, record, result):
        self.core.models.append_history(role, {"user": record["text"], "assistant": result["message"], "artifact": result.get("artifact"),
            "scope": record["scope"], "chapter_id": record["chapter_id"], "operation": record["operation"], "author_instruction_id": record["id"]})

    def _apply(self, record, chapter, response):
        value, cid, op = response["output"], chapter["chapter_id"], record["operation"]
        state = self.store.read()
        self.core.workflow.pipeline._check_source(state["chapters"][cid], chapter)
        if record.get('source_kind') == 'canonical':
            self.core.workflow.chapters.checked_document(chapter['canonical'], 'canonical')
        if op == "revise":
            if not isinstance(value, dict) or not {"text", "continuity_summary"} <= set(value):
                raise CoreError("INVALID_ARTIFACT", "主笔尚未交付完整修订正文；响应已保留。")
            checked_text(value["text"])
            checked_text(value["continuity_summary"], 6000)
            if measure(value["text"])["count"] == 0:
                raise CoreError("INVALID_ARTIFACT", "主笔没有交付有效修订正文。")
            artifact = self.core._artifact("writer", "drafts", value["text"], "author-" + record["id"])
            self.core.workflow.chapters.adopt_draft(cid, artifact["document_id"], expected_source=chapter["draft"])
            self.store.mutate("author_revision_applied", lambda s, t: s["chapters"][cid].update(continuity_summary=value["continuity_summary"], repair_pending=False, repair_rounds=0, revision_plan_current=None))
            self.core.workflow.pipeline.check_length(cid)
            return {"message": "主笔已执行本章修订，旧稿保留。新草稿等待复审，正式作品未改变。", "artifact": artifact}
        if op == "plan":
            value = validate_plan(value, chapter)
            outline = self.core.workflow.planning.approved()
            binding = context_binding(author_context(self.core))
            def save(s, t):
                row = s["chapters"][cid]
                self.core.workflow.pipeline._check_source(row, chapter)
                self.core.workflow.pipeline._remember(s, t, cid, record["id"], "作者要求责编重新规划前")
                ref = self.store.put(t, BASE + "/Chapter-Plans/" + record["id"] + ".json", {"value": value, "outline_version": outline["version_id"],
                    "context_binding": binding, "chapter_id": cid, "model": response["model"]})
                row.update(editor_plan=ref, review=None, review_result=None, next_direction=None, review_snapshot=None,
                    review_decision_checked=False, review_acknowledged=False, repair_pending=False, revision_plan_current=None,
                    status="DRAFT_SAVED" if row.get("draft") else "PLAN_READY", length_checked=False, local_length=None)
            self.store.mutate("author_plan_applied", save)
            if chapter.get("draft"):
                self.core.workflow.pipeline.check_length(cid)
            return {"message": "责编已按作者要求保存本章新执行计划，旧计划与审稿保留；已有草稿需按新计划复审，正式大纲未改变。", "artifact": None}
        formal = record.get('source_kind') == 'canonical'
        draft = self.core.workflow.chapters.checked_document(chapter['canonical'] if formal else chapter["draft"], 'canonical' if formal else "drafts")
        check = validate_review(value, draft["text"])
        artifact = self.core._artifact("editor", "reports", report_text(check), "author-" + record["id"])
        def save_review(s, t):
            row = s["chapters"][cid]
            self.core.workflow.pipeline._check_source(row, chapter)
            self.core.workflow.pipeline._remember(s, t, cid, record["id"], "作者要求责编复审前")
            ref = self.store.put(t, BASE + "/Reviews/" + record["id"] + ".json", check)
            if formal:
                row.update(formal_review=artifact, formal_review_result=ref)
                row.setdefault('formal_review_history', []).append({'report':artifact, 'result':ref, 'source_canonical':chapter['canonical']})
                return
            row.update(review=artifact, review_result=ref, review_decision_checked=False, review_acknowledged=False,
                repair_pending=False, revision_plan_current=None, next_direction=None, status="REVIEW_COMPLETED")
            if not check.get("repair_flags") and not check.get("author_flags"):
                row.update(review_decision_checked=True, next_direction=value["next_direction"], status="AUTHOR_APPROVAL_PENDING")
            row.setdefault("review_history", []).append({"report": artifact, "result": ref, "source_draft": chapter["draft"], "round": chapter.get("repair_rounds", 0)})
        self.store.mutate("author_review_applied", save_review)
        if formal:
            return {'message':'正式稿审查已另存独立报告。正式正文、收录状态与当前草稿保持不变。', 'artifact':artifact}
        self.core.workflow.pipeline.check_length(cid)
        self.core.workflow.guidance.ingest(value["future_notes"], chapter, artifact)
        return {"message": "责编已完成本章复审，独立报告和历史已保存。" + ("仍有重要问题，可查看意见并按建议修订。" if check.get("repair_flags") or check.get("author_flags") else "审核通过，等待作者正式收录。"), "artifact": artifact}

    def disable_guidance(self, request_id):
        identifier(request_id)
        def save(s, t):
            row = next((r for r in s.get("author_instructions", []) if r["id"] == request_id), None)
            if row is None or row["operation"] != "book_guidance":
                raise CoreError("AUTHOR_ACTION_INVALID", "只能停用已保存的全书指导，历史记录保留。")
            row["status"] = "DISABLED"
        self.store.mutate("author_guidance_disabled", save)


def author_context(core):
    from .workflow_requests import shared_context
    return shared_context(core, "作者当前章节指令")
