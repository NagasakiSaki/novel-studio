"""Fixed-purpose model tasks with durable input/result receipts; no auto retry."""
from __future__ import annotations

import json
from pathlib import Path
from .core import CoreError, SafeTree, admin, checked_text, digest
from .workflow_store import BASE, WorkflowStore, identifier
from .diagnostics import record, scope
from .prompting import MAX_PROMPT_BYTES, context_size_error


def shared_context(core, query, *, allow_empty=False):
    core.require_idle()
    if not admin.ui_state(core.project).get("context"):
        core.snapshot()
    view = core._context_view('writer')
    material, receipt = core.materials.context("writer", query, split=True, _view=view)
    documents, size = [], 0
    meta = view[4]
    for name in sorted(meta["files"]):
        if not name.startswith(("Canonical/Canon-Characters/", "Canonical/Canon-Worldbuilding/", "Canonical/Approved-Timeline/")) or "/Materials/" in name:
            continue
        if name == "Canonical/Canon-Worldbuilding/作品大纲.md":
            continue
        text = view[5][name].decode("utf-8-sig")
        size += len(text.encode("utf-8"))
        if size > MAX_PROMPT_BYTES:
            raise context_size_error(size, 'shared_context')
        documents.append({"name": name, "revision": meta["files"][name], "text": text})
    view = None
    if not documents and not receipt and not allow_empty and not core.workflow.store.read()["approved_outline"]:
        raise CoreError("MATERIAL_REQUIRED", "请先输入故事构思，或导入并批准世界观、预设或人物资料。")
    return {"approved_always_rules": material["stable"], "retrieved_references": material["dynamic"],
        "approved_context": documents, "material_receipt": receipt,
        "context_note": "已批准资料副本；当前任务直接提供的内容，不代表模型已经理解。"}


def context_sources(context):
    return {
        "documents": [row["name"] for row in context["approved_context"]],
        "materials": [{key: row[key] for key in ("name", "version", "context_mode", "sections")}
                      for row in context["material_receipt"]],
    }


def load_result(core, attempt_id, task):
    identifier(attempt_id)
    store = WorkflowStore(core)
    state = store.read()
    path = BASE + "/Attempts/" + attempt_id + "/result.json"
    with SafeTree(core.project) as tree:
        raw = tree.maybe_read(path)
    if raw is None:
        return None
    source = state.get("task_inputs", {}).get(attempt_id)
    if source is None:
        raise CoreError("WORKFLOW_CHANGED", "已保存结果缺少原始请求依据，未自动采用。")
    store.blob(source)
    envelope = json.loads(raw)
    if envelope.get("attempt_id") != attempt_id or envelope.get("task") != task or envelope.get("request_sha256") != source["sha256"]:
        raise CoreError("WORKFLOW_CHANGED", "结果的用途或请求版本不同，未重放。")
    ref = state.get("task_results", {}).get(attempt_id)
    if ref is not None and digest(raw) != ref["sha256"]:
        raise CoreError("WORKFLOW_CHANGED", "已保存的模型响应发生变化，未重放。")
    if ref is None:
        # Crash after durable response creation but before the state pointer.
        store.mutate("task_response_recovered", lambda s, t: s.setdefault("task_results", {}).update({attempt_id: {"path": path, "sha256": digest(raw)}}))
    return envelope["result"]


def execute(core, task, current, schema, instructions, attempt_id, notify=None, evidence=None):
    """Caller durably claims a step before entering this function."""
    identifier(attempt_id)
    store = WorkflowStore(core)
    role = "writer" if task in {"workflow_write", "workflow_revise", "author_revise"} else "editor"
    selected = core.models.selected(role)
    safe_model = {key: selected.get(key) for key in ("provider", "model_id", "effort", "max_output_tokens", "temperature")}
    folder = BASE + "/Attempts/" + attempt_id
    previous = load_result(core, attempt_id, task)
    if previous is not None:
        return previous
    request = {"attempt_id": attempt_id, "task": task, "role": role, "model": safe_model,
        "instructions": instructions, "current": current, "schema": schema}
    def save_input(state, tree):
        ref = store.put(tree, folder + "/request.json", request)
        state.setdefault("task_inputs", {})[attempt_id] = ref
        return ref
    input_ref = store.mutate("task_input", save_input)
    profile, _ = core.creative.context(role)
    record(core, 'model_start', task=task, role=role, provider=safe_model['provider'], model_id=safe_model['model_id'], attempt_id=attempt_id)
    try:
        with scope(task=task, role=role, attempt_id=attempt_id):
            result = core.models.structured_task(task, instructions, current, schema, profile=profile, notify=notify, evidence=evidence)
    except Exception as error:
        log = getattr(core, 'diagnostics', None)
        if log:
            log.error(error, 'host_error', task=task, role=role, attempt_id=attempt_id)
        raise
    record(core, 'model_finish', task=task, role=role, status='RESPONSE_RECEIVED', attempt_id=attempt_id, actual_model=result.get('model', {}).get('actual_model'))
    checked_text(admin.encode(result).decode("utf-8"), MAX_PROMPT_BYTES)
    def save_result(state, tree):
        ref = store.put(tree, folder + "/result.json", {"attempt_id": attempt_id, "task": task, "request_sha256": input_ref["sha256"], "result": result})
        state.setdefault("task_results", {})[attempt_id] = ref
    store.mutate("task_result", save_result)
    actual = result.get("model", {})
    if actual.get("provider") != safe_model["provider"] or actual.get("requested_model") != safe_model["model_id"]:
        raise CoreError("MODEL_CHANGED", "请求前后选择的模型不同。结果已保留，请作者检查，不自动重发。")
    return result
