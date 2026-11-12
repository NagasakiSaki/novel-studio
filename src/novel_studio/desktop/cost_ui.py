"""Local rate entry and conservative cost ledger, no credential access."""
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QDateEdit, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget
from ..core import CoreError
from ..costs import CostService, RATE_FIELDS, REASONS


class CostDialog(QDialog):
    def __init__(self, core, role="writer", parent=None):
        super().__init__(parent)
        self.service, self.role = CostService(core), role
        self.setWindowTitle("运行监控 · 费用估算与模型价格")
        self.resize(1040, 800)
        layout = QVBoxLayout(self)
        note = QLabel("只在本机计算，不读取密钥或发请求。单价单位为每一百万 Token；空白表示未知，零只用于已确认免费项。")
        note.setWordWrap(True)
        layout.addWidget(note)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        summary = QWidget()
        summary_layout = QVBoxLayout(summary)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        summary_layout.addWidget(self.summary)
        tabs.addTab(summary, "当前角色费用估算")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        settings = QWidget()
        form = QFormLayout(settings)
        self.provider = QComboBox()
        for text, key in (("OpenAI 官方 API", "openai"), ("Anthropic 官方 API", "anthropic"), ("Gemini 官方 API", "gemini")):
            self.provider.addItem(text, key)
        self.model = QLineEdit()
        self.model.setPlaceholderText("准确模型 ID；设置价格不会切换创作模型")
        chosen = core.models.selected(role) if core.models else {}
        if chosen.get("provider") in {"openai", "anthropic", "gemini"}:
            self.provider.setCurrentIndex(self.provider.findData(chosen["provider"]))
            self.model.setText(chosen.get("model_id", ""))
        form.addRow("供应商", self.provider)
        form.addRow("模型", self.model)
        load = QPushButton("加载该模型已保存价格")
        load.clicked.connect(self.load)
        form.addRow(load)
        self.source = QLineEdit()
        self.source.setPlaceholderText("官方价格页或合同报价出处；本程序不会打开该地址")
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.currency = QComboBox()
        self.currency.setEditable(True)
        self.currency.addItems(["USD", "CNY", "EUR", "GBP", "JPY"])
        form.addRow("价格来源", self.source)
        form.addRow("查询日期", self.date)
        form.addRow("币种（不换算）", self.currency)
        self.rates = {}
        for key, text in RATE_FIELDS.items():
            entry = QLineEdit()
            entry.setPlaceholderText("未知请留空")
            self.rates[key] = entry
            form.addRow(text, entry)
        self.uniform = QCheckBox("来源明确说明所有缓存写入期限使用同一费率（否则不要勾选）")
        form.addRow(self.uniform)
        save = QPushButton("保存本机估算价格并重新计算")
        save.clicked.connect(self.save)
        form.addRow(save)
        scroll.setWidget(settings)
        tabs.addTab(scroll, "作者确认的模型价格")
        self.status = QLabel("未配置价格时显示未知，不使用演示单价。Antigravity 订阅不换算成 API 费用。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    def guarded(self, action):
        try:
            return action()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "费用估算", str(error) if isinstance(error, CoreError) else "本地价格记录无法安全读取，未更改原记录。")

    def load(self):
        def operation():
            value = self.service.prices(self.provider.currentData(), self.model.text()) or {}
            self.source.setText(value.get("source", ""))
            self.currency.setCurrentText(value.get("currency", "USD"))
            self.date.setDate(QDate.fromString(value["as_of"], "yyyy-MM-dd") if value.get("as_of") else QDate.currentDate())
            self.uniform.setChecked(value.get("write_rate_scope") == "uniform_all_ttl")
            for key, entry in self.rates.items():
                entry.setText(value.get(key, ""))
            self.status.setText("已加载本机记录；请核对供应商、模型、价格来源及日期。" if value else "此模型尚未配置价格。")
        self.guarded(operation)

    def save(self):
        def operation():
            value = {"source": self.source.text(), "currency": self.currency.currentText(), "as_of": self.date.date().toString("yyyy-MM-dd"),
                **{key: field.text().strip() for key, field in self.rates.items()}, "write_rate_scope": "uniform_all_ttl" if self.uniform.isChecked() else None}
            self.service.save(self.provider.currentData(), self.model.text().strip(), value)
            self.status.setText("已保存本机价格。估算会按当前价格重新计算，不能当作历史账单。")
            self.refresh()
        self.guarded(operation)

    def refresh(self):
        report = self.service.report(self.role)
        text = ["主笔" if self.role == "writer" else "责编", report["scope"], report["invoice"], ""]
        if not report["rows"]:
            text.append("暂无本角色 API 用量数据，费用未知。不会把 Antigravity 额度换算成金额。")
        for currency, group in report["groups"].items():
            text.append(currency + " · " + ("已知项小计 " + group["known_subtotal"] + "；可估 " + str(group["known"]) + " 项，" if group["known"] else "费用未知；") + "未知 " + str(group["unknown"]) + " 项。存在未知项时总额仍未知。")
        for row in reversed(report["rows"][-50:]):
            text.append(("独立分析/诊断" if row["diagnostic"] else "创作回合") + " · " + row["provider"] + " / " + row["model"] + " · " + str(row["status"]) + "\n" +
                (row["estimated_cost"] + " " + row["currency"] + "（估算）· " + row["source"] + " · " + row["as_of"] if row["estimated_cost"] is not None else "费用未知：" + REASONS.get(row.get("reason"), "无可核验数据")))
        self.summary.setPlainText("\n\n".join(text))
