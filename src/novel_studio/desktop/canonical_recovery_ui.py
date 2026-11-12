"""Human-readable, author-only recovery of chapter directory records."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
                              QPlainTextEdit, QPushButton, QMessageBox, QFileDialog,
                              QTabWidget, QWidget, QComboBox, QAbstractItemView)
from ..core import CoreError, SafeTree, admin

EXPLANATIONS = {
    'INDEX_PENDING': '正式正文已经保存，原作者批准和备份有效；章节目录仍指向旧版本。',
    'REGISTRY_MISSING': '正式正文及批准凭证有效，但恢复登记缺项；确认后先补登记，再补齐章节记录。',
    'INDEX_APPLIED_AUDIT_PENDING': '章节目录已经更新，之前的索引写入已有持久化批准；系统将自动重试补齐审计，不会重复改目录。',
    'INDEX_DONE': '正文和章节目录已核验完成，正在收尾保存恢复记录。',
    'ORPHAN_UNREGISTERED': '批准意图已保存，但尚未登记，没有正文发布许可。只能核查并关闭未发布意图。',
    'PENDING_UNREGISTERED': '恢复登记已写入，但发布许可尚未完成；正文保持原版，不能按已发布版本补记。',
    'INCOMPLETE_UNPUBLISHED': '记录写入未完成，已核实本事务尚未发布；可关闭并保留原始片段。',
    'CURRENT_BEFORE': '当前正文和目录仍为原版本，没有完成的新正文事务；不会自动继续发布。',
    'CANCELLED_CLOSING': '作者已关闭未发布意图；正在保留存档和释放活动槽位。',
    'PUBLISHED_NO_RECEIPT': '正式正文可能已保存，但完成凭证不足；需要人工核查，不能快捷修复。',
    'EVIDENCE_INVALID': '批准、备份或登记未通过完整性校验；保留全部证据，未修改正文。',
    'CONFLICT': '正文或章节上下文已发生其他变化；不能用旧记录覆盖，需要作者核查。',
    'SUPERSEDED': '章节目录已指向其他事务或版本；不能退回这笔旧事务。',
}


def export_diagnosis(core, path):
    target = Path(path)
    if target.suffix.lower() != '.json':
        raise CoreError('RECOVERY_EXPORT', '请选择新的 JSON 文件保存诊断信息。')
    data = {'format': 'novel-studio-recovery-diagnostic-v1', 'project_id': core.project_id,
            'book_title': core.title, 'diagnosis': core.recovery.diagnose(),
            'phase2': core.recovery.extended_diagnose(),
            'scope': 'Local metadata only; no manuscript, prompt, key or automatic upload.'}
    with SafeTree(target.parent) as output:
        output.create(target.name, admin.encode(data))
    return str(target)


class RecoveryDialog(QDialog):
    def __init__(self, presenter):
        super().__init__(presenter.window)
        self.p, self.core = presenter, presenter.core
        self.setWindowTitle('正式修订检查 · 只补齐章节记录')
        self.resize(920, 680)
        layout = QVBoxLayout(self)
        self.book = QLabel('作品：' + self.core.title)
        self.book.setTextFormat(Qt.PlainText)
        self.book.setWordWrap(True)
        layout.addWidget(self.book)
        notice = QLabel('恢复只关联已经批准、已经保存的正文。不会重新生成、覆盖或回滚正文。旧记录、批次和字数显示可在“其他恢复”中核查。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.listing = QListWidget()
        layout.addWidget(self.listing)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        layout.addWidget(self.summary, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.hide()
        layout.addWidget(self.details)
        buttons = QHBoxLayout()
        self.detail_button = QPushButton('查看详情')
        self.detail_button.setCheckable(True)
        self.detail_button.toggled.connect(self.details.setVisible)
        self.confirm = QPushButton('确认补齐章节记录')
        self.confirm.clicked.connect(lambda:self.p._guarded(self.recover))
        self.cancel_intent = QPushButton('关闭未发布意图（保留记录）')
        self.cancel_intent.clicked.connect(lambda:self.p._guarded(lambda:self.recover(close=True)))
        self.export = QPushButton('导出诊断信息')
        self.export.clicked.connect(lambda:self.p._guarded(self.export_information))
        defer = QPushButton('暂不处理')
        defer.clicked.connect(self.reject)
        for button in (self.detail_button, self.confirm, self.export, defer):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        more = QHBoxLayout()
        self.lock_button = QPushButton('检查中断锁')
        self.lock_button.clicked.connect(lambda:self.p._guarded(self.check_locks))
        more.addWidget(self.cancel_intent)
        more.addWidget(self.lock_button)
        self.extended_button = QPushButton('其他恢复：旧记录、批次、字数显示')
        self.extended_button.clicked.connect(lambda:self.p._guarded(self.open_extended))
        more.addWidget(self.extended_button)
        layout.addLayout(more)
        self.listing.currentRowChanged.connect(self.selected)
        self.reload()

    def open_extended(self):
        self.check_project()
        PhaseTwoDialog(self.p).exec()
        self.reload()

    def check_project(self):
        if self.p.core is not self.core:
            raise CoreError('OBJECT_CHANGED', '作品已切换，请重新打开修订检查。')

    def reload(self):
        self.check_project()
        diagnostic = self.core.recovery.diagnose()
        self.lock_button.setEnabled(diagnostic.get('busy', False))
        self.rows = diagnostic['operations']
        self.listing.clear()
        for row in self.rows:
            title = f"第{row['volume']}卷 第{row['number']}章 {row['title']}" if 'volume' in row else '无法确认章节身份的记录'
            self.listing.addItem(title)
        self.listing.setCurrentRow(0)
        if not self.rows:
            self.summary.setPlainText('\n'.join(diagnostic['warnings']) or '没有待处理的正式修订记录。')
            self.confirm.setEnabled(False)
            self.cancel_intent.setEnabled(False)
        elif diagnostic['warnings']:
            self.summary.appendPlainText('\n' + '\n'.join(diagnostic['warnings']))

    def selected(self, index):
        if index < 0 or index >= len(self.rows):
            self.confirm.setEnabled(False)
            self.cancel_intent.setEnabled(False)
            return
        row = self.rows[index]
        text = EXPLANATIONS.get(row['state'], '记录尚不能确认，需要人工核查。')
        if row.get('reason'):
            text += '\n原因：' + row['reason']
        text += '\n\n正文：' + ('已核实获批新版保存' if row.get('body_saved') else '未核实获批新版；保留当前正文')
        text += '\n作者批准：' + ('已核实' if row.get('approval_valid') else '未通过完整核验')
        if row.get('approval_kind') == 'manual_association':
            text += '（只批准本次目录关联；未追认缺失的历史发布批准）'
        text += '\n章节目录：' + ('已更新到该事务' if row.get('index_updated') else '尚未更新到该事务或存在冲突')
        text += '\n\n准备执行：' + ('仅补齐已保存正式正文的章节目录关联。' if row['can_recover'] else '保留现状，核查记录。')
        text += '\n执行后不会修改任何正式正文。'
        self.summary.setPlainText(text)
        self.details.setPlainText('事务：' + row['transaction_id'] + '\n章节标识：' + row.get('chapter_id', '无法确认') + '\n诊断：' + row['state'])
        self.confirm.setEnabled(row['can_recover'])
        self.cancel_intent.setEnabled(row.get('can_close', False))

    def recover(self, close=False):
        self.check_project()
        index = self.listing.currentRow()
        if index < 0:
            return
        row = self.rows[index]
        preview = self.core.recovery.preview(row['transaction_id'], close=close)
        question = preview['diff'] + '\n\n不会生成、覆盖或回滚正文。是否确认？'
        if QMessageBox.question(self, '作者确认', question, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.core.author_commit(preview['handle'], True)
            self.p.refresh_view()
        finally:
            self.p.refresh_recovery()
            self.reload()

    def check_locks(self):
        self.check_project()
        if QMessageBox.question(self, '检查中断锁', '只处理能够证明所属进程已经结束的本作品锁，并保存原记录。不终止进程，不修改正文。是否检查？',
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.core.workflow.store.recover_locks(True)
            self.reload()
            self.p.refresh_recovery()

    def export_information(self):
        self.check_project()
        path, unused = QFileDialog.getSaveFileName(self, '导出本机恢复诊断', '', 'JSON (*.json)')
        if path:
            export_diagnosis(self.core, path)
            self.summary.appendPlainText('\n诊断信息已保存到您选择的新文件；没有上传。')


class PhaseTwoDialog(QDialog):
    """Selection and exact author confirmation; no raw ID/SHA input fields."""
    def __init__(self, presenter):
        super().__init__(presenter.window)
        self.p, self.core, self.offset = presenter, presenter.core, 0
        self.setWindowTitle('其他恢复 · 正文保持不变')
        self.resize(860, 650)
        layout = QVBoxLayout(self)
        title = QLabel('当前作品：' + self.core.title + '\n所有操作仅补记目录或重建字数、显示记录。不会发布、覆盖或回滚正文，也不会调用模型。')
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        legacy, batches, derived = QWidget(), QWidget(), QWidget()
        tabs.addTab(legacy, '旧工程人工关联')
        tabs.addTab(batches, '多批次核查')
        tabs.addTab(derived, '字数与版本显示')
        self.chapter = QComboBox()
        self.derived_chapter = QComboBox()
        left, middle, right = QVBoxLayout(legacy), QVBoxLayout(batches), QVBoxLayout(derived)
        explanation = QLabel('先在工作台打开需要处理的旧工程。本页只关联当前作品；其他工程、来源不明或已有后继事务的记录会拒绝。\n明确选择章节和本作品的完整历史事务；文件名、时间或单独的摘要不能证明归属。')
        explanation.setWordWrap(True)
        left.addWidget(explanation)
        left.addWidget(self.chapter)
        associate = QPushButton('选择本作品旧事务并查看关联预览')
        associate.clicked.connect(lambda:self.p._guarded(self.associate))
        left.addWidget(associate)
        left.addStretch()
        self.batches = QListWidget()
        self.batches.setSelectionMode(QAbstractItemView.ExtendedSelection)
        middle.addWidget(QLabel('选择最多 16 个批次、共 500 章。剩余未发布稿件仍需重新预览批准。'))
        middle.addWidget(self.batches)
        batch_button = QPushButton('查看所选批次并确认补记')
        batch_button.clicked.connect(lambda:self.p._guarded(self.recover_batches))
        middle.addWidget(batch_button)
        right.addWidget(QLabel('可信来源：已完成的正文事务与章节目录。缓存只会清空重建，不作为正文来源。'))
        right.addWidget(self.derived_chapter)
        repair = QPushButton('查看并重建本章字数、版本显示')
        repair.clicked.connect(lambda:self.p._guarded(self.repair_derived))
        right.addWidget(repair)
        right.addStretch()
        page = QHBoxLayout()
        self.previous = QPushButton('前 25 章')
        self.following = QPushButton('后 25 章')
        self.previous.clicked.connect(lambda:self.page(-25))
        self.following.clicked.connect(lambda:self.page(25))
        page.addWidget(self.previous)
        page.addWidget(self.following)
        layout.addLayout(page)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        layout.addWidget(self.summary)
        close = QPushButton('暂不处理 / 关闭')
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self.reload()

    def check_project(self):
        if self.p.core is not self.core:
            raise CoreError('OBJECT_CHANGED', '作品已切换，请重新打开恢复窗口。')

    def page(self, step):
        self.offset = max(0, self.offset + step)
        self.p._guarded(self.reload)

    def reload(self):
        self.check_project()
        state = self.core.workflow.store.read()
        chapters = sorted(state['chapters'].values(), key=lambda c:c['ordinal'])
        self.chapter.clear()
        self.derived_chapter.clear()
        for row in chapters[self.offset:self.offset + 25]:
            label = f"第{row['volume']}卷 第{row['number']}章 {row['title']}"
            self.chapter.addItem(label, row['chapter_id'])
            if row.get('canonical') and row.get('approval_transaction'):
                self.derived_chapter.addItem(label, row['chapter_id'])
        self.previous.setEnabled(self.offset > 0)
        self.batches.clear()
        bids = sorted(state['batches'], key=lambda bid: state['batches'][bid]['status'] == 'COMPLETED')
        self.batch_ids = bids[self.offset:self.offset + 25]
        self.following.setEnabled(self.offset + 25 < max(len(chapters), len(bids)))
        for bid in self.batch_ids:
            row = state['batches'][bid]
            text = '已完成，可重复核验' if row['status'] == 'COMPLETED' else '部分完成，需核查'
            self.batches.addItem(f"{text} · 已记 {len(row.get('completed', []))} 章 · {bid[:8]}")

    def confirm(self, preview):
        self.summary.setPlainText(preview['diff'])
        if QMessageBox.question(self, '作者确认：正文不会改变', preview['diff'], QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        self.check_project()
        result = self.core.author_commit(preview['handle'], True)
        if 'batches' in result:
            pending = sum(r['remaining_need_new_author_preview'] for r in result['batches'].values())
            message = '所选批次全部核验完成。' if result['complete'] else f'仅补记已发布项；另有 {pending} 章未发布，仍需新的作者预览批准。'
        elif result.get('remaining_need_new_author_preview'):
            message = f"仅补记已发布项；另有 {result['remaining_need_new_author_preview']} 章未发布，仍需新的作者预览批准。"
        else:
            message = '所选记录已核验并补齐；正式正文没有改变。'
        self.summary.setPlainText(message)
        self.p.refresh_view()
        self.p.refresh_recovery()
        self.reload()

    def associate(self):
        self.check_project()
        cid = self.chapter.currentData()
        if not cid:
            return
        paths, unused = QFileDialog.getOpenFileNames(self, '选择当前作品的完整旧事务（同批收录可多选）',
            str(self.core.project / 'Backups/Transactions'), '事务完成凭证 (COMMITTED.json)')
        if not paths:
            return
        transactions = []
        for path in paths:
            try:
                relative = Path(path).resolve().relative_to(self.core.project)
            except ValueError:
                raise CoreError('RECOVERY_CONFLICT', '所选凭证不属于当前工程，未读取或关联。')
            if len(relative.parts) != 4 or relative.parts[:2] != ('Backups', 'Transactions') or relative.name != 'COMMITTED.json':
                raise CoreError('RECOVERY_CONFLICT', '请选择当前工程原有的完整事务凭证。')
            transactions.append(relative.parts[2])
        self.confirm(self.core.recovery.legacy_transactions_preview(self.core.project, transactions, cid))

    def recover_batches(self):
        self.check_project()
        ids = [self.batch_ids[self.batches.row(item)] for item in self.batches.selectedItems()]
        self.confirm(self.core.recovery.batches_preview(ids))

    def repair_derived(self):
        self.check_project()
        cid = self.derived_chapter.currentData()
        if cid:
            self.confirm(self.core.recovery.derived_preview(cid))
