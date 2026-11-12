"""Qt author-only official API setup. Secret text never becomes a model field."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QApplication, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)
from ..api_service import ProviderRegistry
from ..providers import DESTINATIONS, safe_error
from ..core import CoreError
from ..model_limits import MAX_TOKEN_PARAMETER
from .dialogs import InformationDialog
from .jobs import Job


class ProviderDialog(QDialog):
    def __init__(self, registry: ProviderRegistry, core=None, role="writer", parent=None):
        super().__init__(parent)
        self.registry, self.core, self.job = registry, core, None
        self.setWindowTitle("模型管理 · 官方直连与本机安全凭据")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(800, screen.width() - 40), min(830, screen.height() - 40))
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        intro = QLabel("主笔和责编分别配置。默认可继续使用 Antigravity，无需额外购买 API。\nAPI 仅直连官方地址，费用与数据处理适用供应商政策；不使用代理、中转或自动收费重试。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.role = QComboBox()
        self.role.addItem("主笔 · 草稿与提案", "writer")
        self.role.addItem("责编 · 独立审稿报告", "editor")
        self.role.setCurrentIndex(0 if role == "writer" else 1)
        self.provider = QComboBox()
        for value, name in [("antigravity", "Antigravity · 官方宿主"), ("openai", "OpenAI · 官方 API"), ("anthropic", "Anthropic · 官方 API"), ("gemini", "Gemini · 官方 API")]:
            self.provider.addItem(name, value)
        form.addRow("当前角色", self.role)
        form.addRow("连接方式", self.provider)
        self.destination = QLabel()
        self.destination.setTextFormat(Qt.PlainText)
        self.destination.setWordWrap(True)
        form.addRow("实际接收方", self.destination)
        layout.addLayout(form)
        self.key_box = QGroupBox("本机密钥 · Windows 凭据管理器")
        key_layout = QVBoxLayout(self.key_box)
        self.key_state = QLabel()
        key_layout.addWidget(self.key_state)
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.Password)
        self.secret.setPlaceholderText("仅在此填写 API Key；不粘贴到聊天或正文")
        self.secret.setMaxLength(2560)
        key_layout.addWidget(self.secret)
        self.both = QCheckBox("此密钥同时用于我本机的主笔与责编（两个角色配置仍独立）")
        key_layout.addWidget(self.both)
        row = QHBoxLayout()
        self.save_key = QPushButton("保存到 Windows 安全存储")
        self.remove_key = QPushButton("移除此角色密钥")
        self.save_key.clicked.connect(self.store_key)
        self.remove_key.clicked.connect(self.delete_key)
        row.addWidget(self.save_key)
        row.addWidget(self.remove_key)
        key_layout.addLayout(row)
        layout.addWidget(self.key_box)
        models = QFormLayout()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.NoInsert)
        self.model.setPlaceholderText("刷新官方目录，或填写准确模型 ID")
        self.refresh = QPushButton("读取此供应商的官方模型目录")
        self.refresh.clicked.connect(self.refresh_catalog)
        models.addRow("模型 ID", self.model)
        models.addRow("", self.refresh)
        self.capability = QLabel()
        self.capability.setWordWrap(True)
        models.addRow("已核验能力", self.capability)
        self.effort = QComboBox()
        models.addRow("思考程度", self.effort)
        self.maximum = QSpinBox()
        self.maximum.setRange(0, MAX_TOKEN_PARAMETER)
        self.maximum.setSpecialValueText("自动：使用模型官方最大值")
        models.addRow("输出 Token 上限", self.maximum)
        self.use_maximum = QPushButton('使用官方最大值')
        self.use_maximum.clicked.connect(lambda: self.maximum.setValue(0))
        models.addRow('', self.use_maximum)
        limit_note = QLabel('上下文不再按软件的低额度压缩；仅保留32 MiB本机传输保护。输出上限不要求写满，仍按故事需要生成；输入与输出可能共用模型窗口。未知能力会明确标记，使用官方目录核验。')
        limit_note.setWordWrap(True)
        models.addRow('', limit_note)
        self.cache = QComboBox()
        models.addRow("缓存策略", self.cache)
        self.ttl = QComboBox()
        models.addRow("缓存有效期", self.ttl)
        layout.addLayout(models)
        self.consent = QCheckBox("我同意主动发起 AI 操作时，将此角色所选资料和对话发送至上方接收方。")
        self.consent.setChecked(False)
        layout.addWidget(self.consent)
        self.notice = QLabel("读取目录不发送小说；连接测试仅发送固定短句，可能收费。\n创作工具处理每轮最多 6 次官方请求，每次可能计费；超限暂停，没有自动重试或预热。\nAPI 历史保存在本项目，与 Antigravity CID 分开；更换供应商/模型保留旧历史，不自动转移其原生协议。")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.status = QLabel("尚未发出网络请求")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        self.test = buttons.addButton("测试官方连接（可能收费）", QDialogButtonBox.ActionRole)
        self.new_session = buttons.addButton("新建 API 对话", QDialogButtonBox.ActionRole)
        self.apply = buttons.addButton("应用到此角色", QDialogButtonBox.ActionRole)
        self.test.clicked.connect(self.test_connection)
        self.apply.clicked.connect(self.apply_config)
        self.new_session.clicked.connect(self.new_conversation)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.role.currentIndexChanged.connect(self.role_changed)
        self.provider.currentIndexChanged.connect(self.provider_changed)
        self.model.currentTextChanged.connect(self.model_changed)
        self.cache.currentIndexChanged.connect(self.cache_changed)
        self.role_changed()

    def identity(self):
        return self.role.currentData(), self.provider.currentData()

    def role_changed(self):
        role = self.role.currentData()
        p = self.core.models.selected(role) if self.core else {"provider": "antigravity"}
        self.provider.blockSignals(True)
        self.provider.setCurrentIndex(max(0, self.provider.findData(p["provider"])))
        self.provider.blockSignals(False)
        self.provider_changed()

    def provider_changed(self):
        role, provider = self.identity()
        self.secret.clear()
        self.both.setChecked(False)
        self.consent.setChecked(False)
        native = provider == "antigravity"
        self.destination.setText("Antigravity 官方模型连接（由宿主决定实际模型接收方）" if native else DESTINATIONS[provider])
        self.key_box.setEnabled(not native)
        self.refresh.setEnabled(not native or self.core is not None)
        self.test.setEnabled(not native)
        self.consent.setEnabled(not native and self.core is not None)
        self.apply.setEnabled(self.core is not None)
        self.new_session.setEnabled(not native and self.core is not None)
        self.load_catalog()
        self.update_key_status()

    def update_key_status(self):
        role, provider = self.identity()
        try:
            self.registry.reference(role, provider)
            message = "已绑定本角色的本机凭据；不会在界面回显密钥。"
        except CoreError:
            message = "此角色尚未配置该供应商的本机密钥。"
        self.key_state.setText(message)

    def guarded(self, operation):
        try:
            return operation()
        except Exception as error:
            self.status.setText(str(safe_error(error)))
            return None

    def store_key(self):
        role, provider = self.identity()
        try:
            value = self.guarded(lambda: self.registry.save_key(role, provider, self.secret.text(), self.both.isChecked()))
            if value:
                self.status.setText("已保存到 Windows 凭据管理器。未发出网络请求；普通配置只保存凭据引用。")
        finally:
            self.secret.clear()
        self.update_key_status()

    def delete_key(self):
        role, provider = self.identity()
        if QMessageBox.question(self, "移除本机密钥", "仅移除此角色的 Novel Studio 凭据绑定；如果另一个角色仍在使用，不会删除共用记录。是否继续？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.guarded(lambda: self.registry.remove_key(role, provider))
        self.update_key_status()

    def load_catalog(self):
        role, provider = self.identity()
        native = provider == "antigravity"
        saved = self.core.models.preferences(role) if self.core else {}
        p = saved if native else saved.get("api_configs", {}).get(provider, {})
        selected = p.get("model_id", "")
        self.model.blockSignals(True)
        self.model.setEditable(not native)
        self.model.clear()
        if native:
            self.model.addItem("选择官方目录中的模型", None)
            for row in self.core.models.catalog_rows if self.core else []:
                self.model.addItem(row.get("display_name", row["model_id"]), row["model_id"])
            index = self.model.findData(selected)
            if index < 0 and selected:
                self.model.addItem(selected + " · 已保存", None)
                index = self.model.count() - 1
            self.model.setCurrentIndex(max(0, index))
        else:
            for row in self.registry.catalog(provider):
                self.model.addItem(row["model_id"])
            self.model.setCurrentText(selected)
        self.model.blockSignals(False)
        self.maximum.setValue(p.get("max_output_tokens") or 0)
        self.model.setEnabled(not native or self.core is not None)
        self.model_changed()
        self.effort.setCurrentIndex(max(0, self.effort.findData(p.get("effort"))))
        self.cache.setCurrentIndex(max(0, self.cache.findData(p.get("cache", {}).get("mode", "provider_default"))))
        self.ttl.setCurrentIndex(max(0, self.ttl.findData(p.get("cache", {}).get("ttl"))))

    def model_changed(self):
        _, provider = self.identity()
        cap = self.registry.capability(provider, self.model.currentText().strip())
        native = provider == 'antigravity'
        self.maximum.setEnabled(not native)
        self.use_maximum.setEnabled(not native)
        if native:
            self.maximum.setSpecialValueText('由 Antigravity 宿主管理')
            self.capability.setText('上下文与输出由 Antigravity 管理；Novel Studio 不另设 token 上限。当前 CLI 没有公开的调节参数，不能将 API 上限当作宿主承诺。')
        else:
            self.maximum.setSpecialValueText('自动：使用模型官方最大值')
            self.capability.setText('输入/上下文窗口：' + str(cap.get('max_input_tokens') or cap.get('context_window_tokens') or '官方未提供') + '；最大输出：' + str(cap.get('max_output_tokens') or '未知，请刷新官方目录') + '\n' + '已知上限自动取最大；未知上限不会伪报。此前手动值可点击“使用官方最大值”恢复自动。')
        self.effort.clear()
        self.effort.addItem("官方默认", None)
        for value in cap.get("effort_levels", []):
            self.effort.addItem(value, value)
        self.effort.setEnabled(bool(cap.get("effort_levels")))
        self.cache.clear()
        self.cache.addItem("官方默认 · 不预热", "provider_default")
        cache = cap.get("cache", {})
        if cache.get("explicit") is True:
            self.cache.addItem("显式缓存 · 可能产生写入费用", "explicit")
        self.cache.setEnabled(provider != "antigravity")
        self.cache_changed()

    def cache_changed(self):
        _, provider = self.identity()
        self.ttl.clear()
        self.ttl.addItem("供应商默认", None)
        cap = self.registry.capability(provider, self.model.currentText().strip())
        for value in cap.get("cache", {}).get("ttl_values", []):
            self.ttl.addItem(value, value)
        self.ttl.setEnabled(self.cache.currentData() == "explicit")

    def config(self):
        mode = self.cache.currentData() or "provider_default"
        return {"model_id": self.model.currentText().strip(), "effort": self.effort.currentData(),
                "max_output_tokens": self.maximum.value() or None,
                "cache": {"mode": mode, **({"ttl": self.ttl.currentData()} if mode != "provider_default" and self.ttl.currentData() else {})}}

    def apply_config(self):
        if not self.core:
            return
        role, provider = self.identity()
        def operation():
            if provider == "antigravity" and self.model.currentData():
                self.core.models.select_native_model(role, self.model.currentData())
            else:
                self.registry.configure(self.core, role, provider, self.config(), self.consent.isChecked())
            self.status.setText("已应用到此角色。没有发送资料；下一次点击发送时使用新配置，旧对话历史保留。")
        self.guarded(operation)

    def new_conversation(self):
        role, provider = self.identity()
        if QMessageBox.question(self, "保留旧历史并新建对话", "下一轮将使用空的 API 原生对话上下文。旧稿件、审稿报告、本地历史和旧对话文件都保留；本操作不发送网络请求。是否继续？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if self.guarded(lambda: (self.registry.new_conversation(self.core, role, provider, self.model.currentText().strip()), True)[1]):
            self.status.setText("已为此角色安排新的 API 对话；旧历史仍保留。")

    def background(self, operation, complete):
        if self.job is not None:
            return
        self.job = Job(lambda notify: operation(), self)
        for widget in (self.role, self.provider, self.model, self.save_key, self.remove_key, self.refresh, self.test, self.apply, self.new_session, self.effort, self.maximum, self.use_maximum, self.cache, self.ttl):
            widget.setEnabled(False)
        self.job.finished_value.connect(complete)
        self.job.failed.connect(lambda error: self.status.setText(str(safe_error(error))))
        self.job.finished.connect(self.job_finished)
        self.status.setText("正在连接上方官方地址；没有自动重试，请等待结果。")
        self.job.start()

    def job_finished(self):
        old, self.job = self.job, None
        old.deleteLater()
        effort, cache, ttl = self.effort.currentData(), self.cache.currentData(), self.ttl.currentData()
        for widget in (self.role, self.provider, self.model, self.save_key, self.remove_key, self.refresh, self.test, self.apply, self.new_session, self.effort, self.maximum, self.cache, self.ttl):
            widget.setEnabled(True)
        self.model_changed()
        self.effort.setCurrentIndex(max(0, self.effort.findData(effort)))
        self.cache.setCurrentIndex(max(0, self.cache.findData(cache)))
        self.ttl.setCurrentIndex(max(0, self.ttl.findData(ttl)))
        self.apply.setEnabled(self.core is not None)
        native = self.provider.currentData() == "antigravity"
        self.model.setEnabled(not native or self.core is not None)
        self.refresh.setEnabled(not native or self.core is not None)
        self.test.setEnabled(not native)
        self.new_session.setEnabled(self.core is not None and not native)

    def refresh_catalog(self):
        role, provider = self.identity()
        if provider == "antigravity":
            if self.core is not None:
                self.background(self.core.models.refresh, self.catalog_finished)
        else:
            self.background(lambda: self.registry.refresh(role, provider), self.catalog_finished)

    def catalog_finished(self, rows):
        self.load_catalog()
        self.status.setText(f"官方目录返回 {len(rows)} 个模型。目录可见不等于账号能成功生成，请单独测试。")

    def test_connection(self):
        role, provider = self.identity()
        config = self.config()
        if QMessageBox.question(self, "确认可能收费的官方连接测试", "接收方：" + DESTINATIONS[provider] + "\n模型：" + config["model_id"] + "\n仅发送固定短句，不含小说，不续接创作历史。供应商可能计费；本程序不自动重试。是否测试？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.background(lambda: self.registry.diagnostic(role, provider, config), self.test_finished)

    def test_finished(self, value):
        self.status.setText("连接测试：" + value["status"])
        InformationDialog("官方 API 测试结果 · 不含小说或密钥", value, self).exec()

    def reject(self):
        if self.job is not None:
            self.status.setText("请求尚未结束，请等待结果后关闭，避免后台请求无人查看。")
            return
        self.secret.clear()
        super().reject()

    def closeEvent(self, event):
        if self.job is not None:
            event.ignore()
        else:
            self.secret.clear()
            self.reject()
            event.accept()
