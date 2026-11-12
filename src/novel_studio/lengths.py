"""Author book/volume/chapter/scene goals with one explicit counting policy."""
from __future__ import annotations

import math
import uuid
from .core import CoreError, SafeTree, checked_text, digest
from .creative import CreativeService
from .text_metrics import measure, METRICS

KINDS = {"book": "全书", "volume": "分卷", "chapter": "章节", "scene": "场景"}
PARENTS = {"volume": {"book"}, "chapter": {"book", "volume"}, "scene": {"chapter"}}
DEFAULT_GOAL = {"minimum": None, "target": None, "maximum": None, "deviation_percent": 10, "strict": False, "inherit": False}


def validate_goal(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_GOAL):
        raise CoreError("GOAL_INVALID", "字数目标字段不完整。")
    for key in ("minimum", "target", "maximum"):
        if value[key] is not None and (type(value[key]) is not int or not 1 <= value[key] <= 20000000):
            raise CoreError("GOAL_INVALID", "目标字数应为正整数，或保留为未设置。")
    if type(value["deviation_percent"]) not in {int, float} or not 0 <= value["deviation_percent"] <= 100 or type(value["strict"]) is not bool or type(value["inherit"]) is not bool:
        raise CoreError("GOAL_INVALID", "允许偏差、严格程度或继承选项不正确。")
    values = [value[k] for k in ("minimum", "target", "maximum") if value[k] is not None]
    if values != sorted(values):
        raise CoreError("GOAL_INVALID", "最低字数、目标字数和最高字数应从小到大排列。")
    return dict(value)


def evaluate(count, goal):
    target, low, high = (goal[k] for k in ("target", "minimum", "maximum"))
    if target is not None:
        low = low if low is not None else math.floor(target * (1 - goal["deviation_percent"] / 100))
        high = high if high is not None else math.ceil(target * (1 + goal["deviation_percent"] / 100))
    status = "UNSET" if low is None and high is None else "UNDER" if low is not None and count < low else "OVER" if high is not None and count > high else "WITHIN"
    return {"count": count, "target": target, "minimum": low, "maximum": high, "status": status, "strict": goal["strict"],
            "difference": count - target if target is not None else None, "remaining": max(target - count, 0) if target is not None else None,
            "progress": count / target if target else None,
            "action": "由作者决定续写、压缩或接受当前篇幅；不自动凑字数、删稿或修改定稿。"}


