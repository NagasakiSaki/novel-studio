"""Bound local Core API. All novel mutations reuse SafeTree/workspace_admin.

Bridge identities never become authors. Approval handles exist only in the UI
service instance; the wire dispatcher has no commit/restore/import operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import workspace_admin as admin
from safe_project import SafeTree, digest, relative_parts, LIMIT as TEXT_BUDGET_BYTES

VERSION = "0.1.0"
AREAS = {"drafts": "Writer-Workspace/Drafts", "proposals": "Writer-Workspace/Proposed-Changes",
         "reports": "Editor-Workspace/Review-Reports", "writer_context": "Writer-Workspace/Context-Snapshot",
         "editor_context": "Editor-Workspace/Manuscript-Snapshot", "canonical": "Canonical"}
READ = frozenset("get_status get_health get_version get_capabilities list_agents get_agent_status get_conversation_status get_workspace_info list_models get_current_model get_usage get_cache_stats get_latency get_error_summary get_logs validate_project validate_permissions list_drafts get_version_history preview_diff read_document run_self_test".split())
WRITE = frozenset("create_draft update_draft create_review_report create_proposal request_approval set_model set_effort test_connection".split())


class CoreError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Capability:
    project_id: str
    role: str
    methods: frozenset = READ
    expires: float = float("inf")


def checked_text(value, maximum=TEXT_BUDGET_BYTES):
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum or "\0" in value:
        raise CoreError("INVALID_ARTIFACT", "内容格式或大小不符合要求。")
    return value


class Core:
    def __init__(self, project: Path, plugin: Path = ROOT):
        self.project, self.plugin = Path(project), Path(plugin)
        with SafeTree(self.project) as tree:
            admin.validate(tree)
            self.metadata = json.loads(tree.read(".novel-studio/project.json"))
        self.project_id = self.metadata["project_id"]
        self.mutex = threading.RLock()
        self._approvals = {}
        self._active = False
        self.models = None
        from .read_cache import DerivedCache
        from .material_search import SearchCache
        self._derived = DerivedCache()
        self._search_cache = SearchCache()
        self._document_paths = OrderedDict()

    def clear_read_caches(self):
        """Disposable derived data only; no project files are changed."""
        with self.mutex:
            self._document_paths.clear()
            self._derived.clear()
            self._search_cache.clear()

    def cache_diagnostics(self):
        with self.mutex:
            return {'scope': 'this Core instance; local computations, not API prompt cache',
                    'document_paths': len(self._document_paths), 'derived': self._derived.stats(),
                    'material_index': self._search_cache.stats()}

    def parse_json(self, raw):
        return self._derived.get(('json-v1', digest(raw)), lambda: json.loads(raw), weight=len(raw) * 8 + 512)

    def measure_text(self, text, metric='novel_visible_chars_v1'):
        from .text_metrics import measure
        return self._derived.get(('measure-v1', digest(text.encode('utf-8')), metric),
                                 lambda: measure(text, metric), weight=2048)

    def _document_row(self, relative, area, payload):
        revision = digest(payload)
        def heading_title():
            first = next((line.strip() for line in payload.decode('utf-8-sig').splitlines() if line.strip()), '')
            heading = re.match(r'^#{1,6}\s+(.+)$', first)
            return (heading.group(1).strip() if heading else Path(relative).stem)[:60]
        title = self._derived.get(('heading-v1', relative, revision), heading_title,
                                  weight=512 + len(relative.encode('utf-8')))
        return {'document_id': hashlib.sha256(relative.encode()).hexdigest()[:32],
                'title': Path(relative).name, 'area': area, 'revision': revision,
                'display_title': title, 'relative': relative}

    @classmethod
    def create(cls, parent, title, plugin=ROOT, protected=()):
        if len(relative_parts(title)) != 1:
            raise CoreError("PATH_FORBIDDEN", "作品名称不能包含文件夹路径。")
        root = Path(parent) / title
        admin.initialize(root, Path(plugin), protected, True)
        return cls(root, plugin)

    def bind(self, role, methods=READ, seconds=3600):
        if role not in {"writer", "editor", "observer"} or not set(methods) <= READ | WRITE:
            raise CoreError("ROLE_DENIED", "无效的接口授权。")
        return Capability(self.project_id, role, frozenset(methods), time.monotonic() + seconds)

    @property
    def recovery(self):
        from .recovery_extensions import RecoveryExtensions
        return RecoveryExtensions(self)

    @property
    def materials(self):
        from .materials import MaterialService
        return MaterialService(self)

    @property
    def creative(self):
        from .creative import CreativeService
        return CreativeService(self)

    @property
    def lengths(self):
        from .lengths import LengthService
        return LengthService(self)

    @property
    def workflow(self):
        from .workflow import WorkflowService
        return WorkflowService(self)

    def _state(self, change=None):
        with SafeTree(self.project) as tree:
            if change is None:
                raw = tree.maybe_read(".novel-studio/Desktop.json")
                return json.loads(raw) if raw else {}
            with tree.lock():
                old = tree.maybe_read(".novel-studio/Desktop.json")
                state = json.loads(old) if old else {}
                change(state)
                tree.publish(".novel-studio/Desktop.json", admin.encode(state), digest(old) if old else None)
                return state

    @property
    def title(self):
        return self._state().get('display_title', self.project.name)

    def rename_title(self, title):
        checked_text(title, 180)
        if not title.strip() or '\n' in title or '\r' in title:
            raise CoreError('TITLE_INVALID', '请填写一行作品名称。')
        with self.mutex:
            self.require_idle()
            self._state(lambda state:state.update(display_title=title.strip(),
                title_history=(state.get('title_history', []) + [state.get('display_title', self.project.name)])[-50:]))

    def log(self, operation, status="PASS", **safe_fields):
        # Caller supplies IDs, numeric metrics and enums only. No prompts, paths,
        # tool arguments, CLI stderr, manuscripts or authentication material.
        entry = {"time": time.time(), "operation": operation, "status": status, **safe_fields}
        from .diagnostics import record
        record(self, 'workflow_step', action=operation, status=status, **{k: v for k, v in safe_fields.items() if k in {'role', 'error_code'}})
        self._state(lambda s: s.update(logs=(s.get("logs", []) + [entry])[-200:]))

    def require_idle(self):
        if self._active:
            raise CoreError("BUSY", "这一回合仍在运行，请等待结束。")
        with SafeTree(self.project) as tree:
            if any(tree.maybe_read(f".novel-studio/.ns-{r}-turn.lock") is not None for r in ("writer", "editor")):
                raise CoreError("BUSY", "另一个窗口仍在处理这部作品。")

    def documents(self, role="author", *, _cancel=None, _areas=None, _manuscript_counts=False):
        def checkpoint():
            if _cancel is not None and _cancel.is_set():
                raise CoreError('VIEW_CANCELLED', '本地目录加载已停止，作品文件没有改变。')
        areas = (list(AREAS) if role == "author" else
                 ["drafts", "proposals", "writer_context"] if role == "writer" else
                 ["reports", "editor_context"] if role == "editor" else [])
        if _areas is not None:
            if not isinstance(_areas, (tuple, list)) or len(set(_areas)) != len(_areas) or any(a not in areas for a in _areas):
                raise CoreError('ROLE_DENIED', '文档区域超出当前角色范围。')
            areas = list(_areas)
        result = []
        state = admin.ui_state(self.project)
        with SafeTree(self.project) as tree:
            for area in areas:
                checkpoint()
                if area in {'writer_context', 'editor_context'}:
                    manifest_path = state.get('context' if area == 'writer_context' else 'review')
                    selected = {}
                    if manifest_path:
                        if relative_parts(manifest_path)[:2] != relative_parts(AREAS[area]):
                            raise CoreError('SNAPSHOT_INVALID', '资料副本来源不匹配。')
                        manifest, unused_revision = admin.snapshot_manifest(tree, manifest_path)
                        selected = {(Path(manifest_path).parent / name).as_posix(): payload
                                    for name, payload in admin.snapshot_payloads(tree, manifest_path, manifest).items()}
                    if area == 'writer_context' and state.get('feedback'):
                        feedback = state['feedback']
                        if relative_parts(feedback)[:3] != ('Writer-Workspace', 'Context-Snapshot', 'Author-Feedback'):
                            raise CoreError('SNAPSHOT_INVALID', '作者意见副本超出范围。')
                        selected[feedback] = tree.read(feedback)
                else:
                    selected = {name: None for name in tree.files(AREAS[area])}
                for relative, snapshot_payload in selected.items():
                    checkpoint()
                    if Path(relative).suffix.lower() not in {".md", ".txt", ".json"} or relative.endswith("/SNAPSHOT.json"):
                        continue
                    payload = snapshot_payload if snapshot_payload is not None else tree.read(relative)
                    row = self._document_row(relative, area, payload)
                    if _manuscript_counts and (area == 'drafts' or relative.startswith('Canonical/Final-Manuscript/')):
                        row['word_count'] = self.measure_text(payload.decode('utf-8-sig'))['count']
                    result.append(row)
        with self.mutex:
            for row in result:
                self._document_paths[row['document_id']] = (row['relative'], row['area'])
                self._document_paths.move_to_end(row['document_id'])
            while len(self._document_paths) > 4096:
                self._document_paths.popitem(last=False)
        return result

    def snapshot_history(self, role='editor', *, cursor=None, limit=20):
        from .snapshot_history import page
        with self.mutex:
            return page(self, role, cursor, limit)

    def close_snapshot_history(self, cursor):
        from .snapshot_history import close
        with self.mutex:
            close(self, cursor)

    def snapshot_document(self, snapshot_id, name, revision, role='editor'):
        from .snapshot_history import document
        return document(self, role, snapshot_id, name, revision)

    def restore_snapshot(self, snapshot_id, name, revision):
        with self.mutex:
            self.require_idle()
            old = self.snapshot_document(snapshot_id, name, revision)
            if not name.startswith('Drafts/'):
                raise CoreError('ROLE_DENIED', '只能将历史稿件恢复为新的待审草稿。')
            source = {'snapshot_id': snapshot_id, 'name': name, 'manifest_revision': revision,
                      'revision': old['revision']}
            result = self._artifact('writer', 'drafts', old['text'], uuid.uuid4().hex, _source_snapshot=source)
            return {**result, 'source_snapshot': source}

    def document(self, identifier, role="author"):
        # A catalogue entry locates a path, never authorizes a saved revision.
        # Every selected payload still goes through SafeTree and a fresh SHA.
        with self.mutex:
            known = self._document_paths.get(identifier) if isinstance(identifier, str) else None
            if known is not None:
                self._document_paths.move_to_end(identifier)
        if known is not None and known[1] not in {'writer_context', 'editor_context'}:
            relative, area = known
            allowed = (set(AREAS) if role == 'author' else {'drafts', 'proposals'} if role == 'writer'
                       else {'reports'} if role == 'editor' else set())
            if area not in allowed:
                raise CoreError('PROJECT_SCOPE_DENIED', '所选文件不在当前角色的可见范围。')
            try:
                with SafeTree(self.project) as tree:
                    payload = tree.read(relative)
            except FileNotFoundError:
                with self.mutex:
                    self._document_paths.pop(identifier, None)
                raise CoreError('PROJECT_SCOPE_DENIED', '所选文件已不在当前作品中，请重新载入。') from None
            return {**self._document_row(relative, area, payload), 'text': payload.decode('utf-8-sig')}
        # Snapshot membership/manifest/source validation is deliberately fresh;
        # a previous catalogue cannot keep a superseded snapshot authorized.
        row = next((r for r in self.documents(role) if r["document_id"] == identifier), None)
        if row is None:
            raise CoreError("PROJECT_SCOPE_DENIED", "所选文件不在当前角色的可见范围。")
        with SafeTree(self.project) as tree:
            payload = tree.read(row["relative"])
        if digest(payload) != row["revision"]:
            raise CoreError("REVISION_CONFLICT", "文件刚刚发生变化，请重新载入。")
        with self.mutex:
            self._document_paths[identifier] = (row['relative'], row['area'])
            self._document_paths.move_to_end(identifier)
            while len(self._document_paths) > 4096:
                self._document_paths.popitem(last=False)
        return {**row, "text": payload.decode("utf-8-sig")}

    def _artifact(self, role, kind, text, key, identifier=None, expected=None, dry_run=False, _broker=False, _source_snapshot=None):
        if not _broker:
            self.require_idle()
        allowed = {"writer": {"drafts", "proposals"}, "editor": {"reports"}}
        if kind not in allowed.get(role, set()) or (kind == "reports" and identifier is not None):
            raise CoreError("ROLE_DENIED", "此角色只能保存自己的创作产物，不能覆盖稿件或报告。")
        checked_text(text)
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", key):
            raise CoreError("INVALID_REQUEST", "缺少有效的操作编号。")
        fingerprint = digest(admin.encode([role, kind, text, identifier, expected]))
        op_id = digest(key.encode())
        op_path = ".novel-studio/Operations/" + op_id + ".json"
        if identifier:
            row = self.document(identifier, role)
            if row["area"] != kind:
                raise CoreError("ROLE_DENIED", "文件不属于当前输出区域。")
            relative = row["relative"]
        else:
            relative = AREAS[kind] + "/" + ("审稿-" if kind == "reports" else "草稿-" if kind == "drafts" else "提案-") + op_id[:20] + ".md"
        payload = text.encode("utf-8")
        new_revision = digest(payload)
        response = {"document_id": hashlib.sha256(relative.encode()).hexdigest()[:32], "revision": new_revision,
                    "area": kind, "dry_run": dry_run}
        with SafeTree(self.project) as tree:
            with tree.lock():
                old_record = tree.maybe_read(op_path)
                if old_record:
                    previous = json.loads(old_record)
                    if previous["fingerprint"] != fingerprint:
                        raise CoreError("IDEMPOTENCY_CONFLICT", "同一操作编号不能用于不同内容。")
                    current = tree.maybe_read(relative)
                    if previous["status"] == "COMMITTED":
                        return previous["response"]
                    if current is not None and digest(current) == new_revision:
                        tree.publish(op_path, admin.encode({**previous, "status": "COMMITTED"}), digest(old_record))
                        return previous["response"]
                before = tree.maybe_read(relative)
                before_digest = digest(before) if before is not None else None
                if before_digest != expected:
                    raise CoreError("REVISION_CONFLICT", "草稿已变化，已阻止覆盖。请重新载入并合并您的修改。")
                if dry_run:
                    return response
                tree.mkdir(".novel-studio/Operations")
                record = {"status": "PREPARED", "fingerprint": fingerprint, "response": response,
                          "relative": relative, "before": before_digest, "after": new_revision}
                if _source_snapshot is not None:
                    record['source_snapshot'] = _source_snapshot
                if old_record is None:
                    tree.create(op_path, admin.encode(record))
                    old_record = admin.encode(record)
                if before is not None:
                    tree.mkdir("Backups/Checkpoints/Core")
                    backup = "Backups/Checkpoints/Core/" + op_id + ".bin"
                    saved = tree.maybe_read(backup)
                    if saved is None:
                        tree.create(backup, before)
                    elif digest(saved) != before_digest:
                        raise CoreError("REVISION_CONFLICT", "历史快照与当前版本不一致。")
                tree.publish(relative, payload, expected)
                tree.publish(op_path, admin.encode({**record, "status": "COMMITTED"}), digest(old_record))
        self.log("artifact_saved", role=role, area=kind, document_id=response["document_id"])
        return response

    def snapshot(self):
        with self.mutex:
            self.require_idle()
            if self.materials.records():
                return self.materials.snapshot()
            result = admin.context_snapshot(self.project, True)
            admin.ui_state(self.project, {"context": result["destination"] + "/SNAPSHOT.json"})
            return result

    def validated_context(self, role):
        view = self._context_view(role)
        return view[2], view[3]

    def _context_view(self, role):
        """Fresh, call-local verified bytes. Never retained in any Core cache."""
        state = admin.ui_state(self.project)
        manifest = state.get("context" if role == "writer" else "review")
        prefix = ("Writer-Workspace", "Context-Snapshot") if role == "writer" else ("Editor-Workspace", "Manuscript-Snapshot")
        if not isinstance(manifest, str):
            raise CoreError("SNAPSHOT_REQUIRED", "请先更新资料副本。" if role == "writer" else "请先把草稿交给责编。")
        parts = relative_parts(manifest)
        if len(parts) != 4 or parts[:2] != prefix or parts[-1] != "SNAPSHOT.json" or not re.fullmatch(r"[a-f0-9]{32}", parts[2]):
            raise CoreError("SNAPSHOT_INVALID", "资料副本选择不符合安全范围。")
        with SafeTree(self.project) as tree:
            try:
                data, revision = admin.snapshot_manifest(tree, manifest)
                payloads = admin.snapshot_payloads(tree, manifest, data)
            except ValueError as error:
                raise CoreError('SNAPSHOT_INVALID', '资料副本清单或内容已变化，请重新生成。') from error
        if role == "writer" and state.get("feedback"):
            feedback = relative_parts(state["feedback"])
            if feedback[:3] != ("Writer-Workspace", "Context-Snapshot", "Author-Feedback"):
                raise CoreError("SNAPSHOT_INVALID", "作者意见副本超出范围。")
        return self, role, state, manifest, data, payloads, revision

    def _use_context_view(self, role, view=None):
        if view is None:
            return self._context_view(role)
        # Private in-process transfer, not a serialized manifest or Bridge API.
        # Recheck identity/selection/manifest while reusing the exact bytes that
        # this invocation already verified, never bytes read after that check.
        if not isinstance(view, tuple) or len(view) != 7 or view[0] is not self or view[1] != role:
            raise CoreError('SNAPSHOT_INVALID', '资料读取批次不属于当前作品或角色。')
        state = admin.ui_state(self.project)
        if state.get('context' if role == 'writer' else 'review') != view[3]:
            raise CoreError('SNAPSHOT_INVALID', '资料副本选择已变化，请重新组装上下文。')
        with SafeTree(self.project) as tree:
            try:
                unused, revision = admin.snapshot_manifest(tree, view[3])
            except ValueError as error:
                raise CoreError('SNAPSHOT_INVALID', '资料副本清单已变化，请重新组装上下文。') from error
        if revision != view[6]:
            raise CoreError('SNAPSHOT_INVALID', '资料副本清单已变化，请重新组装上下文。')
        return view

    def handoff(self, identifier):
        with self.mutex:
            self.require_idle()
            row = self.document(identifier, "writer")
            if row["area"] != "drafts":
                raise CoreError("ROLE_DENIED", "请先选择要交接的草稿。")
            state = admin.ui_state(self.project)
            if not state.get("context"):
                self.snapshot()
                state = admin.ui_state(self.project)
            result = admin.review_snapshot(self.project, row["relative"], state["context"], True)
            admin.ui_state(self.project, {"review": result["destination"] + "/SNAPSHOT.json", "last_draft": row["relative"]})
            return result

    def feedback(self, report_id, text):
        with self.mutex:
            self.require_idle()
            report = self.document(report_id, "editor")
            if report["area"] != "reports":
                raise CoreError("ROLE_DENIED", "请先选择独立审稿报告。")
            name = admin.author_feedback(self.project, report["relative"], checked_text(text, 100_000))
            admin.ui_state(self.project, {"feedback": name})
            return {"saved": True}

    def author_preview(self, identifier=None, category="Final-Manuscript", filename=None, source=None, transaction=None):
        with self.mutex:
            self.require_idle()
            if transaction:
                plan = admin.restore(self.project, transaction)
                operation = ("restore", transaction)
            elif source:
                source = Path(source)
                if source.suffix.lower() not in {".md", ".txt", ".json"}:
                    raise CoreError("INVALID_REQUEST", "请选择 Markdown、纯文本或 JSON 资料。")
                if category not in admin.CANON:
                    raise CoreError("PATH_FORBIDDEN", "无效的资料类别。")
                plan = admin.import_material(self.project, source, "Canonical/" + category + "/" + source.name)
                operation = ("import", source)
            else:
                row = self.document(identifier, "writer")
                name = filename or row["title"]
                if len(relative_parts(name)) != 1 or category not in admin.CANON:
                    raise CoreError("PATH_FORBIDDEN", "正式文件名不能包含目录。")
                plan = admin.promote(self.project, row["relative"], "Canonical/" + category + "/" + name)
                operation = ("promote", row["relative"])
            handle = secrets.token_urlsafe(32)
            display_plan = plan
            if transaction and plan['target'].startswith('Canonical/Final-Manuscript/'):
                plan, cid = self.recovery.restore_preview(plan)
                operation = ('workflow_restore', cid)
            self._approvals[handle] = (time.monotonic() + 300, plan, operation)
            if transaction and display_plan["target"].startswith("Canonical/Canon-Worldbuilding/Materials/"):
                return {"handle": handle, "operation": "restore", **self.materials.restore_display(display_plan)}
            return {"handle": handle, "target": display_plan["target"], "diff": display_plan["diff"], "operation": operation[0]}

    def author_commit(self, handle, confirmed=False):
        with self.mutex:
            self.require_idle()
            item = self._approvals.pop(handle, None)
            if not confirmed or item is None or item[0] < time.monotonic():
                raise CoreError("APPROVAL_REQUIRED", "请重新查看完整差异并明确批准。")
            _, plan, (operation, source) = item
            if operation == "workflow_outline":
                return self.workflow.planning.commit_approved(plan, source)
            if operation == "workflow_chapters":
                return self.workflow.chapters.commit_approved(plan, source)
            if operation == "workflow_chapter_revision":
                return self.workflow.chapters.commit_revision(plan, source)
            if operation == 'canonical_recovery':
                return self.recovery.commit(plan)
            if operation == 'workflow_restore':
                result = self.recovery.submit(plan, source)
                try:
                    self.recovery.complete_derived(source)
                except (ValueError, OSError) as error:
                    raise CoreError('DERIVED_UPDATE_PENDING', '正文和章节目录已保存；请在正式修订检查中补齐字数和显示记录，不要重新发布正文。') from error
                return {'transaction_id': result['transaction_id'], 'target': result['target']}
            if operation == 'legacy_association':
                return self.recovery.associate(plan)
            if operation == 'legacy_batch_association':
                return self.recovery.associate_batch(plan)
            if operation == 'legacy_verified':
                return self.recovery.verify_association(plan)
            if operation == 'recovery_batches':
                return self.recovery.recover_batches(plan)
            if operation == 'recovery_derived':
                return self.recovery.recover_derived(plan)
            if operation == 'workflow_schema':
                from .schema_migration import commit
                return commit(self, plan)
            if operation == "restore":
                result = admin.restore(self.project, source, True, plan["approval_digest"])
            elif operation == "material":
                item, record, payload = source
                result = self.materials.commit(plan, item, record, payload)
            elif operation == "import":
                result = admin.import_material(self.project, source, plan["target"], True, plan["approval_digest"])
            else:
                result = admin.promote(self.project, source, plan["target"], True, plan["approval_digest"])
            self.log("author_" + operation, transaction_id=result["transaction_id"])
            if operation == "restore" and result["target"].startswith("Canonical/Final-Manuscript/"):
                self.workflow.chapters.record_restore(result)
            self._state(lambda s: s.update(version_order=(s.get("version_order", []) + [result["transaction_id"]])[-1000:]))
            return {"transaction_id": result["transaction_id"], "target": result["target"]}

    def history(self):
        result = []
        with SafeTree(self.project) as tree:
            for name in tree.files("Backups/Transactions"):
                if name.endswith("/COMMITTED.json"):
                    data = json.loads(tree.read(name))
                    title = Path(data["target"]).name
                    material = False
                    if data["target"].startswith("Canonical/Canon-Worldbuilding/Materials/"):
                        after = json.loads(tree.read(str(Path(name).parent / "after.bin")))
                        title = "资料：" + after["source"]["name"] + " · v" + str(after["version"])
                        material = True
                    result.append({"version_id": data["transaction_id"], "title": title, "material": material, 'target':data['target'],
                                   "restorable": data["before_sha256"] is not None})
        order = self._state().get("version_order", [])
        positions = {value: index for index, value in enumerate(order)}
        return sorted(result, key=lambda row: positions.get(row["version_id"], -1), reverse=True)

    def validate_permissions(self, *, verify_plugin=True):
        with SafeTree(self.project) as tree:
            expected = admin.policies(self.project, Path(self.metadata["plugin"]), self.metadata["private_roots"],
                                      verify_plugin=verify_plugin)
            same = all(json.loads(tree.read(f".novel-studio/Sessions/{r}.json")) == expected[r] for r in expected)
        return {"legacy_profiles_match": same, "native_write_policy": "deny_all_in_workbench_sessions",
                "current_host_enforcement": "NOT TESTED", "os_isolation": False}

    def dispatch(self, capability, method, params, key=None):
        if capability.project_id != self.project_id:
            raise CoreError("PROJECT_SCOPE_DENIED", "接口没有这部作品的授权。")
        if method not in READ | WRITE:
            raise CoreError("METHOD_NOT_FOUND", "当前版本不支持此接口。")
        if capability.expires < time.monotonic() or method not in capability.methods:
            raise CoreError("ROLE_DENIED", "此接口未获授权或授权已过期。")
        with self.mutex:
            return self._dispatch(capability.role, method, params, key)

    def _dispatch(self, role, method, p, key):
        if method == "get_version":
            from .version_info import versions
            return {"version": VERSION, "protocol_version": "1", 'versions': versions(self)}
        if method in {"get_status", "get_health", "validate_project"}:
            return {"project_id": self.project_id, "busy": self._active, "local": True, "network_listener": False}
        if method == "get_capabilities":
            return {"read": sorted(READ), "optional_grants": sorted(WRITE), "author_commit": "not_exposed",
                    "self_test": "read_only_project_consistency", "export": "author_UI_only"}
        if method == "run_self_test":
            checks = [{"check": "session_profile_integrity", "status": "PASS" if self.validate_permissions()["legacy_profiles_match"] else "FAIL"}]
            state = admin.ui_state(self.project)
            for identity, field in (("writer", "context"), ("editor", "review")):
                if not state.get(field):
                    checks.append({"check": identity + "_snapshot", "status": "NOT TESTED"})
                else:
                    try:
                        self.validated_context(identity)
                        checks.append({"check": identity + "_snapshot", "status": "PASS"})
                    except (CoreError, ValueError, OSError, KeyError):
                        checks.append({"check": identity + "_snapshot", "status": "FAIL"})
            return {"scope": "read_only_project_consistency; no model request or mutation", "checks": checks}
        if method in {"list_agents", "get_agent_status", "get_conversation_status"}:
            state = self._state()
            roles = [role] if role != "observer" else ["writer", "editor"]
            result = []
            for r in roles:
                saved = state.get("roles", {}).get(r, {})
                provider = saved.get("provider", "antigravity")
                selected = saved if provider == "antigravity" else saved.get("api_configs", {}).get(provider, {})
                result.append({"role": r, "busy": self._active, "provider": provider,
                    "conversation_id": selected.get("cid") if provider == "antigravity" else None,
                    "conversation_scope": "official_cli_cid" if provider == "antigravity" else "local_native_history; no CLI CID",
                    "model": selected.get("model_id"), "effort": selected.get("effort")})
            return result
        if method == "get_workspace_info":
            return {"project_id": self.project_id, "role": role, "areas": sorted(set(r["area"] for r in self.documents(role)))}
        if method == "list_drafts":
            return [{k: v for k, v in r.items() if k != "relative"} for r in self.documents(role)]
        if method == "read_document":
            return {k: v for k, v in self.document(p["document_id"], role).items() if k != "relative"}
        if method == "get_version_history":
            return self.history()
        if method in {"preview_diff", "request_approval"}:
            # An agent may request a review; it receives no UI approval capability.
            row = self.document(p["document_id"], role)
            return {"status": "pending_author_review", "document_id": row["document_id"], "revision": row["revision"]}
        if method == "validate_permissions":
            return self.validate_permissions()
        if method in {"get_logs", "get_error_summary"}:
            return [r for r in self._state().get("logs", []) if method == "get_logs" or r["status"] != "PASS"]
        if method in {"get_usage", "get_cache_stats", "get_latency"}:
            if self.models is not None:
                return self.models.usage(role)
            from .usage import summarize
            return summarize(self._state().get("runs", []), role)
        if method in {"list_models", "get_current_model", "set_model", "set_effort", "test_connection"}:
            if self.models is None:
                raise CoreError("CLI_MISSING", "请先连接官方 Antigravity 程序。")
            if method == "get_current_model" and role == "observer":
                return {r: self.models.configuration(r) for r in ("writer", "editor")}
            return self.models.dispatch(role, method, p)
        if method in {"create_draft", "update_draft", "create_review_report", "create_proposal"}:
            kind = {"create_draft": "drafts", "update_draft": "drafts", "create_review_report": "reports", "create_proposal": "proposals"}[method]
            return self._artifact(role, kind, p["text"], key, p.get("document_id"), p.get("expected_revision"), p.get("dry_run", False))
        raise CoreError("METHOD_NOT_FOUND", "当前版本不支持此接口。")
