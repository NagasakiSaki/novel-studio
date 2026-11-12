"""Versioned editor planning; only an exact author approval reaches Canonical."""
from __future__ import annotations

import difflib
import json
import os
import re
import secrets
import time
import uuid
from .core import CoreError, SafeTree, admin, checked_text, digest, TEXT_BUDGET_BYTES
from .workflow_store import BASE, WorkflowStore, identifier, alive
from .workflow_requests import context_sources, execute, shared_context, load_result
from .material_formats import heading_index

OUTLINE_TARGET = "Canonical/Canon-Worldbuilding/作品大纲.md"
BOOK_TEXT = ("concept", "theme", "synopsis", "characters", "conflicts", "direction", "ending")
VOLUME_TEXT = ("title", "summary", "milestones", "relationships", "foreshadowing")
CHAPTER_TEXT = ("title", "summary", "events", "characters", "relationships", "foreshadowing", "constraints")


def object_schema(properties):
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}


def text_fields(names):
    return {key: {"type": "string"} for key in names}


CHAPTER_SCHEMA = object_schema({"number": {"type": "integer"}, **text_fields(CHAPTER_TEXT)})
VOLUME_SCHEMA = object_schema({"number": {"type": "integer"}, **text_fields(VOLUME_TEXT), "chapters": {"type": "array", "items": CHAPTER_SCHEMA}})
OUTLINE_SCHEMA = object_schema({**text_fields(BOOK_TEXT), "volumes": {"type": "array", "items": VOLUME_SCHEMA}})


def outline_from_text(text):
    """Reuse the audited Markdown heading parser; preserve raw text in save()."""
    checked_text(text, TEXT_BUDGET_BYTES)
    value = {key: "" for key in BOOK_TEXT}
    value.update(concept=text, volumes=[])
    volume = None
    for section in heading_index(text):
        match = re.match(r"^第?[0-9零一二两三四五六七八九十百千]+([卷章])(?:\s*[：:、.．—-]?\s*)(.*)$", section["title"])
        if not match:
            continue
        if match[1] == "卷":
            volume = {"number": len(value["volumes"]) + 1, "title": match[2], "chapters": []}
            value["volumes"].append(volume)
        else:
            if volume is None:
                volume = {"number": 1, "chapters": []}
                value["volumes"].append(volume)
            volume["chapters"].append({"number": len(volume["chapters"]) + 1, "title": match[2]})
    return value


def validate_outline(value):
    if isinstance(value, str):
        value = outline_from_text(value)
    value = json.loads(json.dumps(value, ensure_ascii=False))
    def check(row, names, limit):
        if not isinstance(row, dict) or set(row) - set(names):
            raise CoreError("OUTLINE_INVALID", "大纲结构或字段不受支持，请查看保留的结果。")
        for key in names:
            if key not in {"number", "volumes", "chapters"}:
                row.setdefault(key, "")
                if row[key] is None:
                    row[key] = ""
                checked_text(row[key], limit if key == 'title' else TEXT_BUDGET_BYTES)
        for key in {"volumes", "chapters"} & set(names):
            row.setdefault(key, [])
        if "number" in names and (type(row["number"]) is not int or not 1 <= row["number"] <= 1000):
            raise CoreError("OUTLINE_INVALID", "卷章编号应为正整数。")
    check(value, (*BOOK_TEXT, "volumes"), 30_000)
    if not isinstance(value["volumes"], list) or len(value["volumes"]) > 100:
        raise CoreError("OUTLINE_INVALID", "分卷结构无效或超过当前 100 卷安全上限。")
    total = 0
    for vi, volume in enumerate(value["volumes"], 1):
        if isinstance(volume, dict):
            volume.setdefault("number", vi)
            if volume["number"] is None or type(volume["number"]) is int and volume["number"] == 0:
                volume["number"] = vi
        check(volume, ("number", *VOLUME_TEXT, "chapters"), 10_000)
        if not isinstance(volume["chapters"], list) or len(volume["chapters"]) > 200:
            raise CoreError("OUTLINE_INVALID", "每卷最多规划 200 章，可分卷补充章节细节。")
        for ci, chapter in enumerate(volume["chapters"], 1):
            if isinstance(chapter, dict):
                chapter.setdefault("number", ci)
                if chapter["number"] is None or type(chapter["number"]) is int and chapter["number"] == 0:
                    chapter["number"] = ci
            check(chapter, ("number", *CHAPTER_TEXT), 6000)
        numbers = [row["number"] for row in volume["chapters"]]
        total += len(numbers)
    if total > 1000:
        raise CoreError("OUTLINE_INVALID", "全书超过当前 1000 章目录安全上限。")
    if not any(value[key].strip() for key in BOOK_TEXT) and not any(v["summary"].strip() or v["title"].strip() or v["chapters"] for v in value["volumes"]):
        raise CoreError("OUTLINE_INVALID", "大纲没有可用的故事内容，不能开始空白作品。")
    checked_text(admin.encode(value).decode("utf-8"), TEXT_BUDGET_BYTES)
    return json.loads(json.dumps(value, ensure_ascii=False))


