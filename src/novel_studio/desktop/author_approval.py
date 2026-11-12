"""Shared author-confirmed preview coordinator; no imports of either dispatcher."""
from PySide6.QtWidgets import QDialog
from .dialogs import DiffDialog


def approved(presenter, preview):
    if DiffDialog(preview, presenter.window).exec() == QDialog.Accepted:
        result = presenter.core.author_commit(preview["handle"], True)
        presenter.refresh_view()
        presenter.window.status_text.setText("已按您的明确批准保存，并保留来源与历史。")
        if preview['operation'] == 'chapter_revision':
            presenter.refresh_recovery()
        return result
