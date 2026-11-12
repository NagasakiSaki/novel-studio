"""Workflow intents handled by the existing Presenter and its single Job queue."""
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox
from ..core import CoreError
from ..workflow_review import direction_text
from .workflow_ui import ScaleDialog, StartDialog, OutlineEditDialog, NumberingDialog
from .dialogs import DiffDialog, InformationDialog
from .task_scope import choice_label
from .author_approval import approved  # Public compatibility export.
from .first_ai import FirstCollaborationDialog


def choose(parent, title, prompt, rows, labels):
    if not rows:
        raise CoreError("NOTHING_TO_SELECT", "目前没有可选择的记录。")
    selected, ok = QInputDialog.getItem(parent, title, prompt, labels, 0, False)
    return rows[labels.index(selected)] if ok else None


def latest_run(presenter):
    run = presenter.core.workflow.pipeline.active()
    if run is None:
        raise CoreError("RUN_MISSING", "请先开始一次有明确范围的创作任务。")
    return run


def tick(presenter):
    if not presenter.workflow_auto or presenter.job is not None or presenter.core is None:
        return
    core = presenter.core
    run = core.workflow.pipeline.active()
    if run is None or run["run_id"] != presenter.workflow_run or run["status"] != "RUNNING":
        presenter.workflow_auto = False
        return
    if presenter.window.editor.document().isModified():
        presenter.workflow_auto = False
        core.workflow.pipeline.control(run["run_id"], "pause")
        presenter.window.status_text.setText("当前稿件有未保存修改，连续创作已暂停，请先保存。")
        return
    presenter.start_job(lambda progress: core.workflow.pipeline.advance(run["run_id"], progress),
        lambda value: presenter.window.workflow_panel.bind(core, True), choice_label(run['mode'],run.get('requested_count',1))+" · 正在处理并保存当前步骤…", diagnostic_action='workflow_step')


def activate(presenter, run):
    presenter.workflow_auto = True
    presenter.workflow_run = run["run_id"]
    presenter.window.workflow_panel.pages.setCurrentIndex(3)
    presenter.refresh_view()
    presenter.workflow_timer.start(0)


def outline_finished(presenter, record, sent=None):
    panel = presenter.window.workflow_panel
    presenter.refresh_view()
    panel.versions.setCurrentIndex(panel.versions.findData(record["version_id"]))
    panel.pages.setCurrentIndex(1)
    if sent is not None and panel.instruction.toPlainText() == sent:
        panel.instruction.clear()
    presenter.window.status_text.setText("责编的大纲候选已保存，可继续对话调整；确认之前不会成为正式创作依据。")