def render_outline(value):
    labels = {"concept": "核心概念", "theme": "故事主题", "synopsis": "故事梗概", "characters": "人物与关系", "conflicts": "主要冲突", "direction": "发展方向", "ending": "结局规划"}
    parts = ["# 作品大纲\n"]
    for key in BOOK_TEXT:
        parts.append("## " + labels[key] + "\n\n" + value[key] + "\n")
    for volume in sorted(value["volumes"], key=lambda row: row["number"]):
        parts.append(f"## 第{volume['number']}卷 {volume['title']}\n\n{volume['summary']}\n\n重要节点：{volume['milestones']}\n人物与关系变化：{volume['relationships']}\n伏笔与回收：{volume['foreshadowing']}\n")
        for chapter in sorted(volume["chapters"], key=lambda row: row["number"]):
            parts.append(f"### 第{chapter['number']}章 {chapter['title']}\n\n{chapter['summary']}\n主要事件：{chapter['events']}\n重点人物：{chapter['characters']}\n关系变化：{chapter['relationships']}\n伏笔：{chapter['foreshadowing']}\n约束：{chapter['constraints']}\n")
    return "\n".join(parts)


def merge_scope(before, change, scope):
    kind = scope.get("kind")
    if kind == "book":
        return validate_outline(change)
    if before is None:
        raise CoreError("OUTLINE_REQUIRED", "请先生成整体大纲，再修改指定卷章。")
    value = json.loads(json.dumps(before))
    volume = next((row for row in value["volumes"] if row["number"] == scope.get("volume")), None)
    if volume is None:
        raise CoreError("OUTLINE_INVALID", "所选分卷不存在。")
    if kind == "volume" and change.get("number") == volume["number"]:
        value["volumes"][value["volumes"].index(volume)] = change
    elif kind == "chapter":
        chapter = next((row for row in volume["chapters"] if row["number"] == scope.get("chapter")), None)
        if chapter is None or change.get("number") != chapter["number"]:
            raise CoreError("OUTLINE_INVALID", "所选章节不存在或返回编号不同。")
        volume["chapters"][volume["chapters"].index(chapter)] = change
    else:
        raise CoreError("OUTLINE_INVALID", "请选择整书、指定卷或指定章节，修改不能改变编号。")
    return validate_outline(value)


