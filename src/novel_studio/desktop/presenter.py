"""UI controller: Qt intents -> the same bound Core used by tests and Bridge."""
from __future__ import annotations

import json
import re
from pathlib import Path
import sys
from threading import Event
import uuid
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QTreeWidgetItem, QPushButton
from ..core import Core, CoreError, ROOT, READ, WRITE, SafeTree, admin
from ..host import ModelService, OfficialCLI, EFFORTS
from ..usage import summarize
from ..api_service import ProviderRegistry
from ..providers import DESTINATIONS
from .dialogs import DiffDialog, InformationDialog
from .ui_state import transcript_text
from ..diagnostics import DiagnosticLog
from .jobs import Job  # Public compatibility export.

sys.path.insert(0, str(ROOT))
from gui.service import LocalStore


class Presenter(QObject):
    def __init__(self, window, state_root, cli=None, diagnostic_log=None, config_recovery=None, *, background_load=False):
        super().__init__(window)
        self.window = window
        from .local_config_ui import choose_config
        # Startup recovery runs before the workbench is visible. Keep its
        # dialog independent so the desktop can discover and focus it.
        self.store = LocalStore.open(Path(state_root), config_recovery or choose_config)
        self.diagnostic_log = diagnostic_log or DiagnosticLog(self.store.root)
        self.providers = ProviderRegistry(self.store)
        self.cli = cli or OfficialCLI()
        self.cli.diagnostics = self.diagnostic_log
        self.core = None
        self.background_load = background_load
        self.job = None
        self.job_message = None
        self.report_id = None
        self.pending_prompt = None
        self._view_documents, self._view_core = None, None
        self._completing_job = False
        self._job_refreshed_core = None
        self.word_plan = None
        self.loading = True
        self.workflow_auto = False
        self.workflow_run = None
        self.workflow_timer = QTimer(self)
        self.workflow_timer.setSingleShot(True)
        self.workflow_timer.timeout.connect(self.workflow_tick)
        self.recovery_timer = QTimer(self)
        self.recovery_timer.setInterval(10000)
        self.recovery_timer.timeout.connect(self.retry_recovery_audits)
        self.recovery_button = QPushButton('正式修订检查', window)
        self.recovery_button.clicked.connect(lambda:self._guarded(self.open_recovery))
        window.header_layout.addWidget(self.recovery_button)
        self.cancel_load_button = QPushButton('停止加载', window)
        self.cancel_load_button.hide()
        self.cancel_load_button.clicked.connect(self.cancel_catalog_load)
        window.header_layout.addWidget(self.cancel_load_button)
        self.state = self.store.startup_state
        window.restore_layout(self.state.get("layout", {}))
        window.action_requested.connect(self.on_action)
        window.layout_changed.connect(self.save_layout)
        window.presenter = self
        self.loading = False
        if self.state.get("project") and not self.store.safe_start:
            try:
                self.open_project(Path(self.state["project"]))
            except (OSError, ValueError):
                window.status_text.setText("上次的作品无法安全打开，请重新选择。")
        if self.job is None:
            self.refresh_view()
        self.refresh_recovery()
        if self.store.safe_start:
            window.status_text.setText('已安全启动界面；未自动打开作品，原小说没有清空。可手动打开原工程；配置备份见诊断页。')

    def refresh_recovery(self):
        if self.core is None:
            self.recovery_button.hide()
            self.recovery_timer.stop()
            return
        self.recovery_button.show()
        self.recovery_button.setEnabled(self.job is None)
        try:
            diagnosis = self.core.recovery.diagnose()
            extra = self.core.recovery.extended_diagnose()
            if extra['batches'] or extra['derived']:
                diagnosis['warnings'].append('其他恢复：有批次或字数、版本显示记录可核查；打开修订检查后选择“其他恢复”。')
        except (OSError, ValueError):
            diagnosis = {'operations': [], 'warnings': ['恢复记录无法安全核验，请查看修订详情。']}
        count = len(diagnosis['operations'])
        self.recovery_button.setText(f'正式修订检查（{count} 项待处理）' if count else
                                     '正式修订检查（容量或记录提示）' if diagnosis['warnings'] else '正式修订检查')
        self.recovery_button.setToolTip('\n'.join(diagnosis['warnings']) or '检查已保存正文与章节目录的关联；恢复不会修改正文。')
        if any(r['state'] in {'INDEX_APPLIED_AUDIT_PENDING', 'INDEX_DONE', 'CANCELLED_CLOSING'} for r in diagnosis['operations']):
            self.recovery_timer.start()
        else:
            self.recovery_timer.stop()
        if count or diagnosis['warnings']:
            self.window.status_text.setText('本作品有正式修订记录需要核查；请点击顶部“正式修订检查”。' if count else diagnosis['warnings'][0])

    def retry_recovery_audits(self):
        if self.core is not None and self.job is None:
            try:
                self.core.recovery.retry_audits()
            except (OSError, ValueError):
                pass
            self.refresh_recovery()

    def open_recovery(self):
        if self.core is None:
            raise CoreError('PROJECT_REQUIRED', '请先打开作品。')
        if self.job is not None:
            raise CoreError('BUSY', '请等待当前操作结束后检查修订记录。')
        from .canonical_recovery_ui import RecoveryDialog
        RecoveryDialog(self).exec()
        self.refresh_recovery()

    def save_layout(self, value=None):
        if not self.loading:
            self.store.save({"layout": value or self.window.capture_layout()})

    def open_project(self, path, discard_inputs=False, *, background=None):
        background = self.background_load if background is None else background
        if self.job is not None:
            raise CoreError('BUSY', '请先停止本地加载或等待当前任务结束，再切换作品。')
        changed = self.core is not None and Path(path) != self.core.project
        if changed and not discard_inputs and (self.window.editor.document().isModified() or any(c.toPlainText().strip() for c in self.window.chat_inputs.values()) or self.window.feedback_input.toPlainText().strip() or self.window.workflow_panel.instruction.toPlainText().strip()):
            raise CoreError("UNSAVED_INPUT", "当前作品还有未保存稿件或未发送文字，请保存/复制后再切换作品。")
        core = Core(path)
        # Compare the saved policies for local work. Native host launch
        # separately verifies the pinned component before using a profile.
        if not core.validate_permissions(verify_plugin=False)["legacy_profiles_match"]:
            raise CoreError("PROFILE_CHANGED", "作品的会话权限已变化，请先检查；程序不会静默覆盖。")
        if self.core is not None:
            self.core.clear_read_caches()
        if changed:
            self.workflow_auto = False
            self.workflow_run = None
            self.window.chat_chapter_id = None
            self.window.search.clear()
            self.window.document_origin = "files"
            self.window.set_document(None, "请选择本作品的章节", "", None, None)
            self.window.review_output.clear()
            self.window.feedback_input.clear()
            self.window.context_list.clear()
            for composer in self.window.chat_inputs.values():
                composer.clear()
            for output in self.window.chat_outputs.values():
                output.clear()
            self.report_id, self.pending_prompt = None, None
        self.core = core
        core.diagnostics = self.diagnostic_log
        ModelService(core, self.cli)
        core.models.attach_api(self.providers)
        old = self.store.load().get("recent_projects", [])
        recent = [str(path)] + [item for item in old if isinstance(item, str) and item != str(path)] if isinstance(old, list) else [str(path)]
        self.store.save({"project": str(path), "recent_projects": recent[:8]})
        if background:
            self.load_catalog_background(core)
        else:
            self.refresh_view()
        self.refresh_recovery()  # Read-only; authorized audit retry is a separate timer tick.

    def cancel_catalog_load(self):
        if self.job is not None and getattr(self.job, 'catalog_core', None) is not None:
            self.job.catalog_cancel.set()
            self.cancel_load_button.setEnabled(False)
            self.window.run_label.setText('正在安全停止本地加载…')

    def load_catalog_background(self, core):
        cancel = Event()
        self.window.tree.clear()
        self.window.workflow_panel.bind(None, True, [])
        self.window.material_panel.bind(None)
        self.window.project_title.setText('正在读取作品目录…')
        self._view_documents, self._view_core = None, None
        def operation(progress):
            rows = core.documents(_cancel=cancel)
            counts = core.lengths.counts(rows, _cancel=cancel) if core.creative.state().get('length_plan') else None
            if cancel.is_set():
                raise CoreError('VIEW_CANCELLED', '本地目录加载已停止，作品文件没有改变。')
            return rows, counts
        def complete(value):
            # This callback receives data only. job_done applies it after the
            # worker has stopped and only to the exact captured Core instance.
            self.job.catalog_result = value
        self.start_job(operation, complete, '正在本机读取作品目录（不会调用模型）…', catalog=(core, cancel))

    def capability(self, role=None):
        if self.core is None:
            raise CoreError("PROJECT_REQUIRED", "请先创建或打开一部作品。")
        return self.core.bind(role or self.window.role, READ | WRITE)

    def call(self, method, params=None, role=None):
        cap = self.capability(role)
        return self.core.dispatch(cap, method, params or {}, uuid.uuid4().hex)

    def start_job(self, operation, done, message, after_finish=None, diagnostic_action=None, *, catalog=None):
        if self.job is not None:
            raise CoreError("BUSY", "上一项操作仍在进行，请等待完成。")
        w = self.window
        w.run_label.setText(message)
        job = Job(operation, self)
        self.job = job
        self._job_refreshed_core = None
        if catalog is not None:
            job.catalog_core, job.catalog_cancel = catalog
            job.catalog_result = None
            self.cancel_load_button.setEnabled(True)
            self.cancel_load_button.show()
        self.job_message = message
        self.diagnostic_job_id = uuid.uuid4().hex[:12]
        self.diagnostic_job_action = diagnostic_action or getattr(self, 'last_action', None)
        job.diagnostic_context = {'job_id':self.diagnostic_job_id, 'action':self.diagnostic_job_action}
        self.diagnostic_log.event('job_start', job_id=self.diagnostic_job_id, action=self.diagnostic_job_action)
        self.diagnostic_job_failed = False
        job.progress.connect(self.progress)
        def complete(value):
            self._completing_job = True
            try:
                done(value)
            except Exception as error:
                self.show_error(error)
            finally:
                self._completing_job = False
        job.finished_value.connect(complete)
        def failed(error):
            if catalog is not None and isinstance(error, CoreError) and error.code == 'VIEW_CANCELLED':
                self.diagnostic_job_failed = True
            else:
                self.show_error(error)
        job.failed.connect(failed)
        job.finished.connect(self.job_done)
        if after_finish is not None:
            job.finished.connect(after_finish)
        try:
            self.refresh_enabled()
            if catalog is None:
                rows = self._view_documents if self._view_core is self.core else []
                w.workflow_panel.bind(self.core, True, rows)
            job.start()
        except Exception:
            # View/source validation can fail before the thread starts. Do not
            # strand the author in a permanently busy state with an idle job.
            self.job = None
            self.job_message = None
            self.cancel_load_button.hide()
            job.deleteLater()
            w.run_label.setText('尚未开始，当前操作未发送')
            self.diagnostic_log.event('job_failed', job_id=self.diagnostic_job_id, action=self.diagnostic_job_action)
            self.refresh_enabled()
            raise
        return job

    def job_done(self):
        self.diagnostic_log.event('job_failed' if getattr(self, 'diagnostic_job_failed', False) else 'job_completed', job_id=getattr(self, 'diagnostic_job_id', None))
        old = self.job
        self.job = None
        self.job_message = None
        old.deleteLater()
        if getattr(old, 'catalog_core', None) is not None:
            self.cancel_load_button.hide()
            if old.catalog_core is self.core and old.catalog_result is not None and not old.catalog_cancel.is_set():
                rows, counts = old.catalog_result
                try:
                    self.refresh_view(rows, counts)
                except Exception as error:
                    self.refresh_enabled()
                    self.window.run_label.setText('目录显示未完成，请重新打开作品。')
                    self.show_error(error)
                else:
                    self.window.run_label.setText('就绪')
            else:
                self.refresh_enabled()
                self.window.project_title.setText('本地加载未完成')
                self.window.project_note.setText('作品文件保持不变，可重新打开作品继续加载。')
                self.window.run_label.setText('本地加载未完成；可重新打开作品，文件没有改变。')
        else:
            self.window.run_label.setText("就绪")
            if self.core is not None and self._job_refreshed_core is self.core:
                self.refresh_enabled()
                self.window.workflow_panel.bind(self.core, False, self._view_documents)
            else:
                self.refresh_view()
        self._job_refreshed_core = None
        if self.workflow_auto:
            run = self.core.workflow.pipeline.active() if self.core else None
            if run and run["status"] == "RUNNING":
                self.workflow_timer.start(0)
            else:
                self.workflow_auto = False

    def workflow_tick(self):
        from .workflow_controller import tick
        try:
            tick(self)
        except Exception as error:
            self.workflow_auto = False
            self.workflow_timer.stop()
            self.show_error(error)

    def show_error(self, error):
        self.diagnostic_job_failed = self.job is not None
        identity = self.diagnostic_log.error(error, action=getattr(self, 'diagnostic_job_action', None) if self.job else getattr(self, 'last_action', None), job_id=getattr(self, 'diagnostic_job_id', None) if self.job else None)
        if self.pending_prompt:
            role, text = self.pending_prompt
            composing = self.window.chat_inputs[role].toPlainText()
            self.window.chat_inputs[role].setPlainText(composing + "\n\n【上次未完成的请求，可编辑后重试】\n" + text if composing.strip() else text)
            self.pending_prompt = None
        message = str(error) if isinstance(error, CoreError) else "操作未完成。文件可能已变化或无法安全读取，请重新打开作品检查。"
        note = '日志记录已暂停，本次错误不会保存。' if not self.diagnostic_log.enabled else ('可在“设置 → 高级与安装 → 诊断日志”查看并导出。' if self.diagnostic_log.available else '日志暂时无法写入，当前会话记录仍可在诊断日志中查看。')
        QMessageBox.warning(self.window, "操作未完成", message + "\n\n诊断编号：" + identity + '\n' + note)
        self.refresh_recovery()

    def confirm_discard(self):
        if not self.window.editor.document().isModified() and not any(c.toPlainText().strip() for c in self.window.chat_inputs.values()) and not self.window.feedback_input.toPlainText().strip() and not self.window.workflow_panel.instruction.toPlainText().strip():
            return True
        return QMessageBox.question(self.window, "还有未保存或未发送的文字", "离开会放弃当前未保存稿件、两个角色未发送的文字、大纲构思及未提交意见。请先保存或复制；仍要离开吗？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def on_action(self, action, data):
        self.last_action = action
        self.diagnostic_log.event('ui_action', action=action, role=self.window.role)
        try:
            self.action(action, data)
        except Exception as error:
            self.show_error(error)

    def action(self, action, data):
        w = self.window
        if data.get('_project_id') and (self.core is None or data['_project_id'] != self.core.project_id):
            raise CoreError('OBJECT_CHANGED', '作品已切换，请在当前作品重新选择对象。')
        if data.get('_run_id') and (self.core is None or not self.core.workflow.pipeline.active() or self.core.workflow.pipeline.active()['run_id'] != data['_run_id']):
            raise CoreError('OBJECT_CHANGED', '创作任务已变化，请重新选择当前任务。')
        if action.startswith('object_'):
            read = {'object_document_read', 'object_chapter_read', 'object_chapter_plan', 'object_chapter_review', 'object_chapter_versions', 'object_outline_read', 'object_volume_expand', 'object_volume_progress', 'object_project_info', 'object_material_info', 'object_material_preview'}
            if self.job is not None and action not in read:
                raise CoreError('BUSY', '当前模型步骤保存后即可进行此操作。')
            if self.core is None:
                raise CoreError('PROJECT_REQUIRED', '请先打开一部作品。')
            from .object_controller import action as object_action
            return object_action(self, action, data)
        if action == 'diagnostic_logs':
            from .diagnostic_ui import show_logs
            show_logs(self)
            return
        if action == 'compatibility':
            from .compatibility_ui import CompatibilityDialog
            CompatibilityDialog(self).exec()
            return
        if action == 'recent_archives':
            rows = self.store.load().get('archived_projects', [])
            if not rows:
                InformationDialog('已归档作品', '尚无归档作品。归档和从列表移除都不会删除作品文件。', w).exec()
            else:
                path, ok = QInputDialog.getItem(w, '恢复已归档作品', '选择作品，恢复到最近列表并打开：', rows, 0, False)
                if ok:
                    recent = self.store.load().get('recent_projects', [])
                    self.store.save({'recent_projects':list(dict.fromkeys([path, *recent]))[:8], 'archived_projects':[r for r in rows if r != path]})
                    self.action('open_recent', {'path':path})
            return
        if action in {'recent_info', 'recent_archive'}:
            recent = self.store.load().get('recent_projects', [])
            if data.get('path') not in recent:
                raise CoreError('OBJECT_CHANGED', '最近作品列表已变化，请重新选择。')
            if action == 'recent_info':
                InformationDialog('作品保存位置', data['path'] + '\n作品文件仍保存在自己的工作区。', w).exec()
            else:
                archived = self.store.load().get('archived_projects', [])
                self.store.save({'recent_projects':[path for path in recent if path != data['path']], 'archived_projects':list(dict.fromkeys([*archived, data['path']]))})
                self.refresh_view()
                w.status_text.setText('已从最近作品中归档；原作品和备份没有移动或删除，可通过打开作品重新加入。')
            return
        if action == "ui_navigate":
            role = data.get("role")
            if role and role != w.role:
                w.switch_role(role)
            w.navigate(data.get("page", "home"))
            if data.get("chapter_id"):
                w.chat_chapter_id = data["chapter_id"]
                w.chat_scope.setCurrentIndex(w.chat_scope.findData("chapter"))
                w.refresh_chat_scope()
            if data.get("filter"):
                field = w.workflow_panel.chapter_filter
                field.setCurrentIndex(max(0, field.findData(data["filter"])))
            return
        if self.job is not None and action not in {"role_selected", "diagnostics", "usage", "workflow_pause", "workflow_stop", "workflow_dashboard"}:
            raise CoreError("BUSY", "当前操作仍在进行，请等待完成。")
        if action.startswith(("workflow_", "outline_")):
            interactive = {"workflow_scale", "workflow_guidance", "workflow_accept", "workflow_register", "workflow_revise", "workflow_rename",
                "workflow_recover_batch", "outline_generate", "outline_edit", "outline_restore", "outline_approve"}
            if self.workflow_auto and self.core and action in interactive:
                self.workflow_auto = False
                self.workflow_timer.stop()
                run = self.core.workflow.pipeline.active()
                if run and run["status"] == "RUNNING":
                    self.core.workflow.pipeline.control(run["run_id"], "pause")
            from .workflow_controller import action as workflow_action
            workflow_action(self, action, data)
            return
        if self.workflow_auto and self.core and action not in {"role_selected", "diagnostics", "usage", "select_document"}:
            run = self.core.workflow.pipeline.active()
            if run and run["status"] == "RUNNING":
                self.workflow_auto = False
                self.core.workflow.pipeline.control(run["run_id"], "pause")
                w.status_text.setText("作者正在调整作品，连续创作已暂停；设置完成后可明确继续。")
        if action == "role_selected":
            self.refresh_models()
            self.refresh_enabled()
            self.refresh_metrics()
            self.refresh_context_receipt()
            return
        if action == "diagnostics":
            self.diagnostics()
            return
        if action == "model_manager":
            from .provider_ui import ProviderDialog
            ProviderDialog(self.providers, self.core, data.get("role", w.role), w).exec()
            self.refresh_view()
            return
        if action == "usage":
            usage = self.call("get_usage")
            names = {"input_tokens": "输入 Token", "output_tokens": "输出 Token", "thinking_tokens": "思考 Token", "cache_read_tokens": "缓存读取 Token", "total_tokens": "官方总 Token"}
            lines = ["当前角色：" + ("主笔" if w.role == "writer" else "责编"), "", "本回合："]
            for key, name in names.items():
                value = usage["latest"][key]
                lines.append(name + "：" + (str(value) if value is not None else "当前模型未提供可核验统计"))
            lines += ["", "当前对话累计："]
            for key, name in names.items():
                value = (usage["cumulative"] or {}).get(key)
                lines.append(name + "：" + (str(value) if value is not None else "尚无数据"))
            lines += ["", "缓存命中率：尚无法确认统计分母，暂不计算。", "不会把缓存读取量换算成保证的费用节省。", "连接测试单独记账，不混入创作回合累计。"]
            local = self.core.cache_diagnostics()
            lines += ['', '本作品 · 本窗口的本地计算缓存（不是模型缓存）：']
            for key, label in (('derived', '解析与字数'), ('material_index', '资料搜索索引')):
                stats = local[key]
                attempts = stats['hits'] + stats['misses']
                ratio = f"{stats['hits'] / attempts:.1%}" if attempts else '尚未使用'
                lines.append(f"{label}：复用 {stats['hits']} 次，新算 {stats['misses']} 次，复用率 {ratio}；淘汰 {stats['evictions']} 次，跳过缓存 {stats['bypasses']} 次。")
            lines.append('本地缓存可丢弃重建，读取原文件与来源核验仍会执行；不会据此推算 API 费用。')
            InformationDialog("本机运行用量 · 缺失统计不显示为零", "\n".join(lines), w,
                              [r["cache_read_tokens"] for r in usage["trend"]]).exec()
            return
        if action in {"new_project", "open_project", "open_recent"}:
            if not self.confirm_discard():
                return
            if action == "open_recent":
                recent = self.store.load().get("recent_projects", [])
                if not isinstance(data.get("path"), str) or data["path"] not in recent:
                    raise CoreError("PROJECT_REQUIRED", "请从已保存的最近作品中选择，或使用打开作品。")
                self.open_project(Path(data["path"]), discard_inputs=True)
            elif action == "open_project":
                path = QFileDialog.getExistingDirectory(w, "选择已有 Novel Studio 作品文件夹")
                if path:
                    self.open_project(Path(path), discard_inputs=True)
            else:
                from .project_ui import NewProjectDialog
                dialog = NewProjectDialog(w)
                if dialog.exec():
                    parent, title = dialog.values
                    if parent.is_relative_to(ROOT):
                        raise CoreError("PROJECT_LOCATION", "请把作品保存在程序目录之外。")
                    private = [self.store.root, *self.store.capture_project_protection(ROOT)]
                    component = ROOT / "payload" / "novel-studio"
                    if not (ROOT / "BUNDLE.json").is_file():
                        component = self.cli.installed_plugin()
                    core = Core.create(parent, title, component, private)
                    core.snapshot()
                    self.open_project(core.project, discard_inputs=True, background=False)
                    w.navigate("home")
                    w.status_text.setText("作品已创建。可以直接写草稿，或从“首次 AI 协作”开始。")
            return
        if self.core is None:
            raise CoreError("PROJECT_REQUIRED", "请先创建或打开一部作品。")
        if self.job is not None:
            raise CoreError("BUSY", "当前操作仍在进行，请等待完成。")
        if action == "costs":
            from .cost_ui import CostDialog
            CostDialog(self.core, w.role, w).exec()
        elif action == "writer_settings":
            if w.editor.document().isModified():
                raise CoreError("UNSAVED_DRAFT", "请先保存当前草稿，再管理它的临时风格或章节统计范围。")
            from .creative_ui import WriterSettingsDialog
            source = w.document_id if w.document_area == "drafts" else None
            WriterSettingsDialog(self.core, source, w, tab=data.get("tab", 0)).exec()
            self.refresh_view()
        elif action == "editor_settings":
            from .creative_ui import EditorSettingsDialog
            EditorSettingsDialog(self.core, w).exec()
            self.refresh_view()
        elif action == "style_analysis":
            from .style_analysis_ui import StyleAnalysisDialog
            StyleAnalysisDialog(self.core, w).exec()
            self.refresh_view()
        elif action == "export_manuscript":
            if w.editor.document().isModified():
                raise CoreError("UNSAVED_DRAFT", "请先保存当前草稿，再导出确切版本。")
            from .export_ui import ExportDialog
            ExportDialog(self.core, w.document_id if w.document_area == "drafts" else None, w).exec()
        elif action == "transcripts":
            from .transcript_ui import TranscriptDialog
            TranscriptDialog(self.core.models, w.role, w).exec()
        elif action == "refresh_models":
            self.start_job(lambda progress: self.core.models.refresh(w.role), lambda value: self.refresh_models(), "读取模型…")
        elif action in {"set_model", "set_effort"}:
            selected = self.core.models.selected(w.role)
            if selected["provider"] == "antigravity":
                self.call(action, {"model_id": data["model_id"]} if action == "set_model" else {"effort": data["effort"]})
            else:
                self.providers.authorize_send(self.core, w.role, selected["provider"])
                config = {k: v for k, v in selected.items() if k != "provider"}
                config["model_id" if action == "set_model" else "effort"] = data["model_id" if action == "set_model" else "effort"]
                self.providers.configure(self.core, w.role, selected["provider"], config, True)
            self.refresh_models()
        elif action == "test_connection":
            selected = self.core.models.selected(w.role)
            destination = DESTINATIONS.get(selected["provider"], "Antigravity 官方宿主")
            if QMessageBox.question(w, "测试官方模型连接", "将向 " + destination + " 发送固定测试文字，可能计费。不会发送小说或沿用创作对话。是否测试？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                role = w.role
                self.start_job(lambda progress: self.core.models.diagnostic(role),
                    lambda value: InformationDialog("连接测试结果", self.connection_text(value), w).exec(), "连接测试中…")
        elif action == "new_draft":
            if self.confirm_discard():
                value = self.call("create_draft", {"text": "# 新章节\n\n"})
                self.refresh_view()
                self.load_document(value["document_id"])
                w.editor.moveCursor(QTextCursor.End)
                w.editor.setFocus()
        elif action == "select_document":
            if self.confirm_discard():
                self.load_document(data["document_id"])
        elif action == "save_draft":
            value = self.call("update_draft", {"text": data["text"], "document_id": data["document_id"], "expected_revision": data["revision"]})
            self.refresh_view()
            self.load_document(value["document_id"])
        elif action == "context_snapshot":
            self.core.snapshot()
            self.refresh_view()
            w.status_text.setText("资料副本已更新；旧副本继续保留。")
        elif action == "handoff":
            if w.editor.document().isModified():
                raise CoreError("UNSAVED_DRAFT", "请先保存这份草稿，再交给责编。")
            self.core.handoff(data["document_id"])
            w.switch_role("editor")
            self.refresh_view()
            w.status_text.setText("已创建独立审稿副本；原始草稿保持不变。")
        elif action == "feedback":
            if not self.report_id:
                raise CoreError("REPORT_REQUIRED", "请先在左侧选择一份审稿报告。")
            self.core.feedback(self.report_id, data["text"])
            w.switch_role("writer")
            w.status_text.setText("您选定的意见已交给主笔；未选意见不会自动合并。")
        elif action == "preview_promotion":
            if w.editor.document().isModified():
                raise CoreError("UNSAVED_DRAFT", "请先保存草稿，再预览正式版本差异。")
            row = self.core.document(data["document_id"])
            category = "Final-Manuscript"
            if row["area"] == "proposals":
                categories = {"人物正典": "Canon-Characters", "世界观正典": "Canon-Worldbuilding", "时间线与追踪": "Approved-Timeline"}
                chosen, ok = QInputDialog.getItem(w, "正典变更提案", "选择正式资料类别：", list(categories), 0, False)
                if not ok:
                    return
                category = categories[chosen]
            suggested = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', row["display_title"]).strip() or "新章节"
            name, ok = QInputDialog.getText(w, "正式章节名称", "保存为正式版本的文件名：", text=suggested + ".md")
            if ok:
                self.author_dialog(self.core.author_preview(data["document_id"], category=category, filename=name))
        elif action in {"import_material", "update_material", "material_policy"}:
            from .material_ui import ImportWizard
            self.core.require_idle()
            identifier = data.get("material_id") if action != "import_material" else None
            if action != "import_material" and not identifier:
                raise CoreError("MATERIAL_REQUIRED", "请先在资料库选择一份资料。")
            wizard = ImportWizard(self.core, w, identifier, policy_only=action == "material_policy")
            wizard.exec()
            if wizard.changed:
                self.core.snapshot()
                self.refresh_view()
                w.tabs.setCurrentWidget(w.material_panel)
                w.status_text.setText("资料已批准，主笔副本已更新；责编已有交接保持旧版本，需要时请重新交接。")
        elif action == "preview_material_context":
            text, receipt = self.core.materials.context(w.role, w.chat_inputs[w.role].toPlainText())
            descriptions = [r["name"] + " · v" + str(r["version"]) + "：" + str(len(r["sections"])) + " 个片段，" + str(r["provided_bytes"]) + " 字节" for r in receipt]
            message = "本轮将直接提供：\n" + ("\n".join(descriptions) or "没有全局预设资料；其他设定仍从指定快照读取。") + "\n\n常驻规则会完整提供；背景仅按当前输入检索。提供给宿主不等于模型已理解。\n\n实际选定原文：\n" + text
            InformationDialog("本轮资料范围预览 · 尚未发送模型", message, w).exec()
        elif action == "history":
            from .object_controller import history
            history(self, data)
        elif action == "send":
            role, prompt = w.role, data["text"].strip()
            if not prompt:
                return
            if w.editor.document().isModified():
                raise CoreError("UNSAVED_DRAFT", "请先保存当前草稿，再发送创作请求。")
            w.chat_inputs[role].clear()
            self.pending_prompt = (role, prompt)
            w.chat_outputs[role].appendPlainText("作者：" + prompt + "\n\n处理中…")
            operation = w.chat_operation.currentData()
            cid = (w.chat_chapter_id or w.workflow_panel.selected_chapter()) if w.chat_scope.currentData() == "chapter" else None
            if w.chat_scope.currentData() == "chapter" and not cid:
                w.chat_inputs[role].setPlainText(prompt)
                raise CoreError("CHAPTER_REQUIRED", "请先在章节目录选择一章，或将范围切换为整部作品。")
            source_kind = getattr(w, 'chat_source_kind', None) if getattr(w, 'chat_source_chapter_id', None) == cid else None
            self.start_job(lambda progress: self.core.workflow.author.send(role, prompt, operation=operation, chapter_id=cid, source_kind=source_kind, notify=progress),
                lambda value: self.author_finished(role, value), ("主笔" if role == "writer" else "责编") + "正在处理作者指令…")

    def author_finished(self, role, result):
        self.pending_prompt = None
        self.refresh_view()
        self.window.status_text.setText(result["message"])

    def author_dialog(self, preview):
        if DiffDialog(preview, self.window).exec() == DiffDialog.Accepted:
            self.core.author_commit(preview["handle"], True)
            material_restore = preview["operation"] == "restore" and preview["target"].startswith("资料版本恢复 / ")
            if material_restore:
                self.core.snapshot()
            self.refresh_view()
            self.window.status_text.setText("资料已恢复，主笔副本已更新；责编旧交接仍保持原版本，需要时请重新交接。" if material_restore else "已按您的批准更新正式作品，并保留版本历史。")

    @staticmethod
    def connection_text(value):
        return ("模型连接正常" if value["status"] == "SUCCESS" else "已收到回复，但与固定测试文字不符，请重试") + "\n\n" + \
               "所选模型：" + str(value["requested_model"]) + "\n宿主报告模型：" + str(value.get("host_model") or "未提供") + \
               "\n请求的思考程度：" + str(value.get("requested_effort") or "官方默认") + \
               "\n服务端实际思考程度：" + str(value.get("host_effort") or "宿主未提供，不能确认") + \
               f"\n响应耗时：{value['latency_seconds']:.1f} 秒\n本测试未发送小说，也没有替换创作对话。"

    def load_document(self, identifier, *, return_to=None):
        row = self.core.document(identifier)
        w = self.window
        w.set_document(identifier, row["display_title"], row["text"], row["area"], row["revision"])
        if row["area"] == "reports":
            self.report_id = identifier
            w.review_title.setText(row["display_title"])
            w.review_output.setPlainText(row["text"])
            w.show_document_page(w.review_page, return_to)
        else:
            w.show_document_page(w.document_page, return_to)
        self.refresh_enabled()
        w.interactions.refresh_document()

    def refresh_view(self, documents=None, word_plan=None):
        w = self.window
        documents = self.core.documents() if documents is None and self.core else documents
        closed_project = self.core is None and self._view_core is not None
        previous = w.tree.currentItem() if self._view_core is self.core else None
        selected_id = previous.data(0, Qt.UserRole) if previous else None
        self._view_documents, self._view_core = documents, self.core
        if self.core:
            w.project_title.setText(self.core.title)
            w.book_name.setText(self.core.title)
            w.project_note.setText("本机作品 · 双 Agent 独立工作副本")
            w.tree.clear()
            groups = {}
            names = {"drafts": "主笔草稿", "proposals": "正典变更提案", "reports": "独立审稿报告", "canonical": "正式作品（只读预览）", "writer_context": "主笔资料副本", "editor_context": "责编稿件副本"}
            for row in documents:
                if "/Materials/" in row["relative"]:
                    continue  # Structured material records have their own library UI.
                if row["area"] not in groups:
                    parent = QTreeWidgetItem([names[row["area"]]])
                    w.tree.addTopLevelItem(parent)
                    parent.setExpanded(row["area"] in {"drafts", "reports"})
                    groups[row["area"]] = parent
                item = QTreeWidgetItem([row["display_title"]])
                item.setData(0, Qt.UserRole, row["document_id"])
                groups[row["area"]].addChild(item)
                if row["document_id"] == selected_id:
                    item.parent().setExpanded(True)
                    w.tree.setCurrentItem(item)
            w.filter_tree(w.search.text())
            state = admin.ui_state(self.core.project)
            w.context_label.setText("主笔资料副本已准备" if state.get("context") else "请先导入资料，再更新副本")
            w.context_list.clear()
            w.context_list.addItems(["主笔：独立设定副本", "责编：独立稿件与设定副本" if state.get("review") else "责编：尚未交接稿件", "正式作品：需要作者批准"])
            workflow_state = self.core.workflow.store.read()
            for role in ("writer", "editor"):
                history = self.core.models.preferences(role).get("history", [])
                w.chat_outputs[role].setPlainText("\n\n".join(transcript_text(r, role, workflow_state) for r in history))
                w.interactions.bind_history(role, history, workflow_state)
            w.material_panel.bind(self.core)
        else:
            w.tree.clear()
            w.search.clear()
            w.material_panel.bind(None)
            if w.document_id is not None:
                w.set_document(None, "请选择作品与文稿", "", None, None)
            w.review_output.clear()
            w.review_title.setText("独立审稿报告")
            self.report_id = None
            if closed_project:
                w.document_origin = "files"
                w.chat_chapter_id = None
                w.context_list.clear()
                w.feedback_input.clear()
                for view in (*w.chat_inputs.values(), *w.chat_outputs.values()):
                    view.clear()
                self.pending_prompt = None
            w.project_title.clear()
            w.book_name.setText("开始一部作品")
            w.project_note.setText("新建作品或打开已有工程。小说保存在您选择的位置。")
            w.editor.setReadOnly(True)
            w.chapter_title.setText("让创作保持专注")
        self.refresh_models()
        self.refresh_metrics()
        self.refresh_enabled()
        self.refresh_context_receipt()
        self.refresh_word_plan(documents, word_plan)
        w.workflow_panel.bind(self.core, self.job is not None, documents)
        local_state = self.store.load()
        recent = local_state.get("recent_projects", [])
        titles = local_state.get('project_titles', {})
        w.workflow_panel.recent_projects.clear()
        if isinstance(recent, list):
            from PySide6.QtWidgets import QListWidgetItem
            for path in recent:
                if isinstance(path, str) and (not self.core or Path(path) != self.core.project):
                    item = QListWidgetItem(titles.get(path, Path(path).name))
                    item.setData(Qt.UserRole, path)
                    item.setToolTip(path)
                    w.workflow_panel.recent_projects.addItem(item)
        panel = w.workflow_panel
        has_recent = panel.recent_projects.count() > 0
        has_archived = bool(local_state.get('archived_projects'))
        panel.recent_heading.setText("最近作品" if has_recent else "作品归档")
        panel.recent_projects.setVisible(has_recent)
        panel.recent_open.setVisible(has_recent)
        panel.recent_archives.setVisible(has_archived)
        panel.recent_section.setVisible(has_recent or has_archived)
        w.refresh_shell()
        w.interactions.refresh()
        if self._completing_job:
            self._job_refreshed_core = self.core

    def refresh_word_plan(self, documents=None, word_plan=None):
        self.word_plan = None
        if self.core:
            if self.core.creative.state().get("length_plan"):
                self.word_plan = word_plan if word_plan is not None else self.core.lengths.counts(documents)
            self.window.count_metric = self.core.lengths.plan()["metric"]
        self.window.measure_current()

    def show_word_goal(self, current_count):
        w = self.window
        if not self.core or not self.word_plan:
            w.word_goal_label.setText("字数目标尚未设置 · 在主笔设置中管理；当前统计不含格式和空白")
            w.word_progress.setValue(0)
            w.word_progress.setFormat("未设目标")
            return
        from ..lengths import evaluate
        context = self.core.lengths.context(w.document_id)
        value = self.word_plan["nodes"][context["node_id"]]
        node = self.core.lengths.plan()["nodes"][context["node_id"]]
        if w.document_id and node["kind"] in {"chapter", "scene"} and (node.get("source") or {}).get("document_id") != w.document_id:
            w.word_goal_label.setText("当前文稿未关联此目标。计划「" + context["title"] + "」统计的是另一版本，请在字数管理中关联；全书已选章节累计 " + str(self.word_plan["nodes"]["book"]["count"]))
            w.word_progress.setValue(0)
            w.word_progress.setFormat("未关联")
            return
        if node["kind"] == "chapter" and (node.get("source") or {}).get("document_id") == w.document_id:
            value = {**value, **evaluate(current_count, context["goal"])}
        book = self.word_plan["nodes"]["book"]
        status = {"UNSET": "未设目标", "UNDER": "不足", "OVER": "超过", "WITHIN": "范围内"}[value["status"]]
        w.word_goal_label.setText(context["level"] + "「" + context["title"] + "」 " + str(value["count"]) + " / " + str(value["target"] if value["target"] is not None else "未设") +
            " · " + status + " · 剩余 " + str(value["remaining"] if value["remaining"] is not None else "—") + " · 全书已选章节累计 " + str(book["count"]) + ("（有未关联正文）" if book["missing_sources"] else ""))
        ratio = value["progress"]
        w.word_progress.setValue(min(1000, int(ratio * 1000)) if ratio is not None else 0)
        w.word_progress.setFormat(f"{ratio:.1%}" if ratio is not None else "未设目标")
        w.word_goal_label.setToolTip(self.word_plan["description"] + "\n全书累计基于已保存的选定章节；当前章节计数可以包含尚未保存的编辑。")

    def refresh_context_receipt(self):
        if not self.core:
            return
        record = self.core.models.preferences(self.window.role)
        receipt = record.get("context_receipt", [])
        if receipt:
            self.window.context_list.clear()
            for material in receipt:
                always = sum(s["mode"] == "always" for s in material["sections"])
                background = sum(s["mode"] == "retrieval" for s in material["sections"])
                scope = "全文模式" if material["context_mode"] == "full" else f"常驻 {always} 节 / 检索 {background} 片段"
                self.window.context_list.addItem(material["name"] + " · v" + str(material["version"]) + "\n" + scope + "\n上次成功回合已提供；不代表模型理解")
            for item in record.get("context_tool_reads", []):
                if "relative" in item:
                    description = "宿主工具实读：" + Path(item["relative"]).name
                else:
                    description = "API 受控工具实读：" + str(item.get("document_id") or item.get("reference_id") or item.get("material_id") or "已批准资料")[:16]
                self.window.context_list.addItem(description)

    def refresh_models(self):
        w = self.window
        if not self.core:
            return
        p = self.core.models.selected(w.role)
        rows = self.core.models.catalog_for(w.role)
        w.model_selector.clear()
        if not rows:
            w.model_selector.addItem(p.get("model_id") or "点击刷新，读取官方模型列表", p.get("model_id"))
        else:
            w.model_selector.addItem("选择官方账号模型…", None)
            for row in rows:
                w.model_selector.addItem(row["display_name"], row["model_id"])
            index = w.model_selector.findData(p.get("model_id"))
            w.model_selector.setCurrentIndex(max(0, index))
            if index < 0 and p.get("model_id"):
                w.model_selector.addItem(p["model_id"] + " · 手动 ID，未核验", p["model_id"])
                w.model_selector.setCurrentIndex(w.model_selector.count() - 1)
        w.model_selector.setToolTip("当前接收方：" + DESTINATIONS.get(p["provider"], "Antigravity 官方宿主"))
        w.model_selector.setEnabled(bool(rows) and self.job is None)
        w.effort_selector.clear()
        w.effort_selector.addItem("官方默认", None)
        efforts = EFFORTS if p["provider"] == "antigravity" else self.providers.capability(p["provider"], p.get("model_id")).get("effort_levels", [])
        for effort in efforts:
            w.effort_selector.addItem(effort + " · 请求值", effort)
        w.effort_selector.setCurrentIndex(max(0, w.effort_selector.findData(p.get("effort"))))
        w.effort_selector.setToolTip("这是 CLI 支持的请求档位；当前模型是否可调整，请在诊断中核验。宿主拒绝时不会静默降级。")
        w.effort_selector.setEnabled(self.job is None and bool(efforts))

    def refresh_enabled(self):
        w = self.window
        ready = self.core is not None and self.job is None
        w.cost_action.setEnabled(ready)
        w.usage_action.setEnabled(ready)
        w.editor.setReadOnly(not ready or w.role != "writer" or w.document_area != "drafts")
        for item in [w.import_button, w.history_button, w.context_button, w.refresh_models_button, w.metrics_button, w.material_context_button]:
            item.setEnabled(ready)
        w.new_project_button.setEnabled(self.job is None)
        w.open_project_button.setEnabled(self.job is None)
        w.model_manager_button.setEnabled(self.job is None)
        w.writer_settings_button.setEnabled(ready)
        w.writer_settings_button.setVisible(w.role == "writer")
        w.editor_settings_button.setEnabled(ready)
        w.editor_settings_button.setVisible(w.role == "editor")
        w.style_analysis_button.setEnabled(ready)
        w.word_settings_button.setEnabled(ready and w.role == "writer")
        w.new_draft_button.setEnabled(ready and w.role == "writer")
        w.resume_document.setVisible(self.core is not None and w.document_area == "drafts")
        w.save_button.setEnabled(ready and w.role == "writer" and w.document_area == "drafts")
        w.handoff_button.setEnabled(ready and w.document_area == "drafts")
        w.approval_button.setEnabled(ready and w.document_area in {"drafts", "proposals"})
        w.feedback_button.setEnabled(ready and self.report_id is not None)
        chosen = bool(self.core and self.core.models.selected(w.role).get("model_id"))
        w.connection_button.setEnabled(ready and chosen)
        w.material_panel.import_button.setEnabled(ready)
        w.material_panel.more_button.setEnabled(ready and w.material_panel.current is not None)
        if getattr(w, "export_widget", None) is not None:
            w.export_widget.setEnabled(ready)
        for r, item in w.send_buttons.items():
            item.setEnabled(ready and bool(self.core and self.core.models.selected(r).get("model_id")))

    def refresh_metrics(self):
        if not self.core:
            return
        data = self.core.models.usage(self.window.role)
        for key, name in [("input_tokens", "输入"), ("output_tokens", "输出"), ("cache_read_tokens", "缓存读取")]:
            value = data["latest"][key]
            self.window.metrics[key].setText(name + " " + (str(value) if value is not None else "—"))
        share = data["latest"].get("cache_read_share")
        self.window.metrics["hit_rate"].setText("读取占比 " + (f"{share:.1%}" if share is not None else "—"))
        seconds = data["latency_seconds"]
        self.window.metrics["latency"].setText("耗时 " + (f"{seconds:.1f}s" if isinstance(seconds, (int, float)) else "—"))

    def progress(self, event):
        if event.get('type') == 'compatibility_probe':
            self.window.run_label.setText('合成兼容验证 ' + str(event['request']) + '/' + str(event['maximum']) + ' · ' + ('主笔' if event['role'] == 'writer' else '责编'))
            return
        if event.get("role") and event["role"] != self.window.role:
            return
        if event["type"] == "tool":
            self.window.run_label.setText("读取资料…")
        elif event["type"] in {"usage", "api_usage"}:
            for key, name in [("input_tokens", "已观察输入"), ("output_tokens", "输出"), ("cache_read_tokens", "缓存读取")]:
                value = event["observed"].get(key)
                self.window.metrics[key].setText(name + " " + (str(value) if value is not None else "—"))

    def run_finished(self, role, result):
        self.pending_prompt = None
        if result.get("artifact") and self.window.editor.document().isModified():
            self.window.status_text.setText("新产物已保存并列入作品目录；当前未保存的编辑继续保留，请先保存后再打开新产物。")
            return
        if result.get("artifact"):
            self.load_document(result["artifact"]["document_id"])
        check = result.get("length_check")
        detail = "本稿本地计数 " + str(check["count"]) + "，目标状态：" + {"UNSET": "未设置单稿目标", "UNDER": "不足，可由作者决定续写", "OVER": "超过，可由作者决定精简", "WITHIN": "范围内"}[check["status"]] + "。" if check else ""
        if result.get("review_check"):
            checked = result["review_check"]
            detail = "报告引用核验：" + str(checked["verified_quotes"]) + " 条匹配，" + (str(checked["unverified_quotes"]) + " 条待核验。" if checked["unverified_quotes"] is not None else "结构待核验。") + "判断仍由作者复核。"
        self.window.status_text.setText(detail + "本回合已结束 · 产物保存在工作副本中，正式作品未自动更新。")

    def session_binding_diagnostic(self):
        try:
            result = self.core.validate_permissions()
            component_available = True
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            try:
                result = self.core.validate_permissions(verify_plugin=False)
            except (OSError, ValueError, KeyError, TypeError):
                return {"component_status": "会话配置无法核对", "detail": "请先检查作品配置与备份。"}
            component_available = False
        if not result["legacy_profiles_match"]:
            return {**result, "component_status": "会话配置与作品记录不一致", "detail": "请先检查作品配置与备份。"}
        if not component_available:
            return {**result, "component_status": "作品绑定的组件不可用",
                    "detail": "可以继续本地阅读和写作。依赖旧组件路径的 Antigravity 对话需要先恢复原位置的完整组件。"}
        return {**result, "component_status": "已核对本地组件", "detail": "官方连接状态以实际兼容检查为准。"}

    def diagnostics(self):
        data = ({"version": self.call("get_version"), "cli_version": getattr(self.cli, "last_version", None), "permissions": self.session_binding_diagnostic(),
                "agents": self.call("list_agents", role="observer"), "models": self.core.models.catalog_rows,
                "usage": self.call("get_usage"), "logs": self.call("get_logs"),
                "client_prompt_compilation": {role: self.core.models.preferences(role).get("compilation", "NOT TESTED") for role in ("writer", "editor")},
                "privacy": "本报告不含小说、提示词、密钥或完整本机路径；宿主 AI 交互仍受官方数据政策约束。"} if self.core else
                "安装与连接\n\n可以直接新建作品和写草稿。\n使用 Antigravity 时，请安装官方连接程序和本地写作组件，然后读取官方模型目录。\n作品保存在您选择的文件夹；宿主 AI 交互受官方数据政策约束。")
        dialog = InformationDialog("开发者与 Agent 诊断 · 默认只读", data, self.window)
        config_history = dialog.add_action('查看本机配置恢复记录')
        config_history.clicked.connect(lambda:self._guarded(lambda:InformationDialog(
            '本机配置恢复记录（最多显示 50 条；完整记录保留在状态目录）', self.store.recovery_records(), self.window).exec()))
        protected = dialog.add_action('保护其他文件夹')
        protected.setEnabled(self.job is None)
        def configure_protection():
            from .local_config_ui import ProtectedFoldersDialog
            config = self.store.protection_config(ROOT)
            dialog = ProtectedFoldersDialog(list(map(str, config['paths'])), self.window)
            if dialog.exec():
                self.store.add_protected_folders(ROOT, dialog.paths, config['revision'])
                self.window.status_text.setText('本机保护文件夹已保存，用于之后新建的作品。')
        protected.clicked.connect(lambda:self._guarded(configure_protection))
        from PySide6.QtWidgets import QDialogButtonBox
        export = dialog.add_action("导出脱敏报告")
        def save():
            path, _ = QFileDialog.getSaveFileName(dialog, "保存脱敏诊断报告", "Novel-Studio-诊断.json", "JSON (*.json)")
            if path:
                target = Path(path)
                with SafeTree(target.parent) as tree:
                    tree.create(target.name, admin.encode(data))
                QMessageBox.information(dialog, "已保存", "诊断报告已保存在您选择的位置。已有文件不会覆盖。")
        export.clicked.connect(lambda: self._guarded(save))
        effort = dialog.add_action("核验当前模型思考档位")
        effort.setEnabled(self.core is not None and self.job is None)
        effort.clicked.connect(lambda: self._guarded(lambda: InformationDialog("官方思考档位报告", self.core.models.inspect_effort(self.window.role), dialog).exec()))
        check = dialog.add_action("一键检查作品与资料副本")
        check.setEnabled(self.core is not None and self.job is None)
        check.clicked.connect(lambda: self._guarded(lambda: InformationDialog("只读自检结果", self.call("run_self_test"), dialog).exec()))
        install = dialog.add_action("安装／更新写作组件")
        install.setEnabled(self.job is None)
        def component():
            if QMessageBox.question(dialog, "安装本地组件", "将通过官方 CLI 验证并安装此程序自带的 Novel Studio 组件。会更新同名插件，不修改全局权限或系统设置。是否继续？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            source = ROOT / "payload/novel-studio"
            if not source.is_dir():
                raise CoreError("BUNDLE_REQUIRED", "请在打包后的桌面程序中安装组件；源码目录不会被直接安装。")
            dialog.accept()
            def operation(progress):
                self.cli.command(["plugin", "validate", "--strict", str(source), "--json"])
                output = self.cli.command(["plugin", "install", str(source)])
                return "官方验证与安装已结束：\n\n" + output
            self.start_job(operation, lambda value: InformationDialog("组件安装结果", value, self.window).exec(), "安装组件…")
        install.clicked.connect(lambda: self._guarded(component))
        desktop_install = dialog.add_action("安装到本机并创建桌面入口")
        desktop_install.setEnabled(self.job is None)
        def install_desktop():
            if QMessageBox.question(dialog, "安装桌面程序", "将校验并复制本程序到当前用户的应用数据目录，创建一个 Novel Studio Desktop 桌面入口。保留旧版程序和已有快捷方式，不复制您的小说。是否继续？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            from ..deployment import install
            dialog.accept()
            self.start_job(lambda progress: install(ROOT, self.store.root / "DesktopInstall", progress),
                           lambda value: InformationDialog("本机安装完成", value, self.window).exec(), "安装桌面程序…")
        desktop_install.clicked.connect(lambda: self._guarded(install_desktop))
        dialog.exec()

    def _guarded(self, operation):
        try:
            operation()
        except Exception as error:
            self.show_error(error)
