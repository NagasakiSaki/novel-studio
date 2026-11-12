"""Append-only local author transcripts, separate from prompts and diagnostics."""
import json
import re
import uuid
from .core import CoreError, SafeTree, admin, digest, TEXT_BUDGET_BYTES


def checked_pointer(value):
    if not isinstance(value, dict) or set(value) != {"id", "revision"} or not isinstance(value["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["id"]) or not isinstance(value["revision"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["revision"]):
        raise CoreError("TRANSCRIPT_CHANGED", "本地对话历史索引格式不正确。")
    return value


def append(models, role, entry):
    previous = models.preferences(role)
    pointer = previous.get("transcript_head")
    if pointer:
        checked_pointer(pointer)
    identifier = uuid.uuid4().hex
    payload = admin.encode({"format": "novel-studio-transcript-v1", "role": role, "entry": entry, "previous": pointer,
        "legacy": [] if pointer else previous.get("history", [])})
    with models.core.mutex, SafeTree(models.core.project, limit=TEXT_BUDGET_BYTES) as tree:
        with tree.lock():
            folder = ".novel-studio/Transcripts/" + role
            tree.mkdir(folder)
            tree.create(folder + "/" + identifier + ".json", payload)
        # Role-turn locks serialize the callers; publication of the new head is
        # atomic. A failure leaves an unreferenced record, not a deleted history.
        visible = previous.get("history", []) + [entry]
        while len(admin.encode(visible)) > 500_000 and len(visible) > 1:
            visible.pop(0)
        return models.save(role, history=visible[-15:], transcript_head={"id": identifier, "revision": digest(payload)})


def page(models, role, cursor=None, limit=20):
    preferences = models.preferences(role)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise CoreError("TRANSCRIPT_CHANGED", "对话历史分页大小不正确。")
    head = preferences.get("transcript_head")
    if cursor is None and not head:
        return {"entries": list(reversed(preferences.get("history", [])))[:limit], "cursor": None, "scope": "legacy visible history only; earlier trimmed entries cannot be reconstructed"}
    current = cursor or {"node": head, "offset": 0}
    entries, visited = [], set()
    with SafeTree(models.core.project, limit=TEXT_BUDGET_BYTES) as tree:
        while current and len(entries) < limit:
            if not isinstance(current, dict) or set(current) != {"node", "offset"} or type(current["offset"]) is not int or current["offset"] < 0:
                raise CoreError("TRANSCRIPT_CHANGED", "历史分页位置不正确。")
            pointer = checked_pointer(current["node"])
            if pointer["id"] in visited:
                raise CoreError("TRANSCRIPT_CHANGED", "历史索引出现循环，已停止读取。")
            visited.add(pointer["id"])
            raw = tree.read(".novel-studio/Transcripts/" + role + "/" + pointer["id"] + ".json")
            if digest(raw) != pointer["revision"]:
                raise CoreError("TRANSCRIPT_CHANGED", "本地历史记录已变化，已停止读取。")
            record = json.loads(raw)
            if record.get("role") != role or record.get("format") != "novel-studio-transcript-v1":
                raise CoreError("TRANSCRIPT_CHANGED", "历史记录角色不匹配。")
            items = [record["entry"], *reversed(record["legacy"])]
            start = current["offset"]
            take = items[start:start + limit - len(entries)]
            entries.extend(take)
            end = start + len(take)
            current = {"node": pointer, "offset": end} if end < len(items) else {"node": record["previous"], "offset": 0} if record["previous"] else None
    return {"entries": entries, "cursor": current, "scope": "local author transcript, newest first; not native provider request history"}
