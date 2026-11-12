"""Author-facing capability status. Inspection never runs a model probe."""
import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QPushButton, QMessageBox, QInputDialog
from ..core import CoreError, SafeTree, admin, digest
from ..compatibility_store import protect
from ..version_info import versions
from ..compatibility_probe import run
from .. import schema_migration
from .dialogs import DiffDialog

REASONS = {'LIVE_EVIDENCE_REQUIRED': '尚无本连接程序的实际协议及权限验证凭证；先进行固定合成验证。',
    'MAJOR_REVIEW_REQUIRED': '连接程序主要版本变化，须维护人员审查新契约并更新适配器。',
    'REQUIRED_FLAG_MISSING': '缺少创作必需参数；修复官方连接程序安装或更新适配器。',
    'RUNTIME_CONTRACT_FAILED': '实际返回不符合已验证契约；已有结果保留，请重新检查，未知请求不自动重发。',
    'KNOWN_ISSUE': '此连接程序存在已记录问题；请使用修复版本。',
    'CLI_MISSING': '未找到官方连接程序；请到“开发者诊断与安装”检查官方组件安装。',
    'CLI_START_FAILED': '连接程序未能正常启动；请检查官方组件安装，修复后重新检查。',
    'CLI_VERSION_INVALID': '连接程序返回了无法识别的版本信息；请检查是否为正确的官方程序。',
    'COMPAT_STORE_INVALID': '本机兼容缓存无法校验；可以点击下方“归档本机兼容缓存并重新检查”，原缓存会保留。',
    'COMPAT_HISTORY_FULL': '兼容记录已满；可以归档本机缓存后重新验证，小说不受影响。'}


