"""Four workflow categories are separate from the existing aesthetic A/B/C."""
from __future__ import annotations

import json
from .core import CoreError, admin, checked_text, digest
from .material_search import search
from .workflow_store import BASE, WorkflowStore
from .workflow_planning import object_schema, text_fields

DIRECTION_FIELDS = ("plot_goal", "events", "characters", "relationships", "threads", "foreshadowing", "world_constraints", "mood_pace", "avoid")
ISSUE_SCHEMA = object_schema({**text_fields(("title", "quote", "basis", "recommendation")),
    "kind": {"type": "string", "enum": ["world", "identity", "timeline", "corruption", "key_plot", "other"]},
    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
    "certainty": {"type": "string", "enum": ["supported", "uncertain"]},
    "start_line": {"type": "integer"}, "end_line": {"type": "integer"}})
NOTE_SCHEMA = object_schema({**text_fields(("topic", "text")),
    "kind": {"type": "string", "enum": ["world", "character", "thread", "style", "timeline"]},
    "scope": {"type": "string", "enum": ["always", "relevant", "next"]},
    "expires_after_chapter": {"type": "integer"}})
DIRECTION_SCHEMA = object_schema({**text_fields(DIRECTION_FIELDS), "suggested_words": {"type": "integer"}})
REVIEW_SCHEMA = object_schema({"summary": {"type": "string"},
    "current_objective": {"type": "array", "items": ISSUE_SCHEMA},
    "current_quality": {"type": "array", "items": {"type": "string"}},
    "future_notes": {"type": "array", "items": NOTE_SCHEMA}, "next_direction": DIRECTION_SCHEMA})
DIRECTION_LABELS = {"plot_goal": "剧情目标", "events": "主要事件", "characters": "重点人物", "relationships": "关系变化", "threads": "主线与支线",
    "foreshadowing": "伏笔处理", "world_constraints": "世界观约束", "mood_pace": "情绪与节奏", "suggested_words": "责编篇幅建议（以正式大纲和叙事需要为准）", "avoid": "需要避免"}


def direction_text(direction):
    return "\n\n".join(label + "：\n" + str(direction.get(key, "尚未提供")) for key, label in DIRECTION_LABELS.items())


def report_text(check):
    value = check["value"]
    parts = ["# 责编审稿意见\n", value["summary"], "\n## A · 本章客观问题"]
    for issue, quote in zip(value["current_objective"], check["quote_checks"]):
        parts.append(f"### {issue['title']}\n\n原文第{issue['start_line']}—{issue['end_line']}行：\n> {issue['quote']}\n\n引文核对：{'匹配' if quote['quote_matches'] else '未匹配，须作者检查'}\n依据：{issue['basis']}\n建议：{issue['recommendation']}\n确定程度：{'有依据的判断，仍需核对推论' if issue['certainty'] == 'supported' else '尚不确定，交作者裁决'}")
    if not value["current_objective"]:
        parts.append("本次未列出客观问题；不等于保证不存在问题。")
    parts += ["\n## B · 本章质量建议", "\n".join("- " + row for row in value["current_quality"]) or "未列出。",
              "\n## C · 后续章节注意", "\n".join("- " + row["topic"] + "：" + row["text"] for row in value["future_notes"]) or "未列出。",
              "\n## D · 下一章方向", direction_text(value["next_direction"]),
              "\n以上是责编建议；在作者授权的连续创作范围内，重要问题可触发最多两轮草稿返修，不等于作者批准或正典变更。"]
    return "\n\n".join(parts)


