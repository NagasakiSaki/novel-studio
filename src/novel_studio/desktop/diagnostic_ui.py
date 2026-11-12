"""Observe dialog lifecycle, Qt/Python errors; never capture dialog text."""
import sys
import time
from PySide6.QtCore import QObject, QEvent, qInstallMessageHandler
from PySide6.QtWidgets import QDialog, QMessageBox, QFileDialog, QApplication
from .dialogs import InformationDialog


class DiagnosticObserver(QObject):
    def __init__(self, app, log):
        super().__init__(app)
        self.log = log
        app.installEventFilter(self)
        self.previous_hook = sys.excepthook
        sys.excepthook = self.exception
        self.previous_qt = qInstallMessageHandler(self.qt_message)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QDialog):
            identity = self.log.last_error_id
            linked = identity if identity and isinstance(obj, QMessageBox) and identity in obj.text() else None
            self.log.dialog(type(obj).__name__, int(obj.icon().value) if isinstance(obj,QMessageBox) else None, linked)
            if not obj.property('diagnostic_observed'):
                obj.setProperty('diagnostic_observed', True)
                cls = type(obj).__name__
                obj.finished.connect(lambda value, name=cls: self.log.event('window_done', dialog_class=name, result=value))
        return False

    def exception(self, kind, error, tb):
        identity = self.log.error(error, 'unhandled')
        QMessageBox.warning(QApplication.activeWindow(), '发生未预期错误', '操作未完成，已保留诊断编号：' + identity + '\n请到“设置 → 诊断日志”查看并导出。请先检查现有版本再决定是否重试。')

    def qt_message(self, kind, context, message):
        self.log.event('qt_message', qt_type=int(kind.value))

    def close(self):
        self.parent().removeEventFilter(self)
        sys.excepthook = self.previous_hook
        qInstallMessageHandler(self.previous_qt)


def show_logs(presenter):
    log = presenter.diagnostic_log
    def summary():
        report = log.report()
        names = {'process_start':'程序启动','process_exit':'程序正常结束','ui_action':'界面操作','ui_error':'操作报错','job_start':'任务开始','job_completed':'任务完成','job_failed':'任务失败','model_start':'模型任务开始','model_finish':'模型已返回','host_event':'官方连接进展','host_error':'模型或连接报错','workflow_step':'创作流程','window_show':'打开窗口或弹窗','window_done':'关闭窗口','qt_message':'界面组件诊断','unhandled':'未捕获异常（旧版也用于错误位置）','error_trace':'错误代码位置','log_enabled':'日志设置'}
        lines = ['日志状态：' + ('正在记录' if report['enabled'] else '已暂停'), '本机保存：' + ('正常' if report['persisted'] else '暂不可写，当前会话记录仍可导出'),
                 '不会记录小说正文、提示词或密钥，不会自动上传。出错后可直接点击下方“导出诊断日志”。', '最近记录（最多80条）：']
        for row in report['events'][-80:]:
            stamp = time.strftime('%m-%d %H:%M:%S', time.localtime(row['time']))
            detail = ' · '.join(str(row[k]) for k in ('action','task','status','error_code','error_id') if k in row)
            if 'context_bytes' in row:
                detail += f" · 上下文 {row['context_bytes'] / 1024:.1f} KB / 本机上限 {row.get('limit_bytes', 0) / 1024:.0f} KB"
            if 'host_result_status' in row:
                detail += ' · 宿主终态 ' + row['host_result_status'] + ' · 类别线索 ' + row.get('host_failure','UNKNOWN')
            if 'cli_version' in row:
                detail += ' · 连接程序版本 ' + row['cli_version']
            if 'cli_compatibility' in row:
                detail += ' · ' + {'SUPPORTED':'版本受支持','UNSUPPORTED':'版本待兼容','INVALID':'版本无法识别','DETECTED':'已识别版本（能力另行检查）','FULL':'完全兼容','PARTIAL':'部分兼容','INCOMPATIBLE':'模型操作暂停'}[row['cli_compatibility']]
            lines.append(stamp + '  ' + names.get(row['event'], '诊断事件') + (' · ' + detail if detail else ''))
        if report['unclosed_sessions']:
            lines += ['', '发现之前会话缺少正常退出标记，可结合最近操作排查；关机或强制关闭也可能导致这一标记。']
        return '\n'.join(lines)
    dialog = InformationDialog('诊断日志 · 本机保存，不自动上传', summary(), presenter.window)
    export = dialog.add_action('导出诊断日志')
    toggle = dialog.add_action('暂停记录日志' if log.enabled else '恢复记录日志')
    def save():
        path, _ = QFileDialog.getSaveFileName(dialog, '保存诊断日志（同名文件自动另存）', 'Novel-Studio-诊断日志.json', 'JSON (*.json)', options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            target = log.export(path)
            presenter.window.status_text.setText('诊断日志已保存：' + str(target) + '（没有上传）')
    export.clicked.connect(lambda: presenter._guarded(save))
    def change():
        log.set_enabled(not log.enabled)
        toggle.setText('暂停记录日志' if log.enabled else '恢复记录日志')
        dialog.text.setPlainText(summary())
    toggle.clicked.connect(lambda: presenter._guarded(change))
    dialog.exec()
