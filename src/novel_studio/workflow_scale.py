"""Author inputs remain unchanged; derived scale and local progress are explicit."""
from __future__ import annotations

import math
from .core import CoreError, digest
from .lengths import DEFAULT_GOAL
from .workflow_store import DEFAULT_SCALE, WorkflowStore


def validate_scale(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_SCALE):
        raise CoreError("SCALE_INVALID", "作品规模设置字段不完整。")
    maximums = {"chapter_target": 100_000, "chapter_minimum": 100_000, "chapter_maximum": 100_000,
        "chapters_per_volume": 200, "total_words": 100_000_000, "total_chapters": 1000, "total_volumes": 100}
    for key, maximum in maximums.items():
        n = value[key]
        if n is None and key in {"total_words", "total_chapters", "total_volumes"}:
            continue
        if type(n) is not int or not 1 <= n <= maximum:
            raise CoreError("SCALE_INVALID", "请填写范围内的正整数；未确定的全书目标可以留空。")
    if not value["chapter_minimum"] <= value["chapter_target"] <= value["chapter_maximum"]:
        raise CoreError("SCALE_INVALID", "每章最低、目标和最高字数应从小到大排列。")
    return dict(value)


def derive(value):
    value = validate_scale(value)
    chapter, per_volume = value["chapter_target"], value["chapters_per_volume"]
    chapters = value["total_chapters"]
    basis = "作者填写的全书章节数"
    if chapters is None and value["total_words"]:
        chapters, basis = math.ceil(value["total_words"] / chapter), "全书目标字数 ÷ 每章目标，向上取整"
    if chapters is None and value["total_volumes"]:
        chapters, basis = value["total_volumes"] * per_volume, "全书卷数 × 每卷默认章节数"
    volumes = math.ceil(chapters / per_volume) if chapters else None
    words = chapters * chapter if chapters else None
    warnings = []
    if value["total_words"] is not None and words is not None and value["total_words"] != words:
        warnings.append(f"全书目标 {value['total_words']} 字，与 {chapters} 章 × {chapter} 字 = {words} 字不同；未修改您的输入。")
    if value["total_volumes"] is not None and volumes is not None and value["total_volumes"] != volumes:
        warnings.append(f"按 {chapters} 章、每卷 {per_volume} 章预计 {volumes} 卷，与作者填写的 {value['total_volumes']} 卷不同。")
    return {"estimated_words": words, "estimated_chapters": chapters, "estimated_volumes": volumes,
        "words_per_full_volume": chapter * per_volume, "last_volume_chapters": ((chapters - 1) % per_volume + 1) if chapters else None,
        "basis": basis, "warnings": warnings, "inputs_unchanged": value}


def chapter_id(project_id, volume, chapter):
    return digest(f"{project_id}:volume:{volume}:chapter:{chapter}".encode())[:32]


class ScaleService:
    def __init__(self, core, store=None):
        self.core, self.store = core, store or WorkflowStore(core)

    def get(self):
        return validate_scale(self.store.read()["scale"])

    def save(self, value):
        value = validate_scale(value)
        self.store.mutate("scale_saved", lambda s, t: s.update(scale=value))
        if self.store.read()["approved_outline"]:
            self.sync()
        return derive(value)

    def sync(self):
        """Reuse the existing four-level plan, with explicit default/override flags."""
        record = self.core.workflow.planning.approved()
        chapters = self.core.workflow.execution.ensure()
        execution = self.core.workflow.execution.current()
        scale = self.get()
        default = {**DEFAULT_GOAL, "minimum": scale["chapter_minimum"], "target": scale["chapter_target"], "maximum": scale["chapter_maximum"]}
        def operation(plan):
            book_target = scale["total_words"] or (len(chapters) * scale["chapter_target"] if self.core.workflow.execution.describe()["count_known"] else None)
            plan["nodes"]["book"]["goal"] = {**DEFAULT_GOAL, "target": book_target}
            for volume in execution["volumes"]:
                vid = digest(f"{self.core.project_id}:volume:{volume['number']}".encode())[:32]
                volume_node = plan["nodes"].setdefault(vid, {"kind": "volume", "parent": "book", "source": None, "goal": dict(DEFAULT_GOAL)})
                volume_node.update(title=f"第{volume['number']}卷 {volume['title']}", order=volume["number"] - 1, workflow_volume=volume["number"])
                for chapter in [c for c in chapters if c["volume"] == volume["number"]]:
                    cid = chapter_id(self.core.project_id, volume["number"], chapter["number"])
                    node = plan["nodes"].setdefault(cid, {"kind": "chapter", "parent": vid, "source": None, "goal": default.copy(), "workflow_default_goal": True})
                    if node.get("workflow_default_goal", False):
                        node["goal"] = default.copy()
                    node.update(title=f"第{chapter['number']}章 {chapter['title']}", parent=vid, order=chapter["number"] - 1,
                        workflow_chapter_id=cid, outline_version=record["version_id"])
        self.core.lengths._change(operation)

    def goal(self, cid):
        plan = self.core.lengths.plan()
        if cid not in plan["nodes"]:
            self.sync()
        return self.core.lengths.resolved_goal(cid)

    def progress(self):
        state = self.store.read()
        counts = self.core.lengths.counts()
        approved = [row for row in state["chapters"].values() if row.get("canonical")]
        goals = derive(self.get())
        written = sum(1 for row in state["chapters"].values() if row.get("draft"))
        actual = self.core.workflow.execution.describe(state)
        return {**goals, "suggested_scale": goals, "execution": actual,
            "estimated_chapters": actual["chapters"] if actual["count_known"] else None,
            "estimated_volumes": actual["volumes"] if state["approved_outline"] else None,
            "local_counts": counts, "written_chapters": written, "approved_chapters": len(approved),
            "remaining_chapters": max(actual["chapters"] - written, 0) if actual["count_known"] else None,
            "note": "本地字数口径含标点，排除空白和 Markdown 标记；待确认草稿与正式收录分别计数。"}
