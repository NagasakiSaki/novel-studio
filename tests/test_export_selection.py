"""Visible book/volume/chapter/version checks must be the actual export input."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import json
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QApplication, QMessageBox
from novel_studio.core import READ, WRITE, CoreError
from novel_studio.desktop.export_ui import ExportDialog
from novel_studio.document_titles import numbered_title
from ui2_fixtures import project, complete


class SelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='novel-export-tree-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.core, unused=project(self.base,'分层导出合成作品')
        complete(self.core)
        rows=self.core.workflow.chapters.catalog()
        self.core.author_commit(self.core.workflow.chapters.preview([r['chapter_id'] for r in rows])['handle'],True)
        self.dialog=ExportDialog(self.core)
        self.addCleanup(self.dialog.deleteLater)

    def test_every_visible_version_has_checkbox_and_name_focus_does_not_toggle(self):
        d=self.dialog
        self.assertEqual(len(d.selected()),2)
        for leaf in d.sources.leaves:
            self.assertTrue(leaf.flags() & Qt.ItemIsUserCheckable)
            self.assertEqual(leaf.checkState(0),Qt.Checked)
            self.assertTrue(leaf.text(3).isdigit())
        before=[r['source_id'] for r in d.selected()]
        d.sources.setCurrentRow(1)
        self.assertEqual([r['source_id'] for r in d.selected()],before)
        self.assertTrue(d.source_preview.toPlainText())
        d.refresh_sources()
        self.assertEqual([r['source_id'] for r in d.selected()],before)

    def test_group_check_all_clear_invert_and_partial_states_match_preview(self):
        d=self.dialog
        volume=d.sources.groups[1]
        volume.setCheckState(0,Qt.Unchecked)
        self.assertEqual(d.selected(),[])
        volume.setCheckState(0,Qt.Checked)
        self.assertEqual(len(d.selected()),2)
        d.sources.item(0).setCheckState(Qt.Unchecked)
        self.assertEqual(volume.checkState(0),Qt.PartiallyChecked)
        self.assertEqual(len(d.selected()),1)
        d.sources.choose('invert')
        self.assertEqual([r['source_id'] for r in d.selected()],[d.sources.item(0).data(Qt.UserRole)['source_id']])
        d.format.setCurrentIndex(d.format.findData('txt'))
        d.build_preview()
        self.assertEqual([r['source_id'] for r in d.preview_value['sources']],[r['source_id'] for r in d.selected()])
        d.sources.choose(True)
        self.assertIsNone(d.preview_value)
        self.assertFalse(d.export_button.isEnabled())

    def test_formal_volume_chapter_ranges_do_not_require_manual_length_links(self):
        d=self.dialog
        d.scope.setCurrentIndex(d.scope.findData('volume'))
        self.assertEqual(len(d.selected()),2)
        d.scope.setCurrentIndex(d.scope.findData('chapter'))
        self.assertEqual(len(d.selected()),1)
        self.assertFalse(d.selected()[0]['draft'])
        d.nodes.setCurrentIndex(1)
        self.assertEqual(d.selected()[0]['source_id'],d.sources.item(0).data(Qt.UserRole)['source_id'])
        d.scope.setCurrentIndex(d.scope.findData('book'))
        self.assertEqual(len(d.selected()),2)

    def test_explicit_history_and_mixed_order_are_preserved_in_final_file(self):
        d=self.dialog
        formal=d.service.select()
        history=next(r for r in d.service.catalog() if r['historical'])
        self.assertTrue(history['title'].startswith('第'))
        draft=next(r for r in d.service.catalog() if r['draft'])
        d.select_sources([formal[1]['source_id'],history['source_id'],draft['source_id']])
        self.assertIn(d.selected()[0]['volume'],d.order_list.item(0).text())
        self.assertEqual([r['source_id'] for r in d.selected()],[formal[1]['source_id'],history['source_id'],draft['source_id']])
        d.move_source(-1,history['source_id'])
        self.assertEqual(d.selected()[0]['source_id'],history['source_id'])
        d.format.setCurrentIndex(d.format.findData('txt'))
        before={r['relative']:r['revision'] for r in self.core.documents()}
        d.build_preview()
        self.assertIn('历史版本',d.preview_value['text'])
        self.assertIn('未经批准',d.preview_value['text'])
        value=d.preview_value
        target=self.base/'混合版本.txt'
        result=d.service.export(value['handle'],target,True)
        receipt=json.loads(Path(result['receipt']).read_bytes())
        self.assertEqual([r['source_id'] for r in receipt['sources']],[r['source_id'] for r in value['sources']])
        self.assertEqual(target.read_text(encoding='utf-8'),value['text'])
        self.assertEqual(before,{r['relative']:r['revision'] for r in self.core.documents()})
        d.exported(result)
        self.assertFalse(d.result_card.isHidden())
        self.assertEqual(d.result_path.text(),str(target))
        with patch('novel_studio.desktop.export_ui.QDesktopServices.openUrl',return_value=True) as opening:
            d.open_result(False)
            self.assertEqual(Path(opening.call_args.args[0].toLocalFile()),target)
            d.open_result(True)
            self.assertEqual(Path(opening.call_args.args[0].toLocalFile()),target.parent)

    def test_display_titles_avoid_duplicates_without_changing_original_title(self):
        for title in ['第1章 雨港','第一章 雨港','第001章 雨港']:
            self.assertEqual(numbered_title(1,title),title)
        self.assertEqual(numbered_title(1,'雨港'),'第1章 雨港')
        self.assertEqual(numbered_title(1,'第一卷 归来','卷'),'第一卷 归来')


if __name__=='__main__':unittest.main()
