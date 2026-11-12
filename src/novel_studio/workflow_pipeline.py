"""One durable transition per advance; model work is not an unbounded loop."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from .core import CoreError, SafeTree, admin, checked_text, digest
from .text_metrics import measure
from .lengths import evaluate
from .workflow_store import BASE, WorkflowStore, identifier, alive
from .workflow_requests import context_sources, execute, shared_context
from .workflow_planning import BOOK_TEXT, object_schema, text_fields
from .workflow_review import REVIEW_SCHEMA, validate_review, report_text
from .workflow_execution import MAX_CHAPTERS, finished
from .workflow_plans import PLAN_SCHEMA, REPAIR_SCHEMA, validate_plan, validate_repair, PLAN_INSTRUCTIONS, REPAIR_INSTRUCTIONS
from .context_budget import prepare_context
from .diagnostics import record as diagnostic_record

EXECUTION_SCHEMA = object_schema({**text_fields(("volume_title", "chapter_goal")),
    "volume_complete": {"type": "boolean"}, "book_complete": {"type": "boolean"}})
WRITE_SCHEMA = object_schema({**text_fields(("title", "text", "continuity_summary")), "execution": EXECUTION_SCHEMA})
RUNNING = {"RUNNING", "PAUSE_REQUESTED", "STOP_REQUESTED"}
TERMINAL = {"COMPLETED", "STOPPED"}


def model_configuration(models, role):
    value = models.selected(role)
    return {key: value.get(key) for key in ("provider", "model_id", "effort", "max_output_tokens", "temperature", "cache")}


def context_binding(shared):
    return digest(admin.encode({"documents": [{"name": r["name"], "revision": r["revision"]} for r in shared["approved_context"]],
        "materials": [{"material_id": r["material_id"], "record_sha256": r["record_sha256"]} for r in shared["material_receipt"]]}))


class PipelineService:
    def __init__(self, core, store=None):
        self.core, self.store = core, store or WorkflowStore(core)

    def active(self):
        state = self.store.read()
        return state["runs"].get(state["active_run"])

    def get(self, run_id):
        identifier(run_id)
        row = self.store.read()["runs"].get(run_id)
        if row is None:
            raise CoreError("RUN_MISSING", "所选连续创作任务不存在。")
        return row

    def models(self):
        if self.core.models is None:
            raise CoreError("MODEL_REQUIRED", "请先分别设置主笔和责编模型。")
        result = {role: model_configuration(self.core.models, role) for role in ("writer", "editor")}
        for role, selected in result.items():
            if not selected.get("model_id"):
                raise CoreError("MODEL_REQUIRED", "请分别选择主笔和责编的模型后再开始。")
            if selected["provider"] != "antigravity":
                if self.core.models.api is None:
                    raise CoreError("API_NOT_CONFIGURED", "请在模型管理中配置并授权对应角色；不会自动切换模型。")
                self.core.models.api.registry.authorize_send(self.core, role, selected["provider"])
        return result

    def preview(self, mode="chapters", count=1, creative_mode="continuous", review_policy="author_pause"):
        plan, _, _ = self._preview(mode, count, creative_mode, review_policy)
        return plan

    def preview_single_chapter(self):
        plan, shared, selected = self._preview("chapters", 1, "continuous", "author_pause")
        return plan, context_sources(shared), selected[0]

    def _preview(self, mode, count, creative_mode, review_policy):
        self.core.require_idle()
        shared = shared_context(self.core, "创作启动前资料检查")
        self.core.workflow.chapters.ensure()
        # Unplaced files cannot block an isolated draft. They do not become
        # completed chapters automatically; acceptance checks concrete targets.
        unplaced = self.core.workflow.chapters.unindexed()
        if mode not in {"chapters", "volumes", "book"} or creative_mode not in {"continuous", "assisted"} or review_policy not in {'author_pause','continue_drafts'} or type(count) is not int or not 1 <= count <= 1000:
            raise CoreError("RUN_LIMIT_REQUIRED", "请选择明确的章节数、卷数或全书目标，不允许无限续写。")
        active = self.active()
        if active and active["status"] not in TERMINAL:
            raise CoreError("ACTIVE_RUN", "已有未完成任务，请继续或停止该任务，不重复创建。")
        planned = self.core.workflow.chapters.planned()
        state = self.store.read()
        available = [row for row in planned if not finished(state["chapters"][row["chapter_id"]])]
        if not available:
            description = self.core.workflow.execution.describe(state)
            if not description["story_complete"] and not description["count_known"]:
                raise CoreError("EXECUTION_LIMIT", "当前有限目录或运行安全上限已用完，故事尚未宣告完成；原稿保留，请作者安排后续范围。")
            raise CoreError("BOOK_COMPLETE", "已确认大纲中的章节均已完成草稿和审稿；请检查待确认章节。")
        if mode == "chapters":
            selected = available[:count]
        elif mode == "volumes":
            volumes = sorted({r["volume"] for r in available})[:count]
            selected = [r for r in available if r["volume"] in volumes]
        else:
            selected = available
        execution = self.core.workflow.execution.current(state)
        dynamic = execution["dynamic_volumes"] or any(not v["detailed"] and not v["closed"] for v in execution["volumes"] if v["number"] in {r["volume"] for r in selected})
        budget = min(count if mode == "chapters" else MAX_CHAPTERS, MAX_CHAPTERS - sum(finished(r) for r in planned))
        if budget <= 0:
            raise CoreError("EXECUTION_LIMIT", "已达到本作品当前目录安全上限，原稿保留；请作者安排后续范围。")
        selected = selected[:budget]
        volume_scope = None if mode == "chapters" or mode == "book" and execution["dynamic_volumes"] else sorted({r["volume"] for r in selected})
        warnings = self.core.workflow.planning.coverage(self.core.workflow.planning.approved()["value"])
        if unplaced:
            warnings.append(f"发现 {len(unplaced)} 份尚未关联章节目录的现有文件，已原样保留。本次可以先写独立草稿；这些文件不会自动当作已完成章节，正式收录时只核对所选编号及目标冲突，无需先整理全部旧文件。")
        plan = {"mode": mode, "requested_count": count, "workflow_version": 2, "creative_mode": creative_mode, "review_policy":review_policy, "repair_limit": 2,
            "chapter_ids": [r["chapter_id"] for r in selected],
            "chapter_count": len(selected), "volume_count": len({r["volume"] for r in selected}),
            "execution_scope": {"volumes": volume_scope, "chapter_limit": budget if dynamic else len(selected), "dynamic": dynamic},
            "outline_version": self.core.workflow.planning.approved()["version_id"], "models": self.models(),
            "context_binding": context_binding(shared),
            "scale": self.core.workflow.scale.get(), "estimated_model_tasks": None if dynamic and mode != "chapters" else (budget if dynamic else len(selected)) * 3,
            "max_model_tasks": (budget if dynamic else len(selected)) * 9,
            "warnings": warnings,
            "unplaced_files": [{k: r[k] for k in ("document_id", "revision", "relative", "suggested_volume", "suggested_number")} for r in unplaced],
            "notice": "已读取你的正式大纲。每章先由责编规划，再由主笔写作和责编审稿；重要问题最多两轮方案、修订与复审。缺少卷章信息会逐步补全，实际结构与篇幅以正式大纲为准。只保存待确认草稿，正式收录仍需作者批准。模型调用可能计费；暂停在当前步骤保存后生效。"}
        return plan, shared, selected

    def start(self, preview, confirmed=False):
        if not confirmed:
            raise CoreError("RUN_APPROVAL_REQUIRED", "请确认两个模型、大纲、字数目标和本次有限范围。")
        fresh = self.preview(preview["mode"], preview["requested_count"], preview.get("creative_mode", "continuous"), preview.get('review_policy','author_pause'))
        if fresh != preview:
            raise CoreError("REVISION_CONFLICT", "启动前的模型、规划或待写范围已变化，请重新查看。")
        return self._create(fresh)

    def start_review(self, cid, confirmed=False):
        if not confirmed:
            raise CoreError("RUN_APPROVAL_REQUIRED", "请明确同意对当前已保存的草稿重新审阅。")
        chapter = self.store.read()["chapters"].get(identifier(cid))
        if chapter is None or not chapter.get("draft"):
            raise CoreError("CHAPTER_SOURCE_MISSING", "请先采用一份已保存草稿。")
        return self._create({"mode": "review_only", "workflow_version": 2, "creative_mode": "assisted", "repair_limit": 2,
            "requested_count": 1, "chapter_ids": [cid], "chapter_count": 1, "volume_count": 1,
            "outline_version": self.core.workflow.planning.approved()["version_id"], "models": self.models(),
            "context_binding": context_binding(shared_context(self.core, "重新审核已保存稿件")), "scale": self.core.workflow.scale.get(),
            "estimated_model_tasks": 2 if not chapter.get("editor_plan") else 1, "max_model_tasks": 9,
            "notice": "辅助模式：补齐责编计划、重新审稿；重要问题可按步骤修订草稿，正式版本不变。"})

    def _create(self, fresh):
        self.core.require_idle()
        run_id = uuid.uuid4().hex
        def operation(state, tree):
            previous = state["runs"].get(state["active_run"])
            if previous and previous["status"] not in TERMINAL:
                raise CoreError("ACTIVE_RUN", "其他窗口已经创建任务，未重复开始。")
            run = {**fresh, "run_id": run_id, "status": "RUNNING", "index": 0, "attempts": {}, "active_attempt": None,
                "requested_control": None, "completed_chapters": [], "created": time.time(), "error": None, "block": None}
            state["runs"][run_id] = run
            state["active_run"] = run_id
            if fresh['mode'] in {'book','volumes','chapters'}:
                state['creation_preferences']={'mode':fresh['mode'],'count':fresh['requested_count']}
            return run
        return self.store.mutate("run_started", operation)

    def control(self, run_id, action):
        if action not in {"pause", "stop"}:
            raise CoreError("RUN_CONTROL_INVALID", "请选择暂停或停止。")
        identifier(run_id)
        def operation(state, tree):
            run = state["runs"][run_id]
            if run["status"] in TERMINAL:
                return run
            requested = "PAUSED" if action == "pause" else "STOPPED"
            run["requested_control"] = requested
            in_flight = run.get("active_attempt") and run["attempts"][run["active_attempt"]]["status"] == "IN_FLIGHT"
            run["status"] = ("PAUSE_REQUESTED" if action == "pause" else "STOP_REQUESTED") if in_flight else requested
            return run
        return self.store.mutate("run_control", operation, control=True)

    def resume(self, run_id, *, retry_confirmed=False, accept_warning=False, adopt_models=False, adopt_scale=False, adopt_context=False, upgrade_workflow=False):
        self.core.require_idle()
        run = self.get(run_id)
        if run.get("workflow_version", 1) < 2 and not upgrade_workflow:
            raise CoreError("WORKFLOW_UPGRADE_REQUIRED", "旧任务每章只授权写作和审稿。1.3 增加责编规划与最多两轮返修，每章最多九次任务；请查看新流程后明确同意升级，旧稿保留。")
        if run["status"] in TERMINAL:
            raise CoreError("RUN_FINISHED", "该任务已经结束；保留成果，按新的有限范围开始新任务。")
        current_models = self.models()
        if current_models != run["models"] and not adopt_models:
            raise CoreError("MODEL_CHANGED", "模型设置已变化，请明确同意后续步骤采用新模型；旧记录不改。")
        current_scale = self.core.workflow.scale.get()
        if current_scale != run["scale"] and not adopt_scale:
            raise CoreError("SCALE_CHANGED", "作品目标已变化，请明确同意后续步骤采用新目标。")
        current_binding = context_binding(shared_context(self.core, "创作恢复前资料检查"))
        if current_binding != run["context_binding"] and not adopt_context:
            raise CoreError("CONTEXT_CHANGED", "已批准的资料副本已更新，请明确同意后续步骤使用新资料；旧响应不会冒充新资料结果。")
        if self.core.workflow.planning.approved()["version_id"] != run["outline_version"]:
            raise CoreError("OUTLINE_CHANGED", "正式大纲已更新，请停止旧任务并按新规划开始，不能静默换版。")
        attempt = run["attempts"].get(run.get("active_attempt"))
        staged = False
        if attempt and attempt["status"] in {"IN_FLIGHT", "FAILED", "RESULT_READY"}:
            if attempt["status"] == "IN_FLIGHT" and (alive(attempt.get("owner_pid")) is not False or attempt.get("host_pid") and alive(attempt["host_pid"]) is not False):
                raise CoreError("WORKFLOW_RUNNING", "上次的本机请求进程尚未确认结束，请等待后再恢复。")
            with SafeTree(self.core.project) as tree:
                staged = tree.maybe_read(BASE + "/Attempts/" + run["active_attempt"] + "/result.json") is not None
            if staged and retry_confirmed:
                staged = False  # Explicitly request a new model attempt, retaining old evidence.
            if not staged and not retry_confirmed:
                raise CoreError("RESULT_UNKNOWN", "上次结果未知，可能已生成或收费；不会自动重发。明确重新尝试会再次调用模型。")
        if staged and (current_models != run["models"] or current_scale != run["scale"] or current_binding != run["context_binding"]):
            raise CoreError("STALE_RESULT", "已有响应依据旧模型、目标或资料生成。结果已保留；若按新配置重新生成，请明确选择重新请求模型，不把旧响应冒充新配置结果。")
        if run.get("block") and run["block"]["kind"] not in ({"models", "scale", "context", "length", "upgrade"} if upgrade_workflow else {"models", "scale", "context", "length"}) and not accept_warning:
            raise CoreError("AUTHOR_DECISION_REQUIRED", "请先查看暂停原因，并由作者明确决定是否继续。")
        def operation(state, tree):
            row = state["runs"][run_id]
            if adopt_models:
                row["models"] = current_models
            if adopt_scale:
                row["scale"] = current_scale
            if adopt_context:
                row["context_binding"] = current_binding
            if row.get("block") and (accept_warning or row["block"]["kind"] == "length") and row["block"]["kind"] not in {"models", "scale", "context", "upgrade"}:
                cid = row["chapter_ids"][row["index"]]
                chapter = state["chapters"][cid]
                if row["block"]["kind"] == "length":
                    chapter["length_acknowledged"] = True
                elif row["block"]["kind"] == "review":
                    chapter["review_acknowledged"] = True
                    chapter["repair_pending"] = False
                else:
                    raise CoreError("AUTHOR_DECISION_REQUIRED", "此问题需要修复来源或配置，不能只忽略提示。")
            if staged:
                row["attempts"][row["active_attempt"]]["status"] = "RESULT_READY"
            elif attempt and attempt["status"] in {"FAILED", "IN_FLIGHT", "RESULT_READY"}:
                row["attempts"][row["active_attempt"]]["status"] = "ABANDONED_BY_AUTHOR"
                row["active_attempt"] = None
            row.update(status="RUNNING", requested_control=None, error=None, block=None)
            if retry_confirmed:
                row["max_model_tasks"] = row.get("max_model_tasks", len(row["chapter_ids"]) * 9) + 1
            if upgrade_workflow and row.get("workflow_version", 1) < 2:
                row.update(workflow_version=2, creative_mode="continuous", repair_limit=2,
                    max_model_tasks=len(row["chapter_ids"]) * 9, upgraded_by_author=True)
            return row
        return self.store.mutate("run_resumed", operation)

    def phase(self, chapter):
        if not chapter.get("editor_plan"):
            return "CHAPTER_PLANNING"
        if chapter.get("repair_pending"):
            return "CHAPTER_REVISING" if chapter.get("revision_plan_current") else "REVISION_PLANNING"
        if not chapter.get("draft"):
            return "CHAPTER_WRITING"
        if not chapter.get("length_checked"):
            return "CHAPTER_LENGTH_CHECK"
        if not chapter.get("review"):
            return "CHAPTER_REVIEWING"
        if not chapter.get("review_decision_checked"):
            return "REVIEW_DECISION"
        if not chapter.get("next_direction"):
            return "NEXT_CHAPTER_PLANNING"
        return "CHAPTER_DONE"

    def current_context(self, run, chapter, *, source_kind='drafts'):
        record = self.core.workflow.planning.approved()
        query = chapter["title"] + " " + chapter["plan"]["summary"] + " " + chapter["plan"]["characters"]
        shared = shared_context(self.core, query)
        if context_binding(shared) != run["context_binding"]:
            raise CoreError("CONTEXT_CHANGED", "资料副本已变化，请先暂停并确认后续使用范围。")
        counts = self.core.lengths.counts()["nodes"]["book"]["count"]
        planned_count = len(self.core.workflow.chapters.planned())
        count_known = self.core.workflow.execution.describe()["count_known"]
        book_target = run["scale"]["total_words"] or (planned_count * run["scale"]["chapter_target"] if count_known else None)
        current = {**shared, "outline_version": record["version_id"], "approved_outline_text": record["text"],
            "outline_overview": {k: record["value"][k] for k in BOOK_TEXT}, "chapter": {k: chapter[k] for k in ("volume", "number", "ordinal", "title", "plan")},
            "volume_plan": self.core.workflow.execution.volume(chapter)["plan"],
            "execution_plan": {"authority": "derived execution plan, below approved outline", "volume_title": chapter["volume_title"],
                "title_is_author_confirmed": chapter.get("title_source", "author" if chapter["title"] else "pending") == "author",
                "dynamic_volumes": self.core.workflow.execution.current()["dynamic_volumes"], "declared_volumes": len(self.core.workflow.execution.current()["volumes"]),
                "detailed_volume": self.core.workflow.execution.volume(chapter)["detailed"], "scope": run.get("execution_scope")},
            "goal": {**self.core.workflow.scale.goal(chapter["chapter_id"]), "strict": False, "guidance_only": True}, "metric": self.core.lengths.plan()["metric"],
            "book_progress": {"planned_chapters": planned_count if count_known else None, "registered_chapters": planned_count,
                "chapter_count_known": count_known, "current_ordinal": chapter["ordinal"],
                "book_target_words": book_target, "actual_local_words": counts,
                "remaining_words": max(book_target - counts, 0) if book_target else None,
                "target_basis": "global_advisory" if run["scale"]["total_words"] else "approved_detailed_plan_estimate" if count_known else "undetermined",
                "chapter_target": run["scale"]["chapter_target"], "guidance_only": True},
            "guidance": self.core.workflow.guidance.select(chapter["ordinal"], query),
            "rules": "优先级：作者确认大纲 > 当前实际执行计划 > 全局篇幅建议 > 软件默认值。篇幅只是建议，不凑字、不截断、不把目标当否决条件。前章未确认草稿不是正典。只写本章，不修改前章或大纲，不自行批准。"}
        state = self.store.read()
        if chapter.get("editor_plan"):
            saved_plan = self.store.blob(chapter["editor_plan"])
            current["editor_execution_plan"] = saved_plan
            current["goal"] = {**current["goal"], **saved_plan["value"]["word_range"], "strict": False, "basis": "editor_plan_under_approved_outline"}
        if chapter.get("revision_plan_current"):
            current["revision_plan"] = self.store.blob(chapter["revision_plan_current"])
        if chapter.get("review_result"):
            current["current_review"] = self.store.blob(chapter["review_result"])
        current["author_instructions"] = [r for r in state.get("author_instructions", []) if r["status"] == "ACTIVE" and r["scope"] == "book"]
        previous = [row for row in self.core.workflow.chapters.catalog() if row["ordinal"] < chapter["ordinal"] and (row.get("draft") or row.get("canonical"))]
        current["previous_chapter_summaries"] = [{"ordinal": r["ordinal"], "title": r["title"], "summary": r.get("continuity_summary", r["plan"]["summary"]),
            "status": "author_approved" if r.get("canonical") else "unapproved_draft"} for r in previous[-8:]]
        current["recent_manuscripts"] = []
        for row in previous[-2:]:
            area = "canonical" if row.get("canonical") else "drafts"
            source = self.core.workflow.chapters.checked_document(row["canonical"] if area == "canonical" else row["draft"], area)
            current["recent_manuscripts"].append({"ordinal": row["ordinal"], "text": source["text"], "revision": source["revision"], "authority": "author_approved" if area == "canonical" else "unapproved_draft"})
        if previous:
            current["previous_editor_direction"] = previous[-1].get("next_direction")
            if previous[-1].get("review_result"):
                current["previous_quality_advice"] = {"authority": "review_advice_not_author_instruction", "source_report": previous[-1]["review"],
                    "notes": self.store.blob(previous[-1]["review_result"])["value"]["current_quality"]}
        manuscript = chapter.get('canonical' if source_kind == 'canonical' else 'draft')
        if manuscript:
            source = self.core.workflow.chapters.checked_document(manuscript, source_kind)
            current["current_manuscript"] = {"text": source["text"], "document_id": source["document_id"], "revision": source["revision"], "line_numbers": "从 text 的第一行开始计数，标题也占一行。"}
            current['current_manuscript']['authority'] = 'author_approved_formal_source' if source_kind == 'canonical' else 'unapproved_draft'
            current["actual_word_count"] = measure(source["text"], self.core.lengths.plan()["metric"])["count"]
            if chapter.get("review_snapshot") and source_kind == 'drafts':
                current["current_manuscript"]["snapshot"] = chapter["review_snapshot"]
        current, metrics = prepare_context(current)
        diagnostic_record(self.core, 'workflow_step', action='context_prepared', **metrics)
        return current

    def _block(self, run_id, kind, message, detail=None):
        self.store.mutate("run_blocked", lambda s, t: s["runs"][run_id].update(status="BLOCKED", block={"kind": kind, "message": message, "detail": detail}), control=True)
        return self.get(run_id)

    def check_length(self, cid):
        chapter = self.store.read()["chapters"][identifier(cid)]
        row = self.core.workflow.chapters.checked_document(chapter["draft"], "drafts")
        self.core.lengths.link(cid, row["document_id"])
        self.core.lengths.activate(cid)
        goal = self.core.workflow.scale.goal(cid)
        if chapter.get("editor_plan"):
            goal = {**goal, **self.store.blob(chapter["editor_plan"])["value"]["word_range"], "strict": False}
        value = evaluate(measure(row["text"], self.core.lengths.plan()["metric"])["count"], goal)
        value.update(guidance_only=True, blocking=False, strict=False)
        def save(s, t):
            self._check_source(s["chapters"][cid], chapter)
            s["chapters"][cid].update(length_checked=True, local_length=value)
        self.store.mutate("chapter_length_checked", save)
        return value

    def _finish_control(self, state, run_id):
        run = state["runs"][run_id]
        if run.get("requested_control"):
            run["status"] = run["requested_control"]
        return run

    def _finish_step(self, state, tree, run_id, attempt_id, artifact=None):
        run = state["runs"][run_id]
        run["attempts"][attempt_id].update(status="COMPLETED", artifact=artifact)
        run["active_attempt"] = None
        if run.get("creative_mode") == "assisted" and not run.get("requested_control"):
            run.update(status="PAUSED", assist_notice="本步已保存。可在章节页查看结果、直接指导角色，或继续下一步。")
        self._finish_control(state, run_id)

    def _remember(self, state, tree, cid, attempt_id, reason):
        row = state["chapters"][cid]
        saved = self.store.put(tree, BASE + "/Chapter-History/" + cid + "/" + attempt_id + ".json", {"chapter": dict(row), "reason": reason})
        row.setdefault("revision_history", []).append(saved)

    @staticmethod
    def _check_source(row, chapter):
        if any(row.get(k) != chapter.get(k) for k in ("draft", "canonical", "editor_plan", "review_result", "revision_plan_current")):
            raise CoreError("REVISION_CONFLICT", "当前章的来源已在其他窗口变化，模型结果保留，未覆盖新版本。")

    def advance(self, run_id, notify=None, evidence=None):
        try:
            return self._advance(run_id, notify, evidence)
        except Exception as error:
            row = self.get(run_id)
            if row["status"] in RUNNING:
                self.store.mutate("run_preflight_failed", lambda s, t: s["runs"][run_id].update(status="FAILED", error={
                    "code": getattr(error, "code", "TASK_FAILED"), "message": str(error) if isinstance(error, CoreError) else "步骤未完成，未自动重试。"}), control=True)
            raise

    def _advance(self, run_id, notify=None, evidence=None):
        self.core.require_idle()
        run = self.get(run_id)
        if run["status"] != "RUNNING":
            return run
        if run.get("workflow_version", 1) < 2:
            return self._block(run_id, "upgrade", "此为旧流程任务，请明确升级到逐章责编规划与最多两轮返修的新流程。")
        if self.core.workflow.planning.approved()["version_id"] != run["outline_version"]:
            return self._block(run_id, "outline", "正式大纲已变化，请停止旧任务后按新大纲开始。")
        if self.models() != run["models"]:
            return self._block(run_id, "models", "模型配置已变化，请明确选择后续模型再继续。")
        if self.core.workflow.scale.get() != run["scale"]:
            return self._block(run_id, "scale", "全书目标已变化，请明确采用新的后续目标。")
        if context_binding(shared_context(self.core, "创作资料版本检查")) != run["context_binding"]:
            return self._block(run_id, "context", "资料版本已变化，请明确采用新的后续资料。")
        if run["index"] >= len(run["chapter_ids"]):
            self.store.mutate("run_completed", lambda s, t: s["runs"][run_id].update(status="COMPLETED"))
            return self.get(run_id)
        cid = run["chapter_ids"][run["index"]]
        chapter = self.store.read()["chapters"][cid]
        if chapter.get("editor_plan"):
            plan = self.store.blob(chapter["editor_plan"])
            if plan["outline_version"] != run["outline_version"] or plan["context_binding"] != run["context_binding"]:
                def invalidate_plan(s, t):
                    row = s["chapters"][cid]
                    row.setdefault("plan_history", []).append(row["editor_plan"])
                    row.update(editor_plan=None, status="PLANNED")
                self.store.mutate("chapter_plan_needs_sync", invalidate_plan)
                return self.get(run_id)
        phase = self.phase(chapter)
        # A durable old response is replayed only for its original purpose.
        staged = run["attempts"].get(run.get("active_attempt"))
        if staged and staged["status"] == "RESULT_READY":
            phase = staged["phase"]
        if phase == "CHAPTER_LENGTH_CHECK":
            self.check_length(cid)
            return self.get(run_id)
        if phase == "REVIEW_DECISION":
            check = self.store.blob(chapter["review_result"])
            if not chapter.get("review_acknowledged"):
                deferred = run.get('creative_mode')=='continuous' and run.get('review_policy')=='continue_drafts' and (
                    check.get('author_flags',check['serious_flags']) or check.get('repair_flags') and chapter.get('repair_rounds',0)>=run.get('repair_limit',2))
                if deferred:
                    self.store.mutate('review_deferred',lambda s,t:s['chapters'][cid].update(review_decision_checked=True,review_deferred=True,review_pending_author=True))
                    return self.get(run_id)
                if check.get("author_flags", check["serious_flags"]):
                    return self._block(run_id, "review", "责编的重要判断尚不确定或引文无法核实，请查看证据并向责编说明作者决定；不会据此擅自改稿。", check.get("author_flags", check["serious_flags"]))
                if check.get("repair_flags"):
                    if chapter.get("repair_rounds", 0) >= run.get("repair_limit", 2):
                        return self._block(run_id, "review", "本章已完成两轮自动返修，仍有重要问题。草稿、每轮审稿和方案均已保留；可到本章与主笔/责编讨论，或明确保留现状后继续。", check["repair_flags"])
                    self.store.mutate("revision_needed", lambda s, t: s["chapters"][cid].update(repair_pending=True, revision_plan_current=None, status="REVISION_NEEDED"))
                    return self.get(run_id)
            self.store.mutate("review_decision_checked", lambda s, t: s["chapters"][cid].update(review_decision_checked=True))
            return self.get(run_id)
        if phase == "NEXT_CHAPTER_PLANNING":
            self.core.workflow.chapters.checked_document(chapter["draft"], "drafts")
            self.core.workflow.chapters.checked_document(chapter["review"], "reports")
            check = self.store.blob(chapter["review_result"])
            refs = self.core.workflow.guidance.ingest(check["value"]["future_notes"], chapter, chapter["review"])
            self.store.mutate("next_direction_saved", lambda s, t: s["chapters"][cid].update(next_direction=check["value"]["next_direction"], guidance_refs=refs, status="AUTHOR_APPROVAL_PENDING"))
            return self.get(run_id)
        if phase == "CHAPTER_DONE":
            def done(state, tree):
                row = state["runs"][run_id]
                if cid not in row["completed_chapters"]:
                    row["completed_chapters"].append(cid)
                row.update(index=row["index"] + 1, active_attempt=None)
                if row.get("execution_scope"):
                    self.core.workflow.execution.extend_run(state, row, cid)
                elif row["index"] >= len(row["chapter_ids"]):
                    row["status"] = "COMPLETED"
                self._finish_control(state, run_id)
            self.store.mutate("chapter_completed", done)
            return self.get(run_id)
        if phase == "CHAPTER_REVIEWING" and not chapter.get("review_snapshot"):
            snapshot = self.core.handoff(chapter["draft"]["document_id"])
            self.store.mutate("review_snapshot_created", lambda s, t: s["chapters"][cid].update(review_snapshot=snapshot))
            chapter = self.store.read()["chapters"][cid]
        current = self.current_context(run, chapter)
        attempt_id = run.get("active_attempt")
        existing = run["attempts"].get(attempt_id)
        if existing and existing["status"] == "IN_FLIGHT":
            raise CoreError("RESULT_UNKNOWN", "上次步骤尚无确定结论，请先恢复记录，不自动重发。")
        if not existing or existing["status"] != "RESULT_READY":
            attempt_id = uuid.uuid4().hex
            def claim(state, tree):
                row = state["runs"][run_id]
                if row["status"] != "RUNNING" or row.get("active_attempt") and row["attempts"][row["active_attempt"]]["status"] == "IN_FLIGHT":
                    raise CoreError("WORKFLOW_RUNNING", "该步骤已经被其他窗口处理。")
                if len(row["attempts"]) >= row.get("max_model_tasks", len(row["chapter_ids"]) * 9):
                    raise CoreError("TASK_BUDGET", "本次模型任务已达到授权上限。已保存成果；若需追加，请明确选择重新请求当前步骤，可能额外计费。")
                row["active_attempt"] = attempt_id
                row["attempts"][attempt_id] = {"status": "IN_FLIGHT", "phase": phase, "chapter_id": cid, "owner_pid": os.getpid(), "started": time.time()}
            self.store.mutate("step_claimed", claim)
        def progress(event):
            if event.get("type") == "role_lock":
                self.store.mutate("step_lock", lambda s, t: s["runs"][run_id]["attempts"][attempt_id].update(role_lock_sha256=event["fingerprint"]), control=True)
            if event.get("type") == "client_process":
                self.store.mutate("step_host", lambda s, t: s["runs"][run_id]["attempts"][attempt_id].update(host_pid=event["pid"]), control=True)
            if notify:
                notify(event)
        try:
            writing = phase in {"CHAPTER_WRITING", "CHAPTER_REVISING"}
            revising = phase == "CHAPTER_REVISING"
            task, schema = {"CHAPTER_PLANNING": ("workflow_plan", PLAN_SCHEMA), "REVISION_PLANNING": ("workflow_repair_plan", REPAIR_SCHEMA),
                "CHAPTER_REVISING": ("workflow_revise", WRITE_SCHEMA), "CHAPTER_WRITING": ("workflow_write", WRITE_SCHEMA),
                "CHAPTER_REVIEWING": ("workflow_review", REVIEW_SCHEMA)}[phase]
            instructions = (PLAN_INSTRUCTIONS if phase == "CHAPTER_PLANNING" else REPAIR_INSTRUCTIONS if phase == "REVISION_PLANNING" else
                "你是主笔。执行 revision_plan 中的具体修订，优先作者确认大纲及作者直接指令。保留方案指定内容，只修订 current_manuscript 当前草稿，返回完整新正文与摘要，不返回补丁。旧稿由软件保存，不修改其他章节、正式作品或正典。不为字数凑字或截断。" if revising else
                ("你是主笔，优先遵循作者确认的作品大纲，然后是当前执行计划，最后才是全局篇幅建议。只有宏观大纲也直接创作当前章，不必先规划全部章节。"
                 "必须依据 editor_execution_plan 的完整剧情、连续性和 word_range 创作，不能跳过责编计划；goal 已采用该范围。"
                 "交付本章完整正文、章节标题、连续性摘要和 execution 元数据：本卷建议名、本章目标、本卷是否叙事完成、整书是否叙事完成。"
                 "沿用作者已写的卷章名称；没有名称时依据本章内容拟名，不另发请求。volume_complete/book_complete 按叙事发展判断，不能为匹配默认字数/章数草率收尾，也不能跳过已规划的后续卷章。"
                 "不凑字、不截断；上章审稿改善本章，不修改历史章、正式大纲，不自动定稿。标题不得代替正文。" if writing else
                 "你是责编，独立审核当前稿件并给出四类内容：A 本章客观问题（准确行号/短引文/依据/确定程度）；B 本章质量建议；C 后续章节注意；D 下一章具体剧情方向。"
                 "检查已确认大纲、editor_execution_plan、世界观、人物知情、因果时间线、语言和节奏。核对实际 actual_word_count 与计划范围，但差异本身不构成否决。审美偏好不冒充客观错误，未回收伏笔不必判错；重要问题标 medium/high，需引用当前稿件证据。可修复的有依据问题会触发草稿返修；不确定的重大决策才交作者。"
                 "未来注意精简、可检索、标适用范围，不修改本章或正典。下一章方向含事件、人物、关系、主支线、伏笔、约束、情绪节奏、建议字数和避免事项。篇幅只可作为叙事质量建议，不得因偏离目标否决。"))
            result = execute(self.core, task, current, schema, instructions, attempt_id, progress, evidence)
            self.store.mutate("step_result_ready", lambda s, t: s["runs"][run_id]["attempts"][attempt_id].update(status="RESULT_READY"))
            output = result["output"]
            if phase == "CHAPTER_PLANNING":
                value = validate_plan(output, chapter)
                def apply_plan(state, tree):
                    row = state["chapters"][cid]
                    self._check_source(row, chapter)
                    if row.get("draft"):
                        self._remember(state, tree, cid, attempt_id, "为已有草稿补齐责编计划前")
                    ref = self.store.put(tree, BASE + "/Chapter-Plans/" + attempt_id + ".json", {"value": value,
                        "outline_version": run["outline_version"], "context_binding": run["context_binding"], "model": result["model"], "chapter_id": cid})
                    row.update(editor_plan=ref, status="DRAFT_SAVED" if row.get("draft") else "PLAN_READY",
                        review=None, review_result=None, next_direction=None, review_snapshot=None, review_decision_checked=False,
                        length_checked=False, local_length=None)
                    self._finish_step(state, tree, run_id, attempt_id)
                self.store.mutate("chapter_planned", apply_plan)
            elif phase == "REVISION_PLANNING":
                value = validate_repair(output)
                def apply_repair_plan(state, tree):
                    row = state["chapters"][cid]
                    self._check_source(row, chapter)
                    ref = self.store.put(tree, BASE + "/Revision-Plans/" + attempt_id + ".json", {"value": value,
                        "source_draft": chapter["draft"], "source_review": chapter["review_result"], "outline_version": run["outline_version"], "model": result["model"]})
                    row.update(revision_plan_current=ref, status="REVISION_READY")
                    row.setdefault("revision_plans", []).append(ref)
                    self._finish_step(state, tree, run_id, attempt_id)
                    if value["requires_author_decision"]:
                        state["runs"][run_id].update(status="BLOCKED", block={"kind": "review", "message": value["author_question"], "detail": value})
                self.store.mutate("revision_plan_saved", apply_repair_plan)
            elif writing:
                if not isinstance(output, dict) or set(output) - set(WRITE_SCHEMA["properties"]) or not {"text", "continuity_summary"} <= set(output):
                    raise CoreError("INVALID_ARTIFACT", "主笔未返回完整章节结构，结果保留，不自动重试。")
                delivered_title = output.get("title") if output.get("title") is not None else ""
                checked_text(delivered_title, 180)
                checked_text(output["text"])
                checked_text(output["continuity_summary"], 6000)
                metadata = output.get("execution", chapter.get("execution_metadata", {}) if revising else {})
                if "execution" in output:
                    if not isinstance(metadata, dict) or set(metadata) - set(EXECUTION_SCHEMA["properties"]):
                        raise CoreError("INVALID_ARTIFACT", "主笔的执行计划元数据无效，响应保留，不自动重试。")
                    metadata = {**{key: "" for key in ("volume_title", "chapter_goal")}, "volume_complete": False, "book_complete": False, **metadata}
                    if any(type(metadata[k]) is not bool for k in ("volume_complete", "book_complete")):
                        raise CoreError("INVALID_ARTIFACT", "主笔的执行进度字段无效，响应保留，不自动重试。")
                    for key in ("volume_title", "chapter_goal"):
                        if metadata[key] is None:
                            metadata[key] = ""
                    checked_text(metadata["volume_title"], 180)
                    checked_text(metadata["chapter_goal"], 6000)
                title = delivered_title.strip()
                if not title:
                    headings = re.findall(r"^#{1,6}\s+(.+)$", output["text"], re.M)
                    title = (headings[0] if headings else next((line.strip() for line in output["text"].splitlines() if line.strip()), ""))[:80]
                    title = re.sub(r"^第[0-9零一二两三四五六七八九十百千]+章\s*[：:、.．—-]?\s*", "", title) or f"第{chapter['number']}章"
                if measure(output["text"])["count"] == 0:
                    raise CoreError("INVALID_ARTIFACT", "主笔没有交付有效正文，未创建空章节。")
                artifact = self.core._artifact("writer", "drafts", output["text"], "workflow-" + attempt_id)
                def apply_write(state, tree):
                    row = state["chapters"][cid]
                    self._check_source(row, chapter)
                    if revising:
                        self._remember(state, tree, cid, attempt_id, "自动返修前完整章节版本")
                        row.update(repair_rounds=row.get("repair_rounds", 0) + 1, repair_pending=False, revision_plan_current=None,
                            review=None, review_result=None, review_snapshot=None, next_direction=None, length_checked=False,
                            review_decision_checked=False, review_acknowledged=False)
                    self.core.workflow.execution.apply_metadata(state, cid, title, metadata, output["continuity_summary"])
                    state["chapters"][cid].update(draft=artifact, status="DRAFT_SAVED",
                        continuity_summary=output["continuity_summary"], writer_model=result["model"])
                    self._finish_step(state, tree, run_id, attempt_id, artifact)
                self.store.mutate("chapter_revised" if revising else "chapter_written", apply_write)
                self.core.workflow.scale.sync()
            else:
                draft = self.core.workflow.chapters.checked_document(chapter["draft"], "drafts")
                check = validate_review(output, draft["text"])
                text = report_text(check)
                artifact = self.core._artifact("editor", "reports", text, "workflow-" + attempt_id)
                def apply_review(state, tree):
                    self._check_source(state["chapters"][cid], chapter)
                    ref = self.store.put(tree, BASE + "/Reviews/" + attempt_id + ".json", check)
                    state["chapters"][cid].update(review=artifact, review_result=ref, editor_model=result["model"], status="REVIEW_COMPLETED")
                    state["chapters"][cid].setdefault("review_history", []).append({"report": artifact, "result": ref, "source_draft": chapter["draft"], "round": chapter.get("repair_rounds", 0)})
                    self._finish_step(state, tree, run_id, attempt_id, artifact)
                self.store.mutate("chapter_reviewed", apply_review)
            return self.get(run_id)
        except Exception as error:
            def failed(state, tree):
                row = state["runs"][run_id]
                if row["attempts"][attempt_id]["status"] != "COMPLETED":
                    row["attempts"][attempt_id].update(status="FAILED", error_code=getattr(error, "code", "TASK_FAILED"))
                row.update(status="FAILED", error={"code": getattr(error, "code", "TASK_FAILED"), "message": str(error) if isinstance(error, CoreError) else "步骤未完成，已保存记录；请检查后明确重试。"})
                self._finish_control(state, run_id)
            self.store.mutate("step_failed", failed, control=True)
            raise