def validate_review(value, draft):
    if not isinstance(value, dict) or set(value) != set(REVIEW_SCHEMA["properties"]):
        raise CoreError("REVIEW_INVALID", "审稿结果需要分别包含本章客观问题、本章质量建议、未来注意和下一章方向。")
    checked_text(value["summary"], 20_000)
    if not isinstance(value["current_objective"], list) or len(value["current_objective"]) > 40:
        raise CoreError("REVIEW_INVALID", "客观问题列表不正确或过多。")
    checks, severe, repairs, decisions = [], [], [], []
    lines = draft.splitlines()
    for issue in value["current_objective"]:
        if not isinstance(issue, dict) or set(issue) != set(ISSUE_SCHEMA["properties"]):
            raise CoreError("REVIEW_INVALID", "审稿问题字段不完整。")
        for key in ("title", "quote", "basis", "recommendation"):
            checked_text(issue[key], 8000)
        if issue["kind"] not in ISSUE_SCHEMA["properties"]["kind"]["enum"] or issue["severity"] not in {"high", "medium", "low"} or issue["certainty"] not in {"supported", "uncertain"}:
            raise CoreError("REVIEW_INVALID", "审稿的事实类别、严重程度或确定程度不受支持。")
        a, b = issue["start_line"], issue["end_line"]
        verified = (type(a) is int and type(b) is int and 1 <= a <= b <= len(lines) and bool(issue["quote"].strip())
                    and issue["quote"] in "\n".join(lines[a - 1:b]))
        checks.append({"title": issue["title"], "quote_matches": verified, "start_line": a, "end_line": b,
                       "note": "引文匹配不证明文学判断或事实推论正确。"})
        if issue["severity"] in {"medium", "high"}:
            if verified and issue["certainty"] == "supported":
                repairs.append(issue)
            elif issue["severity"] == "high" and issue["kind"] in {"world", "identity", "timeline", "corruption", "key_plot"}:
                decisions.append({"title": issue["title"], "quote_matches": verified, "certainty": issue["certainty"],
                    "reason": "重要判断证据未核实或仍不确定，不能据此自动改稿。"})
        if issue["severity"] == "high" and issue["kind"] in {"world", "identity", "timeline", "corruption", "key_plot"}:
            severe.append({"title": issue["title"], "quote_matches": verified, "certainty": issue["certainty"],
                           "reason": "责编标记潜在严重问题；依据引文核验、确定程度和作者授权决定草稿返修或作者裁决。"})
    if not isinstance(value["current_quality"], list) or len(value["current_quality"]) > 40:
        raise CoreError("REVIEW_INVALID", "质量建议列表不正确。")
    for text in value["current_quality"]:
        checked_text(text, 6000)
    if not isinstance(value["future_notes"], list) or len(value["future_notes"]) > 24:
        raise CoreError("REVIEW_INVALID", "后续指导应精简，最多 24 条。")
    for note in value["future_notes"]:
        validate_note(note)
    direction = value["next_direction"]
    if not isinstance(direction, dict) or set(direction) != set(DIRECTION_SCHEMA["properties"]):
        raise CoreError("REVIEW_INVALID", "下一章方向字段不完整。")
    for key in DIRECTION_FIELDS:
        checked_text(direction[key], 6000)
    if type(direction["suggested_words"]) is not int or not 0 <= direction["suggested_words"] <= 100_000:
        raise CoreError("REVIEW_INVALID", "下一章建议字数不正确；作者设置优先。")
    return {"value": json.loads(json.dumps(value, ensure_ascii=False)), "quote_checks": checks, "serious_flags": severe,
        "repair_flags": repairs, "author_flags": decisions,
        "categories": {"A": "current_objective", "B": "current_quality", "C": "future_notes", "D": "next_direction"}}


def validate_note(note):
    if not isinstance(note, dict) or set(note) != set(NOTE_SCHEMA["properties"]):
        raise CoreError("GUIDANCE_INVALID", "后续指导字段不完整。")
    for key in ("topic", "text"):
        checked_text(note[key], 4000)
        if not note[key].strip():
            raise CoreError("GUIDANCE_INVALID", "指导主题与内容不能为空。")
    if note["kind"] not in NOTE_SCHEMA["properties"]["kind"]["enum"] or note["scope"] not in {"always", "relevant", "next"}:
        raise CoreError("GUIDANCE_INVALID", "指导适用方式不正确。")
    if type(note["expires_after_chapter"]) is not int or not 0 <= note["expires_after_chapter"] <= 1000:
        raise CoreError("GUIDANCE_INVALID", "指导有效章节范围不正确。")
    return dict(note)