class CompatibilityDialog(QDialog):
    def __init__(self, presenter):
        super().__init__(presenter.window)
        self.presenter = presenter
        self.setWindowTitle('兼容性与版本 · 本机自动诊断')
        self.resize(850, 670)
        layout = QVBoxLayout(self)
        self.heading = QLabel('正在读取本机兼容信息…')
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)
        self.check_button = QPushButton('重新检查本机接口（不调用模型）')
        self.probe_button = QPushButton('验证真实协议与权限（最多4次，可能计费）')
        self.migrate_button = QPushButton('预览旧创作记录升级（不改小说正文）')
        self.restore_button = QPushButton('预览恢复迁移前的记录')
        self.repair_button = QPushButton('归档本机兼容缓存并重新检查（不处理小说）')
        for button in (self.check_button, self.probe_button, self.migrate_button, self.restore_button, self.repair_button):
            layout.addWidget(button)
        self.check_button.clicked.connect(lambda: self.refresh(True))
        self.probe_button.clicked.connect(self.probe)
        self.migrate_button.clicked.connect(lambda: presenter._guarded(lambda: self.migrate(False)))
        self.restore_button.clicked.connect(lambda: presenter._guarded(lambda: self.migrate(True)))
        self.repair_button.clicked.connect(lambda: presenter._guarded(self.repair))
        self.refresh()

    def display(self, report):
        label = {'FULL': '完全兼容', 'PARTIAL': '部分兼容', 'INCOMPATIBLE': '不兼容 · 模型操作已暂停'}[report['status']]
        self.heading.setText(label + '。本地阅读、编辑、资料管理与导出继续可用。')
        data = versions(self.presenter.core)
        lines = ['软件版本：' + data['software'], '连接程序版本：' + str(report.get('cli_version') or '不可用'),
            'Antigravity 适配器版本：' + data['adapters']['antigravity_cli'], '接口契约：' + data['transport_contract'],
            '作品数据格式：' + str(data.get('project_schema', '尚未打开作品')),
            '兼容状态：' + label, '', '检查结果与修复方式：']
        lines += [REASONS.get(reason, reason + '：请检查官方连接程序安装、本机文件访问或重新验证。') for reason in report['reasons']]
        lines += ['缺少参数：' + ', '.join(report.get('missing_flags', [])), '', '实际能力证据：']
        labels = {'stream': '流式输入输出与终态', 'structured': '结构化结果', 'agents': '主笔和责编身份',
            'deny_structured': '独立任务权限拒绝', 'deny_workspace': '创作工作区权限拒绝'}
        lines += [labels.get(key, key) + '：' + {'PASS':'已验证','FAIL':'未通过','NOT TESTED':'尚未验证'}[value] for key, value in report.get('checks', {}).items()]
        lines += ['', '可选功能：思考参数 ' + ('可用' if report['features'].get('effort') else '停用（使用宿主默认）'),
            '会话续接：' + ('可用' if report['features'].get('conversation') else '停用；已有对话保留，不自动新建替换'),
            '', '接口检查不等于模型已连接或权限已验证。测试仅使用合成资料，失败不自动重试。缓存不会写入小说工程。']
        self.details.setPlainText('\n'.join(lines))
        self.check_button.setEnabled(True)
        self.probe_button.setEnabled(bool(report.get('binding')) and not report.get('missing_flags') and not any(r in report['reasons'] for r in ('MAJOR_REVIEW_REQUIRED', 'KNOWN_ISSUE')))
        self.migrate_button.setEnabled(self.presenter.core is not None and data.get('project_schema') == 1)
        self.restore_button.setEnabled(self.presenter.core is not None and bool(schema_migration.history(self.presenter.core)))

    def refresh(self, force=False):
        if self.presenter.job is not None:
            return
        self.check_button.setEnabled(False)
        self.probe_button.setEnabled(False)
        self.presenter.start_job(lambda progress: self.presenter.cli.compatibility.inspect(force), self.display, '检查本机兼容性（不调用模型）…')

    def probe(self):
        p = self.presenter
        if p.job is not None:
            return
        answer = QMessageBox.question(self, '批准固定合成测试', '将使用 Gemini 3.8 Flash High，最多4次官方模型请求，可能扣除额度或计费，无法准确预估金额。\n仅创建合成作品，不使用你的小说，不自动重试。\n是否明确批准这次测试？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        parent = p.store.root / 'Compatibility-Probes'
        from ..core import SafeTree
        with SafeTree(p.store.root) as tree:
            tree.mkdir('Compatibility-Probes')
        self.probe_button.setEnabled(False)
        p.start_job(lambda progress: run(p.cli, parent, confirmed=True, notify=progress), self.display, '运行已批准的合成兼容验证…')

    def migrate(self, restore):
        core = self.presenter.core
        if core is None:
            raise CoreError('PROJECT_REQUIRED', '请先打开作品。')
        selected = None
        if restore:
            selected, accepted = QInputDialog.getItem(self, '选择迁移记录', '恢复会检查当前记录是否仍为该次迁移结果：', schema_migration.history(core), 0, False)
            if not accepted:
                return
        value = schema_migration.preview(core, selected)
        dialog = DiffDialog(value, self)
        dialog.setWindowTitle('作者批准 · 创作记录格式变更')
        if dialog.exec() == QDialog.Accepted:
            core.author_commit(value['handle'], True)
            self.refresh()

    def repair(self):
        if self.presenter.job is not None:
            return
        answer = QMessageBox.question(self, '归档兼容缓存', '仅备份并重置本机兼容检查缓存，不处理小说、日志或模型密钥。\n重置后必须重新验证，才会恢复模型功能。是否继续？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        store = self.presenter.cli.compatibility._store()
        with SafeTree(store.root) as tree:
            with tree.lock():
                raw = tree.maybe_read('receipts.bin')
                if raw:
                    tree.mkdir('Archived')
                    name = 'Archived/' + digest(raw) + '.bin'
                    if tree.maybe_read(name) is None:
                        tree.create(name, raw)
                payload = protect(admin.encode({'format': 'novel-compat-receipts-v1', 'records': {}}))
                tree.publish('receipts.bin', payload, digest(raw) if raw else None)
        self.refresh()
