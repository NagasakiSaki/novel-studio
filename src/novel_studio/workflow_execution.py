"""Incremental execution metadata, separate from immutable approved outline text.

No model calls or file writes here: all changes use the existing WorkflowStore.
Unknown chapter counts remain unknown; scale suggestions never create a quota.
"""
from __future__ import annotations

from .core import CoreError, checked_text
from .workflow_scale import chapter_id
from .workflow_store import WorkflowStore

MAX_CHAPTERS = 1000  # Resource/authorization ceiling, never a literary target.
MAX_VOLUMES = 100


def finished(row):
    return bool(row.get("canonical") or row.get("draft") and row.get("review") and row.get("next_direction"))


class ExecutionService:
    def __init__(self, core, store=None):
        self.core, self.store = core, store or WorkflowStore(core)

    def author_add(self, volume, title=''):
        """Author-created execution entry; approved outline and old identities stay."""
        checked_text(title, 180)
        self.ensure()
        def operation(state, tree):
            run = state['runs'].get(state.get('active_run'))
            if run and run['status'] not in {'COMPLETED', 'STOPPED'}:
                raise CoreError('ACTIVE_RUN', '请先完成或停止当前任务，再添加章节。')
            plan = self.current(state)
            target = next((v for v in plan['volumes'] if v['number'] == volume), None)
            if target is None:
                raise CoreError('NUMBER_INVALID', '请选择已确认大纲中的分卷。')
            number = len([key for key in plan['chapter_ids'] if state['chapters'][key]['volume'] == volume]) + 1
            if chapter_id(self.core.project_id, volume, number) in state['chapters']:
                raise CoreError('EXECUTION_CONFLICT', '下一个编号已被历史章节占用，请先整理目录；原有章节保持不变。')
            ordinal = max((r['ordinal'] for r in state['chapters'].values()), default=0) + 1
            cid = self._add(state, plan, target, {'title':title})
            state['chapters'][cid]['ordinal'] = ordinal
            state['chapters'][cid]['author_added'] = True
            target['closed'] = False
            plan['story_complete'] = False
            return cid
        cid = self.store.mutate('author_chapter_added', operation)
        # Adopt/link uses the same existing scale nodes as generated chapters.
        self.core.workflow.scale.sync()
        return cid

    def current(self, state=None):
        state = state or self.store.read()
        approved = state.get("approved_outline")
        plan = state.get("execution_plans", {}).get(approved["version_id"]) if approved else None
        if plan is None:
            return None
        if not isinstance(plan, dict) or plan.get("format") != "execution-plan-v1" or plan.get("outline_version") != approved["version_id"]:
            raise CoreError("EXECUTION_CONFLICT", "执行计划版本与正式大纲不一致，已停止；未改动正文。")
        ids, volumes = plan.get("chapter_ids"), plan.get("volumes")
        if not isinstance(ids, list) or any(not isinstance(cid, str) for cid in ids) or len(ids) != len(set(ids)) or len(ids) > MAX_CHAPTERS or any(cid not in state["chapters"] for cid in ids):
            raise CoreError("EXECUTION_CONFLICT", "执行目录存在重复或缺失记录，已停止；请作者检查。")
        if not isinstance(volumes, list) or not 1 <= len(volumes) <= MAX_VOLUMES or any(not isinstance(v, dict) or v.get("number") != index for index, v in enumerate(volumes, 1)):
            raise CoreError("EXECUTION_CONFLICT", "执行计划的分卷顺序损坏，已停止。")
        for cid in ids:
            row = state["chapters"][cid]
            if not isinstance(row, dict) or type(row.get("volume")) is not int or not 1 <= row["volume"] <= len(volumes) or type(row.get("number")) is not int or not 1 <= row["number"] <= 1000 or chapter_id(self.core.project_id, row["volume"], row["number"]) != cid:
                raise CoreError("EXECUTION_CONFLICT", "已有卷章身份不一致，已停止；不会重编号或覆盖。")
        return plan

    def rows(self, state=None):
        state = state or self.store.read()
        plan = self.current(state)
        if not plan:
            return []
        return sorted((state["chapters"][cid] for cid in plan["chapter_ids"]), key=lambda r: (r["volume"], r["number"]))

    def _add(self, state, plan, volume, source=None):
        source = source or {}
        number = len([cid for cid in plan["chapter_ids"] if state["chapters"][cid]["volume"] == volume["number"]]) + 1
        if len(plan["chapter_ids"]) >= MAX_CHAPTERS or number > 1000:
            raise CoreError("EXECUTION_LIMIT", "本作品已达到当前目录安全上限；原稿保留，请作者安排后续范围。")
        cid = chapter_id(self.core.project_id, volume["number"], number)
        if cid not in plan["chapter_ids"]:
            plan["chapter_ids"].append(cid)
        row = {"chapter_id": cid, "volume": volume["number"], "volume_title": volume["title"], "number": number,
            "ordinal": 0, "title": source.get("title", ""), "plan": {"number": source.get("number", number), **{key: source.get(key, "") for key in
                ("title", "summary", "events", "characters", "relationships", "foreshadowing", "constraints")}},
            "outline_version": plan["outline_version"], "source_volume_number": volume["source_number"],
            "source_chapter_number": source.get("number"), "title_source": "author" if source.get("title", "").strip() else "pending",
            "execution_generated": not bool(source), "status": "PLANNED", "draft": None, "review": None,
            "next_direction": None, "canonical": None}
        existing = state["chapters"].get(cid)
        if existing is None:
            state["chapters"][cid] = row
        elif not existing.get("draft") and not existing.get("canonical"):
            # Preserve an author's explicit directory-title correction.
            if existing.get("directory_title_edited"):
                row.update(title=existing["title"], title_source="author", directory_title_edited=True)
            existing.update(row)
        return cid

    def _order(self, state, plan):
        for ordinal, row in enumerate(self.rows(state), 1):
            if (row.get("draft") or row.get("canonical")) and row["ordinal"] not in {0, ordinal}:
                raise CoreError("EXECUTION_CONFLICT", "新计划会改变已有稿件顺序，已停止；请作者核对已保存目录。")
            row["ordinal"] = ordinal

    def ensure(self):
        record = self.core.workflow.planning.approved()
        if self.current():
            return self.rows()
        def operation(state, tree):
            if not state.get("approved_outline") or state["approved_outline"]["version_id"] != record["version_id"]:
                raise CoreError("REVISION_CONFLICT", "正式大纲在执行计划初始化期间变化，请重新查看本次范围。")
            plans = state.setdefault("execution_plans", {})
            if record["version_id"] in plans:
                return
            originals = sorted(record["value"]["volumes"], key=lambda r: r["number"])
            plan = {"outline_version": record["version_id"], "volumes": [], "chapter_ids": [],
                "dynamic_volumes": not bool(originals), "story_complete": False, "format": "execution-plan-v1",
                "approved_outline_sha256": state["approved_outline"]["canonical_sha256"]}
            plans[record["version_id"]] = plan
            if not originals:
                originals = [{"number": 1, "title": "", "summary": record["value"]["direction"], "chapters": []}]
            for number, source in enumerate(originals, 1):
                chapters = sorted(source["chapters"], key=lambda r: r["number"])
                volume = {"number": number, "source_number": source["number"], "title": source["title"],
                    "title_source": "author" if source["title"].strip() else "pending", "closed": False,
                    "detailed": bool(chapters), "plan": {k: v for k, v in source.items() if k != "chapters"}}
                plan["volumes"].append(volume)
                for chapter in chapters or [None]:
                    self._add(state, plan, volume, chapter)
            self._order(state, plan)
            for volume in plan["volumes"]:
                volume["closed"] = volume["detailed"] and all(finished(r) for r in self.rows(state) if r["volume"] == volume["number"])
            plan["story_complete"] = not plan["dynamic_volumes"] and all(v["closed"] for v in plan["volumes"])
        self.store.mutate("execution_initialized", operation)
        return self.rows()

    def describe(self, state=None):
        state = state or self.store.read()
        plan = self.current(state)
        if not plan:
            return {"chapters": 0, "volumes": 0, "count_known": False, "story_complete": False}
        return {"chapters": len(plan["chapter_ids"]), "volumes": len(plan["volumes"]),
            "count_known": plan["story_complete"] or not plan["dynamic_volumes"] and all(v["detailed"] or v["closed"] for v in plan["volumes"]),
            "story_complete": plan["story_complete"]}

    def volume(self, chapter, state=None):
        plan = self.current(state)
        return next(v for v in plan["volumes"] if v["number"] == chapter["volume"])

    def apply_metadata(self, state, cid, title, metadata, summary):
        row = state["chapters"][cid]
        volume = self.volume(row, state)
        if row.get("title_source", "author" if row.get("title") else "pending") != "author":
            row.update(title=title, title_source="model" if title else "pending")
        if volume["title_source"] != "author" and metadata.get("volume_title", "").strip():
            volume.update(title=metadata["volume_title"].strip(), title_source="model_suggestion")
        if not volume["title"].strip() and row["title"].strip():
            volume.update(title=row["title"], title_source="first_chapter_suggestion")
        for item in self.rows(state):
            if item["volume"] == row["volume"]:
                item["volume_title"] = volume["title"]
        if row.get("execution_generated"):
            row["plan"].update(title=row["title"], summary=summary, events=metadata.get("chapter_goal", summary))
        row["execution_metadata"] = metadata

    def extend_run(self, state, run, cid):
        """Reconcile the finite authorized queue after one durable chapter.

        Only append after the current volume; stable IDs and completed ordinals
        are never reassigned. Detailed author chapter lists are authoritative.
        """
        plan = self.current(state)
        row = state["chapters"][cid]
        volume = self.volume(row, state)
        rows = self.rows(state)
        tail = max(r["number"] for r in rows if r["volume"] == row["volume"])
        metadata = row.get("execution_metadata", {})
        if row["number"] == tail:
            if volume["detailed"] or metadata.get("volume_complete") or metadata.get("book_complete"):
                volume["closed"] = True
        # A model cannot declare an eight-volume outline complete in volume one.
        if plan["dynamic_volumes"] and metadata.get("book_complete"):
            plan["story_complete"] = True
        elif not plan["dynamic_volumes"] and all(v["closed"] for v in plan["volumes"]):
            plan["story_complete"] = True
        limit = run["execution_scope"]["chapter_limit"]
        remaining = limit - len(run["completed_chapters"])
        if remaining > 0 and not plan["story_complete"] and len(plan["chapter_ids"]) < MAX_CHAPTERS:
            if not volume["detailed"] and not volume["closed"]:
                self._add(state, plan, volume)
            elif plan["dynamic_volumes"] and volume["closed"]:
                number = volume["number"] + 1
                if number <= MAX_VOLUMES:
                    following = {"number": number, "source_number": None, "title": "", "title_source": "pending",
                        "closed": False, "detailed": False, "plan": {}}
                    plan["volumes"].append(following)
                    self._add(state, plan, following)
        # A chapter-count run can end mid-volume; prepare its next slot locally
        # for the next explicitly authorized run, without another model request.
        if remaining <= 0 and not plan["story_complete"] and not volume["closed"] and not volume["detailed"] and len(plan["chapter_ids"]) < MAX_CHAPTERS:
            self._add(state, plan, volume)
        elif remaining <= 0 and not plan["story_complete"] and plan["dynamic_volumes"] and volume["closed"] and len(plan["chapter_ids"]) < MAX_CHAPTERS and volume["number"] < MAX_VOLUMES:
            following = {"number": volume["number"] + 1, "source_number": None, "title": "", "title_source": "pending", "closed": False, "detailed": False, "plan": {}}
            plan["volumes"].append(following)
            self._add(state, plan, following)
        self._order(state, plan)
        scope = run["execution_scope"]
        available = [r for r in self.rows(state) if not finished(r) and
            (scope["volumes"] is None or r["volume"] in scope["volumes"])]
        run["chapter_ids"] = run["completed_chapters"] + [r["chapter_id"] for r in available[:max(remaining, 0)]]
        run["chapter_count"] = len(run["chapter_ids"])
        run["index"] = len(run["completed_chapters"])
        if remaining <= 0 or not available or plan["story_complete"]:
            run["status"] = "COMPLETED"
            run["completion_reason"] = "story_complete" if plan["story_complete"] else "authorized_range"

    def retitle(self, cid, title, volume_title=None):
        checked_text(title, 180)
        if not title.strip():
            raise CoreError("TITLE_REQUIRED", "请填写希望使用的目录标题。")
        if volume_title is not None:
            checked_text(volume_title, 180)
        def operation(state, tree):
            row = state["chapters"].get(cid)
            if row is None:
                raise CoreError("CHAPTER_REQUIRED", "请先选择章节。")
            row.update(title=title.strip(), title_source="author", directory_title_edited=True)
            if volume_title is not None:
                volume = self.volume(row, state)
                volume.update(title=volume_title.strip(), title_source="author")
                for item in self.rows(state):
                    if item["volume"] == row["volume"]:
                        item["volume_title"] = volume["title"]
        self.store.mutate("execution_author_rename", operation)