class GuidanceService:
    def __init__(self, core, store=None):
        self.core, self.store = core, store or WorkflowStore(core)

    def ingest(self, notes, chapter, report):
        notes = [validate_note(note) for note in notes]
        def operation(state, tree):
            event_id = digest(admin.encode([chapter["chapter_id"], report, notes]))
            applied = state.setdefault("guidance_ingestions", {})
            if event_id in applied:
                return applied[event_id]
            refs = []
            for note in notes:
                gid = digest((note["kind"] + ":" + note["topic"].strip().casefold()).encode())[:32]
                old = state["guidance"].get(gid)
                if old and old.get("locked") and old["note"] != note:
                    raise CoreError("GUIDANCE_CONFLICT", "责编建议改变作者锁定的指导，请作者先检查。")
                version = (old["version"] if old else 0) + 1
                record = {"guidance_id": gid, "version": version, "note": note, "source_chapter": chapter["chapter_id"],
                    "source_report": report, "from_ordinal": chapter["ordinal"] + 1, "status": "active", "locked": old.get("locked", False) if old else False,
                    "authority": "model_advice_not_canon"}
                reference = self.store.put(tree, BASE + f"/Guidance/{gid}/v{version}.json", record)
                state["guidance"][gid] = {**record, **reference}
                refs.append({"guidance_id": gid, "version": version})
            applied[event_id] = refs
            return refs
        return self.store.mutate("guidance_saved", operation)

    def update(self, gid, note=None, *, active=True, locked=None):
        from .workflow_store import identifier
        identifier(gid)
        def operation(state, tree):
            old = state["guidance"].get(gid)
            if old is None:
                raise CoreError("GUIDANCE_INVALID", "所选指导不存在。")
            record = {key: value for key, value in old.items() if key not in {"path", "sha256"}}
            record.update(version=old["version"] + 1, status="active" if active else "disabled", authority="author_selected_advice")
            if note is not None:
                record["note"] = validate_note(note)
            if locked is not None:
                record["locked"] = bool(locked)
            reference = self.store.put(tree, BASE + f"/Guidance/{gid}/v{record['version']}.json", record)
            state["guidance"][gid] = {**record, **reference}
        self.store.mutate("guidance_author_update", operation)

    def select(self, ordinal, query):
        records = self.store.read()["guidance"]
        active = []
        for gid, record in records.items():
            if record["status"] != "active" or ordinal < record["from_ordinal"]:
                continue
            note = record["note"]
            expires = note["expires_after_chapter"]
            if expires and ordinal > expires or note["scope"] == "next" and ordinal != record["from_ordinal"]:
                continue
            # Verify the immutable source for every selected candidate.
            self.store.blob(record)
            active.append((gid, record))
        required = [record for gid, record in active if record["note"]["scope"] in {"always", "next"} or record.get("locked")]
        optional = [(gid, record) for gid, record in active if record not in required]
        text, sections, by_id = "", [], {}
        for gid, record in optional:
            start = len(text)
            text += record["note"]["topic"] + "\n" + record["note"]["text"] + "\n"
            sections.append({"id": gid, "title": record["note"]["topic"], "start": start, "end": len(text)})
            by_id[gid] = record
        chosen = list(required)
        if sections:
            for hit in search({"text": text, "sections": sections}, query, limit=12):
                if by_id[hit["section_id"]] not in chosen:
                    chosen.append(by_id[hit["section_id"]])
        output = [{"guidance_id": r["guidance_id"], "version": r["version"], "note": r["note"],
                   "source_report": r["source_report"], "authority": r["authority"]} for r in chosen]
        if len(output) > 40 or len(admin.encode(output)) > 32_000:
            raise CoreError("GUIDANCE_LIMIT", "仍生效的必要指导较多，请作者整理或设置失效范围；未静默删除必要规则。")
        return output
