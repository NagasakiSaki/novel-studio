"""Chapter numbering and author-only acceptance reuse Canonical transactions."""
from __future__ import annotations

import json
from pathlib import Path
import re
import secrets
import time
import uuid
from .core import CoreError, SafeTree, admin, digest
from .workflow_store import BASE, WorkflowStore, identifier
from .workflow_scale import chapter_id
from .text_metrics import measure
from .lengths import evaluate


def formal_target(volume, chapter):
    if type(volume) is not int or type(chapter) is not int or not 1 <= volume <= 100 or not 1 <= chapter <= 1000:
        raise CoreError("NUMBER_INVALID", "正式卷章编号超出范围。")
    return f"Canonical/Final-Manuscript/Volume-{volume:03d}/Chapter-{chapter:03d}.md"


class ChapterService:
    def __init__(self, core, store=None):
        self.core, self.store = core, store or WorkflowStore(core)

    def planned(self):
        return self.core.workflow.execution.ensure()

    def ensure(self):
        self.planned()
        self.core.workflow.scale.sync()
        return self.catalog()

    def catalog(self):
        return sorted(self.store.read()["chapters"].values(), key=lambda r: r["ordinal"])

    def checked_document(self, reference, area):
        if not isinstance(reference, dict):
            raise CoreError("CHAPTER_SOURCE_MISSING", "章节缺少已保存的来源。")
        row = self.core.document(reference.get("document_id"))
        if row["area"] != area or row["revision"] != reference.get("revision"):
            raise CoreError("CHAPTER_SOURCE_CHANGED", "章节或报告版本已变化，请明确采用新版本并重新审核。")
        if area == "canonical" and not row["relative"].startswith("Canonical/Final-Manuscript/"):
            raise CoreError("CHAPTER_SOURCE_CHANGED", "所选正式文件不是章节正文。")
        return row

    def unindexed(self, documents=None):
        used = {(row.get("canonical") or {}).get("document_id") for row in self.catalog()}
        rows = []
        for row in self.core.documents() if documents is None else documents:
            path = Path(row["relative"])
            # Numbering applies to text manuscripts, not service JSON or known
            # folder documentation. Every file remains protected by no-clobber.
            if (row["relative"].startswith("Canonical/Final-Manuscript/") and row["document_id"] not in used
                    and path.suffix.lower() in {".md", ".txt"}
                    and path.stem.casefold() not in {"readme", "readme.zh-cn", "readme.en", "license", "notice", "说明", "目录"}):
                match = re.search(r"Volume-([0-9]+)/Chapter-([0-9]+)", row["relative"])
                hint = re.match(r"(?:第)?([0-9]+)(?:章|[_ -])", Path(row["relative"]).stem)
                rows.append({**row, "suggested_volume": int(match[1]) if match else 1,
                             "suggested_number": int(match[2]) if match else int(hint[1]) if hint else None})
        return rows

    def register_existing(self, document_id, volume, number):
        self.ensure()
        row = self.core.document(document_id)
        if row["area"] != "canonical" or not row["relative"].startswith("Canonical/Final-Manuscript/"):
            raise CoreError("NUMBER_INVALID", "只能登记已有正式章节，不移动或改写原文。")
        cid = chapter_id(self.core.project_id, volume, number)
        def operation(state, tree):
            if cid not in state["chapters"] or state["chapters"][cid].get("draft"):
                raise CoreError("NUMBER_INVALID", "请在已确认大纲中选择尚未生成草稿的卷章位置。")
            if any((r.get("canonical") or {}).get("document_id") == document_id or r.get("canonical") and (r.get("canonical_volume", r["volume"]), r.get("canonical_number", r["number"])) == (volume, number) for r in state["chapters"].values()):
                raise CoreError("NUMBER_CONFLICT", "这份正式章节或编号已经登记。")
            state["chapters"][cid].update(canonical={"document_id": document_id, "revision": row["revision"]}, canonical_volume=volume,
                canonical_number=number, status="ARCHIVED", existing_import=True)
        self.store.mutate("accept_register_existing", operation)
        self.core.lengths.link(cid, document_id)

    def preview(self, chapter_ids, numbering=None):
        self.core.require_idle()
        if not isinstance(chapter_ids, list) or not chapter_ids or len(chapter_ids) > 100 or len(set(chapter_ids)) != len(chapter_ids):
            raise CoreError("CHAPTER_SELECTION_INVALID", "请选择 1 至 100 个不同的已审章节。")
        unplaced = self.unindexed()
        state = self.store.read()
        outline = self.core.workflow.planning.approved()
        numbering = numbering or {}
        occupied = {(r.get("canonical_volume", r["volume"]), r.get("canonical_number", r["number"])) for r in state["chapters"].values() if r.get("canonical")}
        planned_slots = [(r["volume"], r["number"]) for r in self.planned()]
        if not occupied <= set(planned_slots):
            raise CoreError("NUMBER_CONFLICT", "已有正式编号不在当前大纲范围，请作者先核对规划。")
        batch_id, items, slots = uuid.uuid4().hex, [], set(occupied)
        with SafeTree(self.core.project) as tree:
            for cid in chapter_ids:
                identifier(cid)
                chapter = state["chapters"].get(cid)
                if chapter is None or not chapter.get("draft") or not chapter.get("review") or not chapter.get("next_direction"):
                    raise CoreError("REVIEW_REQUIRED", "只能收录已有完整审稿及下一章方向的章节。")
                if chapter.get("editor_plan") and self.store.blob(chapter["editor_plan"])["outline_version"] != outline["version_id"]:
                    raise CoreError("PLAN_NEEDS_SYNC", "正式大纲已更新。本章旧稿和旧计划保留，请先让责编按新大纲重新规划并复审，再收录。")
                if chapter.get("canonical"):
                    raise CoreError("ALREADY_ARCHIVED", "所选章节已经正式收录，不能重复创建。")
                draft = self.checked_document(chapter["draft"], "drafts")
                report = self.checked_document(chapter["review"], "reports")
                volume, number = numbering.get(cid, (chapter["volume"], chapter["number"]))
                target = formal_target(volume, number)
                if (volume, number) not in planned_slots or (volume, number) in slots:
                    raise CoreError("NUMBER_CONFLICT", "建议或手动编号重复，或不属于已确认的大纲。")
                if tree.maybe_read(target) is not None:
                    raise CoreError("TARGET_EXISTS", "正式文件已经存在，新收录不能覆盖，请先核对章节目录。")
                conflicts = [r for r in unplaced if (r["suggested_volume"], r["suggested_number"]) == (volume, number)]
                if conflicts:
                    raise CoreError("EXISTING_CHAPTER_CONFLICT", f"本次第{volume}卷第{number}章与现有文件「{Path(conflicts[0]['relative']).name}」的编号线索冲突。未写入正式区；请只核对此编号的对应关系，其他草稿可继续创作。")
                slots.add((volume, number))
                plan = admin._plan(tree, "chapter_accept", draft["relative"], target, draft["text"].encode("utf-8"), batch_id)[0]
                items.append({"chapter_id": cid, "draft": chapter["draft"], "report": chapter["review"], "volume": volume,
                    "number": number, "title": chapter["title"], "plan": plan, "source": draft["relative"], "report_source": report["relative"]})
        highest = max(planned_slots.index(slot) for slot in slots) if slots else -1
        if any(slot not in slots for slot in planned_slots[:highest + 1]):
            raise CoreError("NUMBER_GAP", "会产生正式章节跳号，请先选择前面的已审章节，或明确登记已有章节。")
        items.sort(key=lambda i: planned_slots.index((i["volume"], i["number"])))
        bundle = {"batch_id": batch_id, "outline_version": outline["version_id"], "items": items}
        handle = secrets.token_urlsafe(32)
        self.core._approvals[handle] = (time.monotonic() + 300, bundle, ("workflow_chapters", batch_id))
        unplaced_notice = (f"另有 {len(unplaced)} 份尚未关联目录的旧文件，本次不移动、不修改，也不把它们当作已完成章。本次只新增下列精确目标，请核对后批准。\n\n" if unplaced else "")
        return {"handle": handle, "target": f"{len(items)} 个正式章节", "operation": "chapter_accept",
            "diff": unplaced_notice + "\n\n".join(f"第{i['volume']}卷 第{i['number']}章 {i['title']}\n" + i["plan"]["diff"] for i in items), "batch_id": batch_id,
            "notice": "一次明确批准涵盖所选精确版本；顺序提交并记录部分完成状态，不宣称跨文件原子事务。"}

    def commit_approved(self, bundle, batch_id):
        identifier(batch_id)
        self.core.require_idle()
        if bundle["batch_id"] != batch_id or self.core.workflow.planning.approved()["version_id"] != bundle["outline_version"]:
            raise CoreError("REVISION_CONFLICT", "确认的大纲或批次已经变化。")
        for item in bundle["items"]:
            self.checked_document(item["draft"], "drafts")
            self.checked_document(item["report"], "reports")
            with SafeTree(self.core.project) as tree:
                if tree.maybe_read(item["plan"]["target"]) is not None:
                    raise CoreError("TARGET_EXISTS", "正式目标已变化，已停止本批次。")
            if any((r["suggested_volume"], r["suggested_number"]) == (item["volume"], item["number"]) for r in self.unindexed()):
                raise CoreError("EXISTING_CHAPTER_CONFLICT", "批准后出现与本次编号冲突的现有文件，已停止；请重新核对本次收录。")
        def begin(state, tree):
            self.core.recovery.prepare_batch(tree, bundle, state)
            ref = self.store.put(tree, BASE + "/Batches/" + batch_id + ".json", bundle)
            state["batches"][batch_id] = {**ref, "status": "APPLYING", "completed": []}
        self.store.mutate("accept_started", begin)
        results = []
        for item in bundle["items"]:
            def operation(state, tree):
                if any((r["suggested_volume"], r["suggested_number"]) == (item["volume"], item["number"]) for r in self.unindexed()):
                    raise CoreError("EXISTING_CHAPTER_CONFLICT", "提交前检测到本次编号的新冲突，已停止；保留原稿和批次记录。")
                payload = tree.read(item["source"])
                if digest(payload) != item["draft"]["revision"] or digest(tree.read(item["report_source"])) != item["report"]["revision"]:
                    raise CoreError("REVISION_CONFLICT", "草稿或审稿报告在批准后变化，未继续。")
                plan = admin._plan(tree, "chapter_accept", item["source"], item["plan"]["target"], payload, batch_id)
                record = self.core.recovery._batch(tree, state, batch_id)[1]
                receipt = admin._commit(tree, plan, item["plan"]["approval_digest"],
                                        transaction_id=record['transactions'][item['chapter_id']])
                self._index_receipt(state, item, receipt)
                state["batches"][batch_id]["completed"].append(item["chapter_id"])
                return receipt
            receipt = self.store.mutate("accept_chapter", operation)
            results.append(receipt)
            cid = item["chapter_id"]
            self.core.lengths.link(cid, self.store.read()["chapters"][cid]["canonical"]["document_id"])
        self.store.mutate("accept_completed", lambda s, t: s["batches"][batch_id].update(status="COMPLETED"))
        self.core._state(lambda s: s.update(version_order=(s.get("version_order", []) + [r["transaction_id"] for r in results])[-1000:]))
        return {"transaction_id": batch_id, "target": f"{len(results)} 个正式章节", "chapter_transactions": [r["transaction_id"] for r in results]}

    @staticmethod
    def _index_receipt(state, item, receipt):
        document_id = digest(receipt["target"].encode())[:32]
        state["chapters"][item["chapter_id"]].update(status="ARCHIVED", canonical={"document_id": document_id, "revision": receipt["after_sha256"]},
            canonical_volume=item["volume"], canonical_number=item["number"], approval_transaction=receipt["transaction_id"])

    def recover_batch(self, batch_id, confirmed=False):
        """Exact registered transactions only; recovery never republishes prose."""
        return self.core.recovery.recover_batch(batch_id, confirmed)

    def adopt_draft(self, cid, document_id, *, expected_source=None):
        identifier(cid)
        self.core.require_idle()
        source = self.core.document(document_id, "writer")
        if source["area"] != "drafts":
            raise CoreError("CHAPTER_SOURCE_CHANGED", "请选择已经保存的主笔草稿，不把资料或报告当正文。")
        def operation(state, tree):
            chapter = state["chapters"].get(cid)
            if chapter is None:
                raise CoreError("CHAPTER_SOURCE_MISSING", "请选择规划中的章节。")
            if expected_source is not None and chapter.get("draft") != expected_source:
                raise CoreError("REVISION_CONFLICT", "本章已在其他窗口采用新稿，未替换；生成结果仍保留。")
            previous = self.store.put(tree, BASE + "/Chapter-History/" + cid + "/" + uuid.uuid4().hex + ".json", dict(chapter))
            chapter.setdefault("revision_history", []).append(previous)
            chapter.update(draft={"document_id": document_id, "revision": source["revision"]}, review=None, review_result=None,
                next_direction=None, review_snapshot=None, status="DRAFT_SAVED", length_checked=False, review_decision_checked=False,
                length_acknowledged=False, review_acknowledged=False, local_length=None, repair_pending=False,
                revision_plan_current=None, repair_rounds=0)
            for guide in state["guidance"].values():
                if guide["source_chapter"] == cid and not guide.get("locked"):
                    guide["status"] = "needs_review"
        self.store.mutate("chapter_author_revision", operation)
        self.core.lengths.link(cid, document_id)

    def place(self, cid, volume, position, volume_title=""):
        """Reading-directory arrangement is separate from stable story identity."""
        identifier(cid)
        if type(volume) is not int or type(position) is not int or not 1 <= volume <= 100 or not 1 <= position <= 1000:
            raise CoreError("NUMBER_INVALID", "请选择有效的目录卷号和插入位置。")
        from .core import checked_text
        checked_text(volume_title, 180)
        def operation(state, tree):
            run = state["runs"].get(state["active_run"])
            if run and run["status"] not in {"COMPLETED", "STOPPED"}:
                raise CoreError("ACTIVE_RUN", "请先完成或停止当前任务，再调整目录归属；正文与已有编号不会移动。")
            if cid not in state["chapters"]:
                raise CoreError("CHAPTER_REQUIRED", "请先选择章节。")
            groups = {}
            for row in sorted(state["chapters"].values(), key=lambda r: (r.get("directory", {}).get("position", r["number"]), r["ordinal"])):
                if row["chapter_id"] != cid:
                    groups.setdefault(row.get("directory", {}).get("volume", row["volume"]), []).append(row)
            group = groups.setdefault(volume, [])
            group.insert(min(position - 1, len(group)), state["chapters"][cid])
            for v, rows in groups.items():
                for index, row in enumerate(rows):
                    old = row.get("directory", {})
                    row["directory"] = {"volume": v, "position": index + 1, "volume_title": volume_title if v == volume else old.get("volume_title", row["volume_title"])}
        self.store.mutate("execution_directory_arranged", operation)

    def rename_volume(self, volume, title):
        from .core import checked_text
        checked_text(title, 180)
        def operation(state, tree):
            rows = [r for r in state['chapters'].values() if r.get('directory', {}).get('volume', r['volume']) == volume]
            if not rows:
                raise CoreError('CHAPTER_REQUIRED', '此分卷已经变化，请重新选择。')
            for row in rows:
                row['directory'] = {**row.get('directory', {}), 'volume':volume,
                    'position':row.get('directory', {}).get('position', row['number']), 'volume_title':title}
        self.store.mutate('execution_volume_renamed', operation)

    def move_volume(self, volume, position):
        def operation(state, tree):
            run = state['runs'].get(state.get('active_run'))
            if run and run['status'] not in {'COMPLETED', 'STOPPED'}:
                raise CoreError('ACTIVE_RUN', '请先完成或停止任务，再调整卷顺序。')
            volumes = sorted({r.get('directory', {}).get('volume', r['volume']) for r in state['chapters'].values()})
            if volume not in volumes or type(position) is not int or not 1 <= position <= len(volumes):
                raise CoreError('NUMBER_INVALID', '所选卷或目标顺序无效。')
            volumes = [value for value in volumes if value != volume]
            volumes.insert(position-1, volume)
            mapping = {old:index+1 for index, old in enumerate(volumes)}
            for row in state['chapters'].values():
                old = row.get('directory', {})
                row['directory'] = {'volume':mapping[old.get('volume', row['volume'])],
                    'position':old.get('position', row['number']), 'volume_title':old.get('volume_title', row['volume_title'])}
        self.store.mutate('execution_volumes_arranged', operation)

    def preview_revision(self, cid):
        identifier(cid)
        self.core.require_idle()
        chapter = self.store.read()["chapters"].get(cid)
        if not chapter or not chapter.get("canonical") or not chapter.get("review") or not chapter.get("next_direction"):
            raise CoreError("REVIEW_REQUIRED", "请先完成该章节修订稿的新一轮审稿。")
        old = self.checked_document(chapter["canonical"], "canonical")
        source = self.checked_document(chapter["draft"], "drafts")
        self.checked_document(chapter["review"], "reports")
        with SafeTree(self.core.project) as tree:
            plan = admin._plan(tree, "chapter_revision", source["relative"], old["relative"], source["text"].encode("utf-8"))[0]
        handle = secrets.token_urlsafe(32)
        envelope = {'plan': plan, 'chapter_sha': digest(admin.encode(chapter)),
                    'report_path': self.checked_document(chapter['review'], 'reports')['relative']}
        self.core._approvals[handle] = (time.monotonic() + 300, envelope, ("workflow_chapter_revision", cid))
        return {"handle": handle, "target": "已收录章节的修订版本（旧版先备份）", "diff": plan["diff"], "operation": "chapter_revision"}

    def commit_revision(self, plan, cid):
        receipt = self.core.recovery.submit(plan, cid)
        try:
            self.core.recovery.complete_derived(cid)
        except (ValueError, OSError) as error:
            raise CoreError('DERIVED_UPDATE_PENDING', '正式正文和章节目录已保存；字数关联或显示记录未完成，请查看详情。不要重新发布正文。') from error
        return {"transaction_id": receipt["transaction_id"], "target": receipt["target"]}

    def record_restore(self, receipt):
        document_id = digest(receipt["target"].encode())[:32]
        matches = [row["chapter_id"] for row in self.catalog() if (row.get("canonical") or {}).get("document_id") == document_id]
        if not matches:
            return
        with SafeTree(self.core.project) as tree:
            payload = tree.read(receipt["target"])
        if digest(payload) != receipt["after_sha256"]:
            raise CoreError("REVISION_CONFLICT", "正式版本在更新索引前发生变化，请先核对。")
        count = measure(payload.decode("utf-8-sig"), self.core.lengths.plan()["metric"])["count"]
        readings = {cid: evaluate(count, self.core.lengths.resolved_goal(cid)) for cid in matches}
        def operation(state, tree):
            for cid in matches:
                state["chapters"][cid].update(canonical={"document_id": document_id, "revision": receipt["after_sha256"]},
                    approval_transaction=receipt["transaction_id"], status="ARCHIVED", local_length=readings[cid])
        self.store.mutate("accept_revision_index", operation)
        for cid in matches:
            self.core.lengths.link(cid, document_id)