class PlanningService:
    def __init__(self, core, store=None):
        self.core, self.store = core, store or WorkflowStore(core)

    def versions(self):
        state = self.store.read()
        return [{"version_id": key, **state["outlines"][key]} for key in reversed(state["outline_order"])]

    def get(self, version_id=None):
        state = self.store.read()
        version_id = version_id or (state["outline_order"][-1] if state["outline_order"] else None)
        identifier(version_id)
        ref = state["outlines"].get(version_id)
        if ref is None:
            raise CoreError("OUTLINE_REQUIRED", "所选大纲版本不存在。")
        record = self.store.blob(ref)
        record["value"] = validate_outline(record["value"])
        return record

    def save(self, value, *, base_version=None, instruction="作者保存大纲草稿", model=None, version_id=None, scope=None):
        raw_text = value if isinstance(value, str) else None
        value = validate_outline(value)
        version_id = identifier(version_id) if version_id else uuid.uuid4().hex
        if version_id in self.store.read()["outlines"]:
            previous = self.get(version_id)
            if previous["value"] != value:
                raise CoreError("IDEMPOTENCY_CONFLICT", "同一大纲版本不能用于不同内容。")
            return previous
        text = raw_text if raw_text is not None else render_outline(value)
        row = self.core._artifact("editor", "reports", text, "outline-" + version_id)
        record = {"version_id": version_id, "base_version": base_version, "scope": scope or {"kind": "book"},
            "instruction": checked_text(instruction, 20_000), "model": model or {"provider": "author"}, "value": value,
            "text": text, "artifact": row, "time": time.time()}
        if raw_text is not None:
            record["source_format"] = "text"
        def operation(state, tree):
            reference = self.store.put(tree, BASE + "/Outlines/" + version_id + ".json", record)
            if version_id not in state["outlines"]:
                state["outlines"][version_id] = {**reference, "number": len(state["outline_order"]) + 1,
                    "base_version": base_version, "time": record["time"], "scope": record["scope"]}
                state["outline_order"].append(version_id)
            if version_id in state["outline_jobs"]:
                state["outline_jobs"][version_id].update(status="COMPLETED", version_id=version_id)
            return record
        return self.store.mutate("outline_saved", operation)

    def generation_inputs(self, instruction, base_version, scope):
        checked_text(instruction, 20_000)
        scope = scope or {"kind": "book"}
        kind = scope.get("kind")
        if kind not in {"book", "volume", "chapter"}:
            raise CoreError("OUTLINE_INVALID", "请选择明确的大纲修改范围。")
        state = self.store.read()
        base_version = base_version or (state["outline_order"][-1] if state["outline_order"] else None)
        base = self.get(base_version) if base_version else None
        if kind != "book" and base is None:
            raise CoreError("OUTLINE_REQUIRED", "请先生成整体大纲。")
        # Planning starts before there is a chapter query. Even an empty idea
        # needs the preset's factual background, not only its always-on rules.
        planning_query = instruction + "\n人物 世界观 故事背景 时间线 伏笔 创作范围 设定 约束"
        current = {**shared_context(self.core, planning_query, allow_empty=bool(instruction.strip())),
            "scale": state["scale"], "author_idea_or_revision": instruction,
            "scope": scope, "previous_outline": base["value"] if base else None,
            "planning_note": "作者确认大纲优先于执行计划、篇幅建议和默认值。构思留空时依据已批准资料自主构思。只返回指定范围。允许纯宏观大纲、空分卷或空章节列表；不必提前拟好全部标题/细纲，后续在创作中形成，不要求作者补数据库字段。"}
        return base, current

    def generation_binding(self, base, current):
        selected = self.core.models.selected("editor")
        model = {
            key: selected.get(key) for key in ("provider", "model_id", "effort", "max_output_tokens", "temperature", "cache")
        }
        binding = digest(admin.encode({"input": current, "base_version": base["version_id"] if base else None, "model": model}))
        return binding, model

    def preview_generation(self, instruction):
        base, current = self.generation_inputs(instruction, None, None)
        binding, model = self.generation_binding(base, current)
        return binding, context_sources(current), model

    def generate(self, instruction="", *, base_version=None, scope=None, notify=None, evidence=None, preview_binding=None):
        base, current = self.generation_inputs(instruction, base_version, scope)
        scope = current["scope"]
        kind = scope["kind"]
        base_version = base["version_id"] if base else None
        if preview_binding is not None and preview_binding != self.generation_binding(base, current)[0]:
            raise CoreError("REVISION_CONFLICT", "模型、大纲或资料已变化，请重新查看发送范围。")
        attempt = uuid.uuid4().hex
        def claim(s, tree):
            s["outline_jobs"][attempt] = {"status": "IN_FLIGHT", "owner_pid": os.getpid(), "base_version": base_version,
                "scope": scope, "instruction": instruction, "time": time.time()}
        self.store.mutate("outline_started", claim)
        def progress(event):
            if event.get("type") == "role_lock":
                self.store.mutate("task_lock", lambda s, t: s["outline_jobs"][attempt].update(role_lock_sha256=event["fingerprint"]), control=True)
            if event.get("type") == "client_process":
                self.store.mutate("task_host", lambda s, t: s["outline_jobs"][attempt].update(host_pid=event["pid"]), control=True)
            if notify:
                notify(event)
        try:
            schema = {"book": OUTLINE_SCHEMA, "volume": VOLUME_SCHEMA, "chapter": CHAPTER_SCHEMA}[kind]
            result = execute(self.core, "outline_plan", current, schema,
                "你是责编，负责待作者确认的故事规划。篇幅与卷章数量仅为参考，故事结构优先。给出核心概念、人物、冲突、必要的分卷、关系变化、伏笔和结局；允许没有章节细纲/名称。"
                "作者没有构思也要基于预设自主设计；不擅自改变已批准设定。局部修订只交付指定范围，保持编号。"
                "不得写正式作品，不修改其他正文；文学审美与未知设定交给作者裁决。", attempt, progress, evidence)
            value = merge_scope(base["value"] if base else None, result["output"], scope)
            return self.save(value, base_version=base_version, instruction=instruction, model=result["model"], version_id=attempt, scope=scope)
        except Exception as error:
            self.store.mutate("outline_failed", lambda s, t: s["outline_jobs"][attempt].update(status="FAILED", error_code=getattr(error, "code", "TASK_FAILED")), control=True)
            raise

    def restore(self, version_id):
        old = self.get(version_id)
        versions = self.versions()
        return self.save(old["text"] if old.get("source_format") == "text" else old["value"], base_version=versions[0]["version_id"] if versions else None,
                         instruction="作者恢复历史大纲，原版本 " + version_id)

    def recover(self, attempt):
        identifier(attempt)
        self.core.require_idle()
        state = self.store.read()
        if attempt in state["outlines"]:
            return self.get(attempt)
        job = state["outline_jobs"].get(attempt)
        if job is None:
            raise CoreError("OUTLINE_REQUIRED", "没有可恢复的大纲任务。")
        result = load_result(self.core, attempt, "outline_plan")
        if result is None:
            raise CoreError("RESULT_UNKNOWN", "上次请求没有保存完整结果，不能自动重发。请查看状态；手动重新生成可能再次计费。")
        before = self.get(job["base_version"])["value"] if job["base_version"] else None
        value = merge_scope(before, result["output"], job["scope"])
        return self.save(value, base_version=job["base_version"], instruction=job["instruction"], model=result["model"], version_id=attempt, scope=job["scope"])

    def diff(self, before, after):
        a, b = self.get(before), self.get(after)
        return "".join(difflib.unified_diff(a["text"].splitlines(keepends=True), b["text"].splitlines(keepends=True), fromfile="旧大纲", tofile="新大纲"))

    def coverage(self, value):
        value = validate_outline(value)
        warnings = []
        volumes = sorted(value["volumes"], key=lambda row: row["number"])
        if [v["number"] for v in volumes] != list(range(1, len(volumes) + 1)):
            warnings.append("卷章编号将由独立执行计划连续分配，不修改大纲原文。")
        for volume in volumes:
            numbers = sorted(c["number"] for c in volume["chapters"])
            if not numbers or numbers != list(range(1, len(numbers) + 1)):
                warnings.append("部分卷章信息尚未填写，软件会在创作过程中自动规划和补全。实际卷章结构与篇幅以已确认大纲为准。")
            if any(not c["summary"].strip() or not c["events"].strip() for c in volume["chapters"]):
                warnings.append("部分章节只有宏观方向，主笔会在本章创作结果中补全执行摘要。")
        scale = self.store.read()["scale"]
        count = sum(len(v["chapters"]) for v in volumes)
        if scale.get("total_chapters") and count != scale["total_chapters"]:
            warnings.append("当前作品规划与默认篇幅设置存在差异，已优先采用你确认的大纲，不需要手动修改设置。")
        if scale.get("total_volumes") and len(volumes) != scale["total_volumes"]:
            warnings.append("当前作品规划与默认篇幅设置存在差异，已优先采用你确认的大纲，不需要手动修改设置。")
        if not volumes:
            warnings.append("已读取宏观大纲，卷章执行计划会在创作过程中逐步形成。")
        return list(dict.fromkeys(warnings))

    def preview(self, version_id):
        self.core.require_idle()
        record = self.get(version_id)
        warnings = self.coverage(record["value"])
        source = self.core.document(record["artifact"]["document_id"], "editor")
        if source["revision"] != record["artifact"]["revision"]:
            raise CoreError("WORKFLOW_CHANGED", "大纲提案原件已变化，不能按旧版本批准。")
        with SafeTree(self.core.project) as tree:
            plan = admin._plan(tree, "outline_approval", source["relative"], OUTLINE_TARGET, record["text"].encode("utf-8"))[0]
        handle = secrets.token_urlsafe(32)
        self.core._approvals[handle] = (time.monotonic() + 300, plan, ("workflow_outline", version_id))
        display_diff = plan["diff"] or "正文与现有正式大纲相同。本次仅确认此规划版本，不重复写正文。\n\n" + record["text"]
        return {"handle": handle, "target": "作品资料中的正式大纲", "diff": display_diff, "operation": "outline_approval", "version_id": version_id, "warnings": warnings}

    def commit_approved(self, plan, version_id):
        record = self.get(version_id)
        self.core.require_idle()
        with SafeTree(self.core.project) as tree:
            with tree.lock():
                source = self.core.document(record["artifact"]["document_id"], "editor")
                if source["revision"] != record["artifact"]["revision"]:
                    raise CoreError("REVISION_CONFLICT", "大纲提案变化，请重新预览。")
                bundle = admin._plan(tree, "outline_approval", source["relative"], OUTLINE_TARGET, record["text"].encode("utf-8"))
                if bundle[0]["approval_digest"] != plan["approval_digest"]:
                    raise CoreError("REVISION_CONFLICT", "大纲来源或目标变化，请重新预览。")
                result = (admin._commit(tree, bundle, plan["approval_digest"]) if bundle[1] != bundle[2] else
                          {"transaction_id": None, "target": OUTLINE_TARGET})
        self.store.mutate("outline_approved", lambda s, t: s.update(approved_outline={"version_id": version_id,
            "canonical_sha256": digest(record["text"].encode("utf-8")), "transaction_id": result["transaction_id"]}))
        if result["transaction_id"]:
            self.core._state(lambda s: s.update(version_order=(s.get("version_order", []) + [result["transaction_id"]])[-1000:]))
        self.core.snapshot()
        return {"transaction_id": result["transaction_id"], "target": OUTLINE_TARGET}

    def approved(self):
        ref = self.store.read()["approved_outline"]
        if ref is None:
            raise CoreError("OUTLINE_APPROVAL_REQUIRED", "请先由作者确认正式大纲，再开始章节创作。")
        record = self.get(ref["version_id"])
        with SafeTree(self.core.project) as tree:
            if digest(tree.read(OUTLINE_TARGET)) != ref["canonical_sha256"]:
                raise CoreError("WORKFLOW_CHANGED", "正式大纲与确认记录不同，请作者检查或重新确认。")
        return record

    def context(self, source_document_id=None):
        state = self.store.read()
        if state["approved_outline"] is None:
            return {}
        record = self.approved()
        current = None
        for row in state["chapters"].values():
            if source_document_id and any((row.get(key) or {}).get("document_id") == source_document_id for key in ("draft", "canonical")):
                current = row
                break
        selected = None
        if current:
            for volume in record["value"]["volumes"]:
                if volume["number"] == current["volume"]:
                    selected = next((c for c in volume["chapters"] if c["number"] == current["number"]), None)
        return {"outline_version": record["version_id"], "status": "author_approved",
            "overview": {k: record["value"][k] for k in BOOK_TEXT},
            "volumes": [{k: v[k] for k in ("number", "title", "summary")} for v in record["value"]["volumes"]],
            "selected_chapter": selected, "note": "只引用已确认版本；未确认的新方案不替换它。"}
