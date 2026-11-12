"""Small native-Qt standard-button vocabulary; no external translation assets."""
from PySide6.QtCore import QTranslator


class ChineseButtons(QTranslator):
    words = {"OK": "确定", "Cancel": "取消", "Close": "关闭", "Yes": "是", "No": "否", "Apply": "应用",
        "Save": "保存", "Open": "打开", "Discard": "放弃", "Reset": "重置", "Restore Defaults": "恢复默认",
        "Retry": "重试", "Abort": "中止", "Ignore": "忽略", "Save All": "全部保存", "Yes to All": "全部同意", "No to All": "全部拒绝", "Help": "帮助",
        'Undo':'撤销', 'Redo':'重做', 'Cut':'剪切', 'Copy':'复制', 'Paste':'粘贴', 'Delete':'删除', 'Select All':'全选'}

    def isEmpty(self):
        return False

    def translate(self, context, sourceText, disambiguation=None, n=-1):
        if context in {"QPlatformTheme", "QDialogButtonBox", "QMessageBox", 'QWidgetTextControl', 'QTextControl', 'QLineEdit'}:
            return self.words.get(sourceText.replace("&", ""), sourceText)
        return sourceText


def install_chinese_buttons(app):
    if not hasattr(app, "_novel_chinese_buttons"):
        translator = ChineseButtons(app)
        app.installTranslator(translator)
        app._novel_chinese_buttons = translator