def action(p, name, data):
    w, core = p.window, p.core
    if name == "workflow_dashboard":
        w.tabs.setCurrentWidget(w.workflow_panel)
        return
    if core is None:
        raise CoreError("PROJECT_REQUIRED", "请先新建或打开作品。")
    panel = w.workflow_panel
    if name not in {"workflow_scale", "workflow_guidance", "workflow_register", "workflow_recover_batch", "workflow_recover_locks"}:
        w.tabs.setCurrentWidget(panel)
    planning = core.workflow.planning
    pipeline = core.workflow.pipeline
    if name == "workflow_first_ai":
        dialog = FirstCollaborationDialog(p)
        result = dialog.exec()
        if dialog.stage_kind == "outline" and not dialog.route:
            panel.instruction.setPlainText(dialog.idea.toPlainText())
        p.refresh_view()
        if result == QDialog.Accepted and dialog.intent:
            intent, values = dialog.intent
            if intent == "ui_navigate" and values.get("page") == "outline":
                panel.versions.setCurrentIndex(0)
            p.action(intent, values)
    elif name == "workflow_scale":
        ScaleDialog(core, w).exec()
        p.refresh_view()
    elif name == "outline_generate":
        text = data.get("instruction", "")
        p.start_job(lambda progress: planning.generate(text, base_version=data.get("base_version"), scope=data.get("scope"),
                    notify=progress, preview_binding=data.get("preview_binding")),
            lambda record: outline_finished(p, record, text), "责编正在规划大纲…")
    elif name == "outline_restore":
        record = planning.restore(data["version_id"])
        outline_finished(p, record)
    elif name == "outline_edit":
        old = planning.get(data["version_id"])
        dialog = OutlineEditDialog(old["value"], w)
        if dialog.exec() == QDialog.Accepted:
            record = planning.save(dialog.value, base_version=old["version_id"], instruction="作者通过表单保存的新大纲草稿")
            outline_finished(p, record)
    elif name == "outline_diff":
        selected = planning.get(data["version_id"])
        if not selected["base_version"]:
            InformationDialog("首份大纲草稿", selected["text"], w).exec()
        else:
            InformationDialog("大纲版本完整差异", planning.diff(selected["base_version"], selected["version_id"]), w).exec()
    elif name == "outline_approve":
        if approved(p, planning.preview(data["version_id"])):
            core.workflow.chapters.ensure()
            p.refresh_view()
            panel.pages.setCurrentIndex(0)
            w.status_text.setText("正式大纲已确认。可在作品页点“首次 AI 协作”试写 1 章。")
    elif name == "outline_recover":
        state = core.workflow.store.read()
        rows = [key for key, value in state["outline_jobs"].items() if value["status"] != "COMPLETED"]
        selected = choose(w, "恢复大纲结果", "选择未完成的规划任务；只恢复已保存结果，不发模型请求：", rows,
            [f"规划任务 {index + 1} · {state['outline_jobs'][key].get('instruction') or '根据预设自主构思'}" for index, key in enumerate(rows)])
        if selected:
            outline_finished(p, planning.recover(selected))
    elif name == "workflow_start":
        if w.editor.document().isModified():
            raise CoreError("UNSAVED_DRAFT", "请先保存当前草稿，再开始连续创作。")
        if data.get("first_preview") is not None:
            activate(p, pipeline.start(data["first_preview"], True))
            return
        state = core.workflow.store.read()
        if not state["approved_outline"]:
            panel.pages.setCurrentIndex(1)
            text = panel.instruction.toPlainText()
            p.start_job(lambda progress: planning.generate(text, notify=progress),
                lambda record: outline_finished(p, record, text), "先由责编准备待确认大纲…")
            return
        preview = pipeline.preview(**panel.task_choice())
        activate(p, pipeline.start(preview, True))
    elif name in {"workflow_pause", "workflow_stop"}:
        p.workflow_auto = False
        p.workflow_timer.stop()
        run = latest_run(p)
        pipeline.control(run["run_id"], "pause" if name == "workflow_pause" else "stop")
        panel.bind(core, p.job is not None)
        w.status_text.setText("已记录要求；当前模型步骤结束并保存后生效，原稿和进度保留。")
    elif name in {"workflow_resume", "workflow_retry"}:
        run = latest_run(p)
        if run.get('workflow_version',1)<2:
            panel.alert.setText('此为旧流程任务。请点击停止，再按上方可见范围开始新版任务；已有草稿会保留。')
            return
        row = pipeline.resume(run["run_id"],retry_confirmed=name=="workflow_retry",accept_warning=True,
                              adopt_models=True,adopt_scale=True,adopt_context=True)
        activate(p,row)
    elif name in {"workflow_writer_chat", "workflow_editor_chat", "workflow_repair", "workflow_chapter_review"}:
        cid = data.get("chapter_id")
        chapter = core.workflow.store.read()["chapters"].get(cid)
        if not chapter:
            raise CoreError("CHAPTER_REQUIRED", "请先选择一章。")
        role = "writer" if name == "workflow_writer_chat" else "editor"
        w.switch_role(role)
        w.chat_chapter_id = cid
        w.chat_scope.setCurrentIndex(w.chat_scope.findData("chapter"))
        w.refresh_chat_scope()
        if name == "workflow_chapter_review":
            w.chat_operation.setCurrentIndex(w.chat_operation.findData("review"))
            w.chat_inputs[role].setPlainText("请重新审核当前章节，核对正式大纲、责编计划、人物与世界观，区分硬伤和文学建议。")
            w.status_text.setText("已关联本章。点击发送给责编，会执行复审并保存独立报告。")
        elif name == "workflow_repair":
            if not chapter.get("review_result"):
                raise CoreError("REVIEW_REQUIRED", "请先让责编审核本章。")
            message = "将保留旧稿、审稿和方案，开始本章新的有限返修任务：责编制定方案、主笔修订、责编复审，最多两轮，最多九次模型任务，可能计费。当前未完成的连续任务会停止并保留进度；正式作品不会更新。是否开始？"
            if QMessageBox.question(w, "按责编建议修订本章", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                active = pipeline.active()
                if active and active["status"] not in {"COMPLETED", "STOPPED"}:
                    pipeline.control(active["run_id"], "stop")
                core.workflow.store.mutate("author_repair_requested", lambda s, t: s["chapters"][cid].update(
                    repair_pending=True, revision_plan_current=None, repair_rounds=0, review_acknowledged=False, review_decision_checked=False))
                run = pipeline.start_review(cid, True)
                core.workflow.store.mutate("author_repair_authorized", lambda s, t: s["runs"][run["run_id"]].update(creative_mode="continuous"))
                activate(p, pipeline.get(run["run_id"]))
    elif name == "workflow_place":
        cid = data.get("chapter_id")
        row = core.workflow.store.read()["chapters"].get(cid)
        if not row:
            raise CoreError("CHAPTER_REQUIRED", "请先选择章节。")
        volume, ok = QInputDialog.getInt(w, "调整阅读目录归属", "目标目录卷号（只整理目录，不移动正文、不改原始创作编号或正式编号）：", row.get("directory", {}).get("volume", row["volume"]), 1, 100)
        if not ok:
            return
        position, ok = QInputDialog.getInt(w, "调整目录位置", "插入到本卷第几章（目录连续重排，历史身份不变）：", 1, 1, 1000)
        if ok:
            core.workflow.chapters.place(cid, volume, position)
            p.refresh_view()
            w.status_text.setText("阅读目录已调整并保留历史；正式编号和创作计划未被重写。")
    elif name == "workflow_export_chapter":
        row = core.workflow.store.read()["chapters"].get(data.get("chapter_id"))
        if not row or not (row.get("draft") or row.get("canonical")):
            raise CoreError("CHAPTER_SOURCE_MISSING", "本章尚无可导出的正文。")
        from .object_controller import action as object_action
        object_action(p, 'object_chapter_export', data)
    elif name == "workflow_chapter_history":
        row = core.workflow.store.read()["chapters"].get(data.get("chapter_id"))
        if not row:
            raise CoreError("CHAPTER_REQUIRED", "请先选择章节。")
        refs = row.get("revision_history", [])
        selected = choose(w, "本章历史版本", "选择完整旧稿/旧审稿（只读查看，不直接更新正式作品）：", refs,
            [f"旧版本 {i + 1}" for i in range(len(refs))])
        if selected:
            snapshot = core.workflow.store.blob(selected)
            old = snapshot.get("chapter", snapshot)
            source = old.get("draft")
            text = core.workflow.chapters.checked_document(source, "drafts")["text"] if source else "该版本尚未生成正文。"
            if old.get("review"):
                text += "\n\n——旧审稿——\n\n" + core.workflow.chapters.checked_document(old["review"], "reports")["text"]
            InformationDialog("本章完整旧版本", text, w).exec()
            if source and QMessageBox.question(w, "恢复为待审草稿？", "是否将刚查看的旧稿恢复为本章当前草稿？现稿保留历史，需重新审稿；正式作品不会改动。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                core.workflow.chapters.adopt_draft(row["chapter_id"], source["document_id"], expected_source=row.get("draft"))
                p.refresh_view()
    elif name == "workflow_author_guidance":
        rows = [r for r in core.workflow.store.read().get("author_instructions", []) if r["status"] == "ACTIVE" and r["operation"] == "book_guidance"]
        selected = choose(w, "全书指导", "选择一条查看或停用；完整历史保留：", rows, [r["text"][:70] for r in rows])
        if selected:
            InformationDialog("作者全书指导", selected["text"], w).exec()
            if QMessageBox.question(w, "停用此条指导？", "停用后不再注入后续任务，历史仍保留。是否停用？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                core.workflow.author.disable_guidance(selected["id"])
    elif name in {"workflow_document", "workflow_review", "workflow_direction"}:
        cid = data.get("chapter_id")
        chapter = core.workflow.store.read()["chapters"].get(cid)
        if chapter is None:
            raise CoreError("CHAPTER_REQUIRED", "请先在目录中选择一章。")
        if name == "workflow_direction":
            if not chapter.get("next_direction"):
                raise CoreError("DIRECTION_PENDING", "责编尚未完成该章的下一章方向。")
            InformationDialog("下一章创作方向 · 作者可裁决", direction_text(chapter["next_direction"]), w).exec()
        else:
            source = (chapter.get("review") if name == "workflow_review" else
                      chapter.get("draft") if chapter.get("draft") and chapter["status"] != "ARCHIVED" else
                      chapter.get("canonical") or chapter.get("draft"))
            if not source:
                raise CoreError("CHAPTER_SOURCE_MISSING", "该章节尚无对应稿件或审稿意见。")
            if not w.editor.document().isModified() or QMessageBox.question(w, "当前稿件尚未保存", "打开另一份文稿会放弃编辑区的未保存内容，是否继续？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                p.load_document(source["document_id"])
    elif name == "workflow_accept":
        ids = data.get("chapter_ids", [])
        rows = core.workflow.store.read()["chapters"]
        if not ids:
            raise CoreError("CHAPTER_SELECTION_INVALID", "请勾选已完成审稿的章节。")
        revisions = [key for key in ids if rows[key].get("canonical")]
        if revisions:
            if len(ids) != 1:
                raise CoreError("CHAPTER_SELECTION_INVALID", "已有正式版的修订请逐章确认，不与首次收录混在同一批。")
            approved(p, core.workflow.chapters.preview_revision(ids[0]))
        else:
            dialog = NumberingDialog([rows[key] for key in ids], w)
            if dialog.exec() == QDialog.Accepted:
                approved(p, core.workflow.chapters.preview(ids, dialog.numbering()))
    elif name == "workflow_register":
        rows = core.workflow.chapters.unindexed()
        selected = next((r for r in rows if r['document_id'] == data['document_id']), None) if data.get('document_id') else choose(w, "登记已有正式章节", "只登记索引，不修改或移动已有正文：", rows, [r["display_title"] for r in rows])
        if selected:
            volume, ok = QInputDialog.getInt(w, "正式卷号", "确认所属卷：", selected["suggested_volume"], 1, 100)
            if not ok:
                return
            number, ok = QInputDialog.getInt(w, "正式章号", "确认本卷章号：", selected["suggested_number"] or 1, 1, 1000)
            if ok:
                core.workflow.chapters.register_existing(selected["document_id"], volume, number)
                p.refresh_view()
    elif name == "workflow_recover_batch":
        rows = [(key, value) for key, value in core.workflow.store.read()["batches"].items() if value["status"] != "COMPLETED"]
        selected = choose(w, "恢复收录记录", "仅校验并恢复已写入的正式章节记录；剩余章节需要重新预览批准：", rows,
            [f"收录批次 {i + 1} · 已记录完成 {len(r['completed'])} 章" for i, (key, r) in enumerate(rows)])
        if selected:
            result = core.workflow.chapters.recover_batch(selected[0], confirmed=True)
            p.refresh_view()
            QMessageBox.information(w, "收录记录已检查", f"已恢复 {len(result['recovered'])} 章；其余 {result['remaining_need_new_author_preview']} 章请重新选择并查看差异。没有重复写正文。")
    elif name == "workflow_recover_locks":
        message = "仅处理能够确认所属进程已经结束的本作品中断锁，并先保存原记录。不会终止进程或修改系统权限；无法确认的锁会保留。是否检查恢复？"
        if QMessageBox.question(w, "检查本作品中断记录", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            removed = core.workflow.store.recover_locks(True)
            QMessageBox.information(w, "检查完成", f"已恢复 {len(removed)} 项。模型请求结果未知时，继续或重试仍需作者决定。")
    elif name == "workflow_revise":
        if w.editor.document().isModified() or w.document_area != "drafts":
            raise CoreError("SAVED_DRAFT_REQUIRED", "请先在文稿页保存修订草稿，然后回到章节目录选择对应章节。")
        old = pipeline.active()
        if old and old["status"] not in {"COMPLETED", "STOPPED"}:
            raise CoreError("ACTIVE_RUN", "请先停止或完成当前连续任务，再重新审核修订稿。")
        if QMessageBox.question(w, "采用作者修订稿", "将当前已保存草稿关联到所选章节，并重新请责编审阅。旧稿/旧报告保留，已有正式版不会自动更新。是否继续？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            cid = data.get("chapter_id")
            core.workflow.chapters.adopt_draft(cid, w.document_id)
            activate(p, pipeline.start_review(cid, confirmed=True))
    elif name == "workflow_rename":
        cid = data.get("chapter_id")
        chapter = core.workflow.store.read()["chapters"].get(cid)
        if chapter is None:
            raise CoreError("CHAPTER_REQUIRED", "请先选择要修改目录名称的章节。")
        title, ok = QInputDialog.getText(w, "修改目录章节名", "只改目录元数据，不改正式大纲或正文：", text=chapter["title"])
        if not ok:
            return
        volume_title, ok = QInputDialog.getText(w, "修改目录卷名", "保留当前卷名或填写新名称；不会改写正式大纲：", text=chapter["volume_title"])
        if ok:
            core.workflow.execution.retitle(cid, title, volume_title)
            core.workflow.scale.sync()
            p.refresh_view()
    elif name == "workflow_guidance":
        from .workflow_guidance_ui import GuidanceDialog
        GuidanceDialog(core, w).exec()
        p.refresh_view()
    else:
        raise CoreError("METHOD_NOT_FOUND", "当前工作流不支持此操作。")
