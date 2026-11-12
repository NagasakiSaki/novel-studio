"""Local domain checkpoints; never deserialize executable objects or grant roles."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import time
import uuid
from .core import CoreError, SafeTree, admin, digest
from .version_info import WORKFLOW_FORMAT, CURRENT_SCHEMA, READABLE_SCHEMAS

BASE = ".novel-studio/Workflow"
FORMAT = WORKFLOW_FORMAT
DEFAULT_SCALE = {"chapter_target": 3000, "chapter_minimum": 2400, "chapter_maximum": 3600,
    "chapters_per_volume": 20, "total_words": None, "total_chapters": 20, "total_volumes": 1}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise CoreError("WORKFLOW_INVALID", "规划或任务编号无效。")
    return value


def alive(pid):
    """False only when the OS can establish that this PID has exited."""
    if type(pid) is not int or pid <= 0:
        return None
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False if ctypes.get_last_error() == 87 else None
        try:
            status = kernel.WaitForSingleObject(handle, 0)
            return False if status == 0 else True if status == 258 else None
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None


class WorkflowStore:
    def __init__(self, core):
        self.core = core

    def initial(self):
        return {"format": FORMAT, "product_schema": CURRENT_SCHEMA, "project_id": self.core.project_id, "sequence": 0,
            "scale": dict(DEFAULT_SCALE), "outlines": {}, "outline_order": [], "approved_outline": None,
            "outline_jobs": {}, "chapters": {}, "runs": {}, "active_run": None,
            "guidance": {}, "batches": {}, "task_inputs": {}, "task_results": {}, "events": [], "execution_plans": {}}

    def decode(self, raw):
        state = json.loads(raw) if raw else self.initial()
        if not isinstance(state, dict) or state.get("format") != FORMAT or state.get("project_id") != self.core.project_id:
            raise CoreError("WORKFLOW_INVALID", "创作流程记录不属于本作品或格式不受支持，已停止。")
        schema = state.get("product_schema", 1)
        if type(schema) is not int or schema not in READABLE_SCHEMAS:
            raise CoreError("WORKFLOW_VERSION_UNSUPPORTED", "本作品使用了当前软件不支持的创作记录版本。请使用兼容的新版本打开；已有文件保持原样，不会强行降级。")
        if any(not isinstance(state.get(k), dict) for k in ("scale", "outlines", "outline_jobs", "chapters", "runs", "guidance", "batches")):
            raise CoreError("WORKFLOW_INVALID", "创作流程记录不完整，已停止。")
        if not isinstance(state.setdefault("execution_plans", {}), dict):
            raise CoreError("WORKFLOW_INVALID", "章节执行计划记录损坏，已停止。")
        return state

    def read(self):
        # UI status reads and worker publication share one Core. Serialize them
        # without loosening Windows handle-sharing or secure-read checks.
        with self.core.mutex:
            with SafeTree(self.core.project) as tree:
                return self.decode(tree.maybe_read(BASE + "/state.json"))

    def mutate(self, label, operation, *, control=False):
        with self.core.mutex:
            if not control:
                self.core.require_idle()
            with SafeTree(self.core.project) as tree:
                with tree.lock():
                    old = tree.maybe_read(BASE + "/state.json")
                    state = self.decode(old)
                    tree.mkdir(BASE)
                    result = operation(state, tree)
                    self._publish_locked(tree, old, state, label)
                    return result

    def _publish_locked(self, tree, old, state, label):
        """Internal: caller owns Core.mutex and this tree's project lock.

        Allows approved Canonical recovery to use one lock without reentering
        public mutation wrappers. Existing schema/publication behavior is kept.
        """
        state['sequence'] += 1
        event = {'sequence': state['sequence'], 'operation': label, 'time': time.time()}
        state['events'] = (state['events'] + [event])[-200:]
        tree.mkdir(BASE + '/Events')
        tree.create(BASE + '/Events/' + uuid.uuid4().hex + '.json', admin.encode(event))
        if old and (state.get('product_schema', 1) < 2 or label.startswith(('outline_', 'scale_', 'accept_', 'execution_', 'author_'))):
            tree.mkdir(BASE + '/History')
            name = BASE + '/History/' + digest(old) + '.json'
            if tree.maybe_read(name) is None:
                tree.create(name, old)
        tree.publish(BASE + '/state.json', admin.encode(state), digest(old) if old else None)
        from .diagnostics import record
        active = state.get('runs', {}).get(state.get('active_run'), {})
        record(self.core, 'workflow_step', action=label, status=active.get('status'), run_id=state.get('active_run'))

    @staticmethod
    def put(tree, relative, value):
        if not relative.startswith(BASE + "/"):
            raise CoreError("WORKFLOW_INVALID", "流程记录路径不正确。")
        payload = admin.encode(value)
        previous = tree.maybe_read(relative)
        if previous is not None and previous != payload:
            raise CoreError("REVISION_CONFLICT", "已保存的任务结果不同，未覆盖历史。")
        if previous is None:
            tree.mkdir(str(Path(relative).parent))
            tree.create(relative, payload)
        return {"path": relative, "sha256": digest(payload)}

    def blob(self, reference):
        path = reference.get("path") if isinstance(reference, dict) else None
        if not isinstance(path, str) or not path.startswith(BASE + "/"):
            raise CoreError("WORKFLOW_INVALID", "缺少有效的流程来源。")
        with SafeTree(self.core.project) as tree:
            raw = tree.read(path)
        if digest(raw) != reference.get("sha256"):
            raise CoreError("WORKFLOW_CHANGED", "流程来源文件发生变化，请先检查记录。")
        return json.loads(raw)

    def recover_locks(self, confirmed=False):
        """Explicit local crash recovery; no kill, timeout guessing or global changes."""
        if not confirmed or self.core._active:
            raise CoreError("RECOVERY_REQUIRED", "请先明确确认恢复中断记录；正在运行的步骤不能恢复。")
        state = self.read()
        hosts = [step.get("host_pid") for run in state["runs"].values() for step in run.get("attempts", {}).values()
                 if step.get("status") == "IN_FLIGHT" and step.get("host_pid")]
        hosts += [job.get("host_pid") for job in state["outline_jobs"].values() if job.get("status") == "IN_FLIGHT" and job.get("host_pid")]
        if any(alive(pid) is not False for pid in hosts):
            raise CoreError("WORKFLOW_RUNNING", "先前的官方请求进程尚未确认结束，暂不重试。")
        known_role_locks = {attempt.get("role_lock_sha256") for run in state["runs"].values() for attempt in run.get("attempts", {}).values()}
        known_role_locks.update(job.get("role_lock_sha256") for job in state["outline_jobs"].values())
        removed = []
        with SafeTree(self.core.project) as tree:
            for name in (".ns-author.lock", ".ns-writer-turn.lock", ".ns-editor-turn.lock"):
                relative = ".novel-studio/" + name
                raw = tree.maybe_read(relative)
                if raw is None:
                    continue
                if name != ".ns-author.lock" and digest(raw) not in known_role_locks:
                    raise CoreError("WORKFLOW_RUNNING", "该角色锁无法证明属于本次工作流；不自动处理其他会话记录。")
                match = re.fullmatch(rb"([1-9][0-9]*):[a-f0-9]{32}", raw)
                if match is None or alive(int(match[1])) is not False:
                    raise CoreError("WORKFLOW_RUNNING", "无法确认旧记录的所属进程已结束；保留锁，不擅自解锁。")
                tree.mkdir(BASE + "/Recovered-Locks")
                tree.create(BASE + "/Recovered-Locks/" + uuid.uuid4().hex + ".bin", raw)
                tree._discard_internal(relative, digest(raw))
                removed.append(name)
        return removed
