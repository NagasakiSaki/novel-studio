"""Author-approved material registry. SafeTree/admin remain the only file layer."""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid
from .core import CoreError, ROOT, SafeTree, admin, digest
from .material_formats import FORMATS, SOURCE_LIMIT
from .material_search import search

FORMAT = "novel-studio-material-v1"
CONTEXT_FORMAT = "novel-studio-material-context-v1"
BASE = "Canonical/Canon-Worldbuilding/Materials"
TYPES = (
    ("global_preset", "全局综合预设", "一个文件包含大量全局资料、规则和预设，无需手动拆分。"),
    ("characters", "人物设定", "人物背景、关系、目标与知情边界。"),
    ("world", "世界观设定", "世界背景、地点、制度和特殊设定。"),
    ("outline", "故事大纲", "整书、分卷、章节方向与场景安排。"),
    ("writing_rules", "文风与写作规则", "经作者批准后提供的风格、叙事和创作约束。"),
    ("timeline", "时间线", "事件日期、人物经历和叙述顺序资料。"),
    ("tracking", "伏笔与剧情追踪", "伏笔、待回收线索和剧情状态参考，不自动修改追踪。"),
    ("chapter", "章节正文", "作为资料保存的正文，不自动成为正式定稿。"),
    ("research", "研究参考资料", "研究笔记、史料与证据，保持来源可追溯。"),
    ("glossary", "术语表", "名词、译名、专名和定义。"),
    ("notes", "普通笔记", "其他创作备忘和片段资料。"),
    ("custom", "自定义资料类型", "自行命名的资料类别，格式和安全边界不变。"),
)
TYPE_IDS = {row[0] for row in TYPES}


@dataclass
class Prepared:
    source: Path
    raw: bytes
    parsed: dict
    material_type: str
    custom_label: str = ""
    update_id: str | None = None
    archived: bool = False
    display_name: str | None = None
    context_enabled: bool | None = None
    retired: bool | None = None


def parse_source(path):
    path = Path(path)
    if not path.is_absolute() or path.suffix.lower() not in FORMATS:
        raise CoreError("FORMAT_UNSUPPORTED", "请选择当前已实现的文件格式。")
    with SafeTree(path.parent, limit=SOURCE_LIMIT) as tree:
        raw = tree.read(path.name)
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    try:
        response = subprocess.run([str(executable), "-I", "-X", "utf8", "-B", str(ROOT / "src/novel_studio/parse_worker.py"), path.suffix.lower()],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise CoreError("PARSE_TIMEOUT", "解析超过 25 秒，已停止本次解析；其他文件不受影响。") from None
    try:
        value = json.loads(response.stdout)
    except ValueError:
        raise CoreError("PARSE_FAILED", "解析进程未能安全完成，可能是文档损坏或资源超限。") from None
    if not value.get("ok"):
        raise CoreError(value["error"]["code"], value["error"]["message"])
    return raw, value["data"]


def selected_sections(record):
    chosen = set(record["always_section_ids"])
    for section in record["sections"]:
        if section["parent"] in chosen:
            chosen.add(section["id"])
    return chosen


def policy_description(record):
    titles = [s["title"] for s in record["sections"] if s["id"] in set(record["always_section_ids"])]
    return ("资料类型：" + dict((r[0], r[1]) for r in TYPES)[record["material_type"]] + (" / " + record["custom_label"] if record.get("custom_label") else "") +
            "\n原件：" + record["source"]["name"] + "（" + record["source"]["format"] + "）；版本 " + str(record["version"]) +
            "\n上下文方式：" + ("完整预设" if record["context_mode"] == "full" else "常驻规则 + 其余按需检索") +
            "\n始终提供：" + ("、".join(titles) or "无（作者确认其余均为按需资料）") +
            '\n显示名称：' + record.get('display_name', record['source']['name']) +
            '\n创作上下文：' + ('已启用' if record.get('context_enabled', True) else '已排除后续注入，原件和版本保留；不会抹除已发送的宿主对话历史') +
            ('\n已移到资料回收区，原件和版本保留' if record.get('retired') else ''))


def body_diff(before, after, before_name, after_name):
    lines = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=before_name, tofile=after_name)
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