class LengthService:
    def __init__(self, core):
        self.core = core
        self.creative = CreativeService(core)

    def plan(self):
        return self.creative.state().get("length_plan", {"format": "novel-studio-length-plan-v1", "metric": "novel_visible_chars_v1", "active": "book",
            "nodes": {"book": {"kind": "book", "title": "全书", "parent": None, "goal": dict(DEFAULT_GOAL), "source": None}}})

    def _change(self, operation):
        def mutate(tree, state):
            plan = state.setdefault("length_plan", self.plan())
            return operation(plan)
        return self.creative._mutate(mutate)

    def set_metric(self, metric):
        if metric not in METRICS:
            raise CoreError("GOAL_INVALID", "请选择本程序支持的字数统计方式。")
        return self._change(lambda plan: plan.update(metric=metric))

    def add(self, kind, title, parent="book"):
        checked_text(title, 180)
        if kind not in PARENTS or not title.strip():
            raise CoreError("GOAL_INVALID", "请选择分卷、章节或场景并填写名称。")
        node_id = uuid.uuid4().hex
        def operation(plan):
            if parent not in plan["nodes"] or plan["nodes"][parent]["kind"] not in PARENTS[kind] or len(plan["nodes"]) >= 10000:
                raise CoreError("GOAL_INVALID", "所选上级不适合此层级，或规划条目过多。")
            order = max((n.get("order", 0) for n in plan["nodes"].values() if n["parent"] == parent), default=-1) + 1
            plan["nodes"][node_id] = {"kind": kind, "title": title, "parent": parent, "order": order, "goal": dict(DEFAULT_GOAL), "source": None}
            return node_id
        return self._change(operation)

    def goal(self, node_id, value):
        value = validate_goal(value)
        def operation(plan):
            if node_id not in plan["nodes"]:
                raise CoreError("GOAL_INVALID", "规划条目不存在。")
            if node_id == "book" and value["inherit"]:
                raise CoreError("GOAL_INVALID", "全书没有可继承的上级。")
            plan["nodes"][node_id]["goal"] = value
            if "workflow_chapter_id" in plan["nodes"][node_id]:
                plan["nodes"][node_id]["workflow_default_goal"] = False
        return self._change(operation)

    def activate(self, node_id):
        def operation(plan):
            if node_id not in plan["nodes"]:
                raise CoreError("GOAL_INVALID", "规划条目不存在。")
            plan["active"] = node_id
        return self._change(operation)

    def link(self, node_id, document_id, start_line=None, end_line=None):
        row = self.core.document(document_id)
        if row["area"] not in {"drafts", "canonical"}:
            raise CoreError("GOAL_INVALID", "只能统计本作品的草稿或正式章节，不把资料和审稿报告算作正文。")
        source = {"document_id": document_id, "revision": row["revision"]}
        if start_line is not None or end_line is not None:
            if type(start_line) is not int or type(end_line) is not int or not 1 <= start_line <= end_line <= len(row["text"].splitlines()):
                raise CoreError("GOAL_INVALID", "场景行号范围不正确。")
            source.update(start_line=start_line, end_line=end_line)
        def operation(plan):
            node = plan["nodes"].get(node_id)
            if node is None or node["kind"] not in {"chapter", "scene"}:
                raise CoreError("GOAL_INVALID", "请选章节或场景关联正文版本。")
            if node["kind"] == "scene" and "start_line" not in source:
                raise CoreError("GOAL_INVALID", "场景应关联明确行号范围，不重复计整章正文。")
            if node["kind"] == "chapter" and "start_line" in source:
                raise CoreError("GOAL_INVALID", "章节统计整个选定版本；片段请关联场景。")
            if node["kind"] == "chapter" and any(k != node_id and n["kind"] == "chapter" and (n.get("source") or {}).get("document_id") == document_id for k, n in plan["nodes"].items()):
                raise CoreError("DUPLICATE_SOURCE", "同一个正文版本已经关联另一章节，已阻止重复累计。")
            node["source"] = source
        return self._change(operation)

    def resolved_goal(self, node_id, plan=None):
        plan = plan or self.plan()
        visited = set()
        while node_id in plan["nodes"] and node_id not in visited:
            visited.add(node_id)
            node = plan["nodes"][node_id]
            if not node["goal"].get("inherit"):
                return {"source_node": node_id, **validate_goal(node["goal"])}
            node_id = node["parent"]
        raise CoreError("GOAL_INVALID", "目标继承存在循环或缺少上级。")

    def counts(self, documents=None, *, _cancel=None):
        plan = self.plan()
        readings = {}
        available = {r["document_id"]: r for r in (self.core.documents() if documents is None else documents) if r["area"] in {"drafts", "canonical"}}
        cache = {}
        for node_id, node in plan["nodes"].items():
            if _cancel is not None and _cancel.is_set():
                raise CoreError('VIEW_CANCELLED', '本地字数加载已停止，作品文件没有改变。')
            if node["kind"] not in {"chapter", "scene"}:
                continue
            source = node.get("source")
            if not source:
                readings[node_id] = {"count": 0, "status": "UNBOUND", "revision": None}
                continue
            try:
                row = available.get(source["document_id"])
                if row is None:
                    raise CoreError("GOAL_INVALID", "统计源已不再属于正文。")
                if source["document_id"] not in cache:
                    with SafeTree(self.core.project) as tree:
                        payload = tree.read(row["relative"])
                    if digest(payload) != row["revision"]:
                        raise CoreError("REVISION_CONFLICT", "正文在统计期间变化，请重新统计。")
                    cache[source["document_id"]] = payload.decode("utf-8-sig")
                text = cache[source["document_id"]]
                if node["kind"] == "scene":
                    if row["revision"] != source["revision"]:
                        readings[node_id] = {"count": 0, "status": "STALE_RANGE", "revision": row["revision"]}
                        continue
                    text = "\n".join(text.splitlines()[source["start_line"] - 1:source["end_line"]])
                value = self.core.measure_text(text, plan["metric"])
                readings[node_id] = {"count": value["count"], "status": "MEASURED", "revision": row["revision"]}
            except (CoreError, ValueError, OSError):
                readings[node_id] = {"count": 0, "status": "SOURCE_UNAVAILABLE", "revision": None}
        def descendants(parent, trail=()):
            if parent in trail:
                raise CoreError("GOAL_INVALID", "章节规划存在循环。")
            values = []
            for child_id, child in plan["nodes"].items():
                if child["parent"] == parent:
                    values.append(child_id)
                    values.extend(descendants(child_id, (*trail, parent)))
            return values
        result = {}
        for node_id, node in plan["nodes"].items():
            if node["kind"] in {"chapter", "scene"}:
                count = readings[node_id]["count"]
                missing = [] if readings[node_id]["status"] == "MEASURED" else [{"node_id": node_id, "status": readings[node_id]["status"]}]
            else:
                chapters = [key for key in descendants(node_id) if plan["nodes"][key]["kind"] == "chapter"]
                count = sum(readings[key]["count"] for key in chapters)
                missing = [{"node_id": key, "status": readings[key]["status"]} for key in chapters if readings[key]["status"] != "MEASURED"]
            goal = self.resolved_goal(node_id, plan)
            result[node_id] = {**evaluate(count, goal), "goal_source_node": goal["source_node"], "missing_sources": missing,
                               "measured_complete": not missing, "scope": "author-selected chapter versions; drafts revisions never auto-summed"}
        return {"metric": plan["metric"], "description": METRICS[plan["metric"]], "nodes": result}

    def context(self, document_id=None):
        plan = self.plan()
        selected = next((key for key, node in plan["nodes"].items() if document_id is not None and node["kind"] == "chapter" and (node.get("source") or {}).get("document_id") == document_id), plan["active"])
        active = plan["nodes"][plan["active"]]
        if active["kind"] == "scene" and ((active.get("source") or {}).get("document_id") == document_id or
                (plan["nodes"][active["parent"]].get("source") or {}).get("document_id") == document_id):
            selected = plan["active"]
        node = plan["nodes"][selected]
        return {"node_id": selected, "level": KINDS[node["kind"]], "kind": node["kind"], "title": node["title"], "metric": plan["metric"], "metric_description": METRICS[plan["metric"]],
                "goal": self.resolved_goal(selected, plan), "instruction": "篇幅仅为创作参考。优先级：作者确认大纲 > 当前执行计划 > 全局篇幅建议 > 软件默认值。全书/分卷建议不能当成单次草稿配额；不凑字、不截断，不因偏离字数阻断保存或审稿。"}

    def check_artifact(self, text, document_id=None):
        goal = self.context(document_id)
        count = measure(text, goal["metric"])["count"]
        effective = goal["goal"] if goal["kind"] in {"chapter", "scene"} else DEFAULT_GOAL
        return {**evaluate(count, effective), "metric": goal["metric"], "level": goal["level"],
                "scope": "new artifact only; book/volume goals never applied as per-artifact length"}