class MaterialService:
    def __init__(self, core):
        self.core = core

    def records(self, include_retired=False):
        result = []
        total = 0
        with SafeTree(self.core.project) as tree:
            # Existing projects need no migration or directory creation on read.
            for name in tree.files("Canonical/Canon-Worldbuilding"):
                if not name.startswith(BASE + "/") or not name.endswith(".json"):
                    continue
                payload = tree.read(name)
                total += len(payload)
                if total > 64 * 1024 * 1024:
                    raise CoreError("LIBRARY_LIMIT", "资料库超过本版 64 MiB 安全加载上限，请分工程管理；没有静默忽略资料。")
                data = self.core.parse_json(payload)
                if data.get("format") == FORMAT and (include_retired or not data.get('retired', False)):
                    result.append({**data, "target": name, "record_sha256": digest(payload)})
        return result

    def get(self, identifier):
        value = next((r for r in self.records(include_retired=True) if r["material_id"] == identifier), None)
        if value is None:
            raise CoreError("MATERIAL_MISSING", "这份资料不在当前工程的已批准资料库中。")
        return value

    def prepare(self, path, material_type, update_id=None, custom_label=""):
        if material_type not in TYPE_IDS or len(custom_label) > 80:
            raise CoreError("INVALID_REQUEST", "请选择有效的资料类型。")
        if update_id:
            self.get(update_id)
        raw, parsed = parse_source(path)
        return Prepared(Path(path), raw, parsed, material_type, custom_label, update_id)

    def duplicates(self, item):
        return [r for r in self.records() if r["source"]["sha256"] == digest(item.raw) and r["material_type"] == item.material_type]

    def prepare_policy(self, identifier):
        row = self.get(identifier)
        relative = row["asset_path"]
        with SafeTree(self.core.project, limit=SOURCE_LIMIT) as tree:
            raw = tree.read(relative)
        if digest(raw) != row["source"]["sha256"]:
            raise CoreError("MATERIAL_CORRUPT", "原件摘要不匹配，已停止。")
        parsed = {"text": row["text"], "sections": row["sections"], "warnings": row["warnings"], "format": row["source"]["format"]}
        return Prepared(Path(row["source"]["path"]), raw, parsed, row["material_type"], row.get("custom_label", ""), identifier, True)

    def preview(self, item, always_ids, mode, scope_confirmed=False, allow_duplicate=False):
        self.core.require_idle()
        if mode not in {"retrieval", "full"} or not set(always_ids) <= {s["id"] for s in item.parsed["sections"]}:
            raise CoreError("INVALID_REQUEST", "上下文方式或规则章节无效。")
        if item.material_type in {"global_preset", "writing_rules"} and not scope_confirmed:
            raise CoreError("SCOPE_CONFIRMATION_REQUIRED", "请明确确认常驻规则与按需资料范围；程序不会替您猜测优先级。")
        duplicates = self.duplicates(item)
        if duplicates and not item.update_id and not allow_duplicate:
            raise CoreError("DUPLICATE_MATERIAL", "已有同类型、同内容资料，默认跳过；需要独立副本时请明确选择。")
        old = self.get(item.update_id) if item.update_id else None
        if item.display_name is not None:
            from .core import checked_text
            checked_text(item.display_name, 180)
        if any(value is not None and type(value) is not bool for value in (item.context_enabled, item.retired)):
            raise CoreError('INVALID_REQUEST', '资料状态格式不正确。')
        identifier = old["material_id"] if old else uuid.uuid4().hex
        asset = ".novel-studio/Material-Assets/" + digest(item.raw) + "/" + item.source.name
        record = {"format": FORMAT, "material_id": identifier, "version": old["version"] + 1 if old else 1,
                  'display_name':item.display_name if item.display_name is not None else (old or {}).get('display_name', item.source.name),
                  'context_enabled':item.context_enabled if item.context_enabled is not None else (old or {}).get('context_enabled', True),
                  'retired':item.retired if item.retired is not None else (old or {}).get('retired', False),
                  "material_type": item.material_type, "custom_label": item.custom_label,
                  "source": {"name": item.source.name, "path": str(item.source), "format": item.source.suffix.lower(),
                             "bytes": len(item.raw), "sha256": digest(item.raw)},
                  "imported_at": time.time(), "text": item.parsed["text"], "sections": item.parsed["sections"],
                  "warnings": item.parsed["warnings"], "always_section_ids": list(dict.fromkeys(always_ids)),
                  "context_mode": mode, "scope_confirmed": scope_confirmed, "asset_path": asset}
        payload = admin.encode(record)
        if len(payload) > SOURCE_LIMIT:
            raise CoreError("MATERIAL_LIMIT", "资料及目录超过本机 32 MiB 文件保护，没有截断保存。")
        target = BASE + "/" + identifier + ".json"
        with SafeTree(self.core.project) as tree:
            plan = admin._plan(tree, "import", item.source.name, target, payload)[0]
        handle = secrets.token_urlsafe(32)
        for key, value in list(self.core._approvals.items()):
            if value[0] < time.monotonic():
                self.core._approvals.pop(key, None)
        self.core._approvals[handle] = (time.monotonic() + 300, plan, ("material", (item, record, payload)))
        before_text = old["text"] if old else ""
        diff = body_diff(before_text, record["text"], "已批准旧版本" if old else "尚未导入", item.source.name)
        details = ("旧版本：\n" + policy_description(old) + "\n\n" if old else "") + "拟批准版本：\n" + policy_description(record)
        return {"handle": handle, "target": dict((r[0], r[1]) for r in TYPES)[item.material_type] + " / " + item.source.name,
                "diff": details + "\n\n正文完整差异：\n" + (diff or "正文未变化；本次变更为资料作用范围或来源/版本信息。"),
                "operation": "material", "material_id": identifier}

    def restore_display(self, plan):
        with SafeTree(self.core.project) as tree:
            old = json.loads(tree.read(plan["target"]))
            restored = json.loads(tree.read(plan["source"]))
        return {"target": "资料版本恢复 / " + restored["source"]["name"],
                "diff": "当前版本：\n" + policy_description(old) + "\n\n拟恢复：\n" + policy_description(restored) +
                        "\n\n正文完整差异：\n" + (body_diff(old["text"], restored["text"], "当前批准版本", "历史批准版本") or "正文相同；恢复资料来源或作用范围信息。")}

    def commit(self, plan, item, record, payload):
        if not item.archived:
            with SafeTree(item.source.parent, limit=SOURCE_LIMIT) as source:
                if digest(source.read(item.source.name)) != record["source"]["sha256"]:
                    raise CoreError("REVISION_CONFLICT", "来源文件已在预览后变化，请重新解析并确认。")
        with SafeTree(self.core.project, limit=SOURCE_LIMIT) as tree:
            with tree.lock():
                tree.mkdir(str(Path(record["asset_path"]).parent))
                raw = tree.maybe_read(record["asset_path"])
                if raw is None:
                    tree.create(record["asset_path"], item.raw)
                elif digest(raw) != record["source"]["sha256"]:
                    raise CoreError("MATERIAL_CORRUPT", "已有原件与摘要不一致。")
                tree.mkdir(".novel-studio/Material-Records")
                version_path = ".novel-studio/Material-Records/" + digest(payload) + ".json"
                existing = tree.maybe_read(version_path)
                if existing is None:
                    tree.create(version_path, payload)
                elif existing != payload:
                    raise CoreError("MATERIAL_CORRUPT", "资料版本记录不一致。")
                bundle = admin._plan(tree, "import", item.source.name, plan["target"], payload)
                return admin._commit(tree, bundle, plan["approval_digest"])

    def snapshot(self):
        with SafeTree(self.core.project) as tree:
            with tree.lock():
                data = {}
                for name in tree.files("Canonical"):
                    payload = tree.read(name)
                    record = json.loads(payload) if name.startswith(BASE + "/") and name.endswith(".json") else None
                    if not record or record.get("format") != FORMAT:
                        data[name] = payload
                        continue
                    if record.get('retired') or not record.get('context_enabled', True):
                        continue
                    descriptor = {"format": CONTEXT_FORMAT, "material_id": record["material_id"], "version": record["version"],
                                  "name": record["source"]["name"], "record_sha256": digest(payload), "source_sha256": record["source"]["sha256"],
                                  "material_type": record["material_type"], "section_count": len(record["sections"]),
                                  "section_index_preview": [{"id": s["id"], "title": s["title"][:160]} for s in record["sections"] if s["level"] <= 2][:64],
                                  "always_section_ids": record["always_section_ids"], "context_mode": record["context_mode"],
                                  "note": "全文由 Core 按本快照版本检索；这里只提供有限的上层目录预览，完整目录在作者资料库。不要自行读取其他版本或完整预设。"}
                    data[name] = admin.encode(descriptor)
                    # Constant rules are delivered by Core every turn, not a
                    # prompt to trust that a model chose to read the file.
                result = admin._snapshot(tree, "Writer-Workspace/Context-Snapshot", data, True)
        admin.ui_state(self.core.project, {"context": result["destination"] + "/SNAPSHOT.json"})
        return result

    def context(self, role, query, *, split=False, _view=None):
        view = self.core._use_context_view(role, _view)
        state, manifest, meta, payloads = view[2:6]
        allowed = {r['material_id'] for r in self.records() if r.get('context_enabled', True)}
        fragments, receipt, total = [], [], 0
        stable_fragments, dynamic_fragments = [], []
        with SafeTree(self.core.project) as tree:
            for name in sorted(meta["files"]):
                if not name.startswith(BASE + "/") or not name.endswith(".json"):
                    continue
                descriptor = json.loads(payloads[name])
                if descriptor.get("format") != CONTEXT_FORMAT:
                    continue
                if descriptor.get('material_id') not in allowed:
                    continue
                sha = descriptor["record_sha256"]
                if not isinstance(sha, str) or not re_full_sha(sha):
                    raise CoreError("MATERIAL_CORRUPT", "资料版本引用无效。")
                raw = tree.read(".novel-studio/Material-Records/" + sha + ".json")
                if digest(raw) != sha:
                    raise CoreError("MATERIAL_CORRUPT", "资料版本摘要已变化。")
                record = self.core.parse_json(raw)
                always = selected_sections(record)
                chosen = []
                if record["context_mode"] == "full":
                    chosen = [{"section_id": "full", "start": 0, "end": len(record["text"]), "text": record["text"], "mode": "full"}]
                else:
                    for section in record["sections"]:
                        if section["id"] in always:
                            chosen.append({"section_id": section["id"], "start": section["start"], "end": section["end"],
                                           "text": record["text"][section["start"]:section["end"]], "mode": "always"})
                    background_bytes = 0
                    for piece in search(record, query, cache=self.core._search_cache):
                        if piece["section_id"] in always:
                            continue
                        size = len(piece["text"].encode("utf-8"))
                        if background_bytes + size > SOURCE_LIMIT:
                            break
                        background_bytes += size
                        chosen.append({**piece, "mode": "retrieval"})
                for piece in chosen:
                    total += len(piece["text"].encode("utf-8"))
                    if total > SOURCE_LIMIT:
                        raise CoreError("CONTEXT_LIMIT", "资料合计超过本机 32 MiB 传输保护，未丢弃常驻规则；模型 token 上限由官方执行。")
                    label = {"always": "常驻规则", "retrieval": "按需背景", "full": "完整预设"}[piece["mode"]]
                    fragment = f"\n[资料 {record['source']['name']} · 版本 {record['version']} · {label} · {piece['section_id']}]\n" + piece["text"]
                    fragments.append(fragment)
                    (dynamic_fragments if piece["mode"] == "retrieval" else stable_fragments).append(fragment)
                receipt.append({"material_id": record["material_id"], "name": record["source"]["name"], "version": record["version"],
                                "source_sha256": record["source"]["sha256"], "record_sha256": sha,
                                "context_mode": record["context_mode"], "delivery": "injected_into_request; not_proof_of_understanding",
                                "sections": [{k: p[k] for k in ("section_id", "start", "end", "mode")} for p in chosen],
                                "provided_bytes": sum(len(p["text"].encode("utf-8")) for p in chosen)})
        return ({"stable": "".join(stable_fragments), "dynamic": "".join(dynamic_fragments)} if split else "".join(fragments)), receipt


def re_full_sha(value):
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)
