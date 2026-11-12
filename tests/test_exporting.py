"""Eight actual formats, selected synthetic versions, hostile resources, no APIs."""
import io
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_studio.core import Core, CoreError, SafeTree, READ, WRITE
from novel_studio.exporting import ExportService, DEFAULTS, FORMATS
from novel_studio.export_formats import cover_image
from novel_studio.material_formats import parse
CONVERTER_AVAILABLE = os.name == "nt" and any(
    (ROOT / relative).is_file() for relative in (
        "tools/pandoc/pandoc.exe", ".build/pandoc-3.13-audit/pandoc-3.13/pandoc.exe"))
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage, QColor
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt developer environment not installed")
class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from novel_studio.desktop.theme import load_local_fonts
        load_local_fonts()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.core = Core.create(self.root, "导出合成小说", ROOT)
        self.cap = self.core.bind("writer", READ | WRITE)
        self.draft = self.core.dispatch(self.cap, "create_draft", {"text": "# 第一章 灯\n独特正文甲：林舟说：“明天见。”\n\n周岚收起**蓝色信封**，向桥边走去。"}, "export-synthetic-first")
        preview = self.core.author_preview(self.draft["document_id"], filename="001_灯.md")
        self.tx = self.core.author_commit(preview["handle"], True)["transaction_id"]
        self.service = ExportService(self.core)
        self.config = {**DEFAULTS, "title": "雾桥合成小说", "author": "合成作者", "subtitle": "测试副标题", "copyright": "测试版权声明", "preface": "测试序言", "header": "合成页眉", "footer": "合成页脚"}

    def assert_roundtrip(self, formats):
        before = {r["relative"]: r["revision"] for r in self.core.documents()}
        rows = self.service.select()
        self.assertEqual(len(rows), 1)
        for extension in formats:
            with self.subTest(format=extension):
                preview = self.service.preview(rows, self.config, extension)
                target = self.root / ("中文作品." + extension)
                result = self.service.export(preview["handle"], target, True)
                data = target.read_bytes()
                parsed = parse(data, "." + extension)
                text = parsed["text"]
                self.assertIn("独特正文甲", text)
                self.assertIn("周岚", text)
                self.assertIn("测试序言", text)
                receipt = json.loads(Path(result["receipt"]).read_bytes())
                self.assertEqual(receipt["sources"][0]["revision"], rows[0]["revision"])
                self.assertFalse(receipt["draft"])
                if extension == "pdf":
                    self.assertTrue(data.startswith(b"%PDF"))
                    from pypdf import PdfReader
                    pdf = PdfReader(io.BytesIO(data))
                    self.assertGreaterEqual(len(pdf.pages), 2)
                    for page in pdf.pages:
                        self.assertIn("合成页眉", page.extract_text())
                if extension in {"epub", "odt", "docx"}:
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        self.assertIsNone(archive.testzip())
        self.assertEqual(before, {r["relative"]: r["revision"] for r in self.core.documents()})

    def test_builtin_formats_chinese_roundtrip_and_sources_unchanged(self):
        self.assert_roundtrip(extension for extension in FORMATS if extension not in {"epub", "rtf"})

    @unittest.skipUnless(CONVERTER_AVAILABLE, "Pinned Windows converter unavailable")
    def test_converter_formats_chinese_roundtrip_and_sources_unchanged(self):
        self.assert_roundtrip(("epub", "rtf"))

    def test_approval_revision_conflict_no_clobber_and_no_source_directory(self):
        rows = self.service.select()
        p = self.service.preview(rows, self.config, "txt")
        with self.assertRaises(CoreError):
            self.service.export(p["handle"], self.root / "x.txt")
        with self.assertRaises(CoreError):
            self.service.export(p["handle"], self.core.project / "x.txt", True)
        existing = self.root / "保留.txt"
        existing.write_bytes(b"KEEP")
        with self.assertRaises(CoreError):
            self.service.export(p["handle"], existing, True)
        self.assertEqual(existing.read_bytes(), b"KEEP")
        with SafeTree(self.core.project) as tree:
            original = tree.read(rows[0]["relative"])
            tree.publish(rows[0]["relative"], original + "\n作者刚改过。".encode(), rows[0]["revision"])
        with self.assertRaises(CoreError):
            self.service.export(p["handle"], self.root / "changed.txt", True)
        self.assertFalse((self.root / "changed.txt").exists())

    def test_history_draft_scope_custom_order_and_planned_volume(self):
        second = self.core.dispatch(self.cap, "update_draft", {"document_id": self.draft["document_id"], "expected_revision": self.draft["revision"], "text": "# 第一章 灯\n新版本独特正文乙。"}, "export-synthetic-second")
        p = self.core.author_preview(second["document_id"], filename="001_灯.md")
        self.core.author_commit(p["handle"], True)
        history = self.service.select("history", ["history_" + self.tx])
        preview = self.service.preview(history, self.config, "txt")
        self.assertIn("独特正文甲", preview["text"])
        self.assertNotIn("新版本独特正文乙", preview["text"])
        draft = self.service.select("draft", [self.draft["document_id"]])
        self.assertIn("未经批准", self.service.preview(draft, self.config, "txt")["text"])
        formal = self.service.select()[0]
        volume = self.core.lengths.add("volume", "卷一")
        chapter = self.core.lengths.add("chapter", "第001章", volume)
        self.core.lengths.link(chapter, formal["document_id"])
        planned = self.service.select("plan", node_id=volume)
        self.assertEqual(planned[0]["volume"], "卷一")
        self.assertFalse(planned[0]["draft"])
        custom = self.service.select("custom", [draft[0]["source_id"], formal["source_id"]])
        self.assertTrue(custom[0]["draft"])

    def synthetic_cover(self):
        image = QImage(240, 360, QImage.Format_RGB32)
        image.fill(QColor("#779999"))
        cover = self.root / "合成封面.png"
        image.save(str(cover))
        return cover

    def assert_cover_exports(self, cover, formats):
        for extension in formats:
            with self.subTest(format=extension):
                p = self.service.preview(self.service.select(), self.config, extension, cover)
                result = self.service.export(p["handle"], self.root / ("cover." + extension), True)
                if extension != "html":
                    with zipfile.ZipFile(result["path"]) as archive:
                        self.assertTrue(any(archive.read(name).startswith(b"\x89PNG") for name in archive.namelist()))
                        if extension == "epub":
                            self.assertTrue(any(b'properties="cover-image"' in archive.read(name) for name in archive.namelist() if name.endswith(".opf")))
                else:
                    self.assertIn(b"data:image/png;base64,", Path(result["path"]).read_bytes())

    @unittest.skipUnless(CONVERTER_AVAILABLE, "Pinned Windows converter unavailable")
    def test_epub_cover_is_embedded_offline(self):
        self.assert_cover_exports(self.synthetic_cover(), ("epub",))

    def test_builtin_covers_and_unsafe_markdown_are_offline(self):
        self.assert_cover_exports(self.synthetic_cover(), ("html", "docx", "odt"))
        malicious = self.core.dispatch(self.cap, "create_draft", {"text": "# 测试\n![替代图片](https://example.invalid/image.png) [只保留文字](https://example.invalid)\n<script>alert('合成')</script>"}, "export-synthetic-hostile")
        p = self.service.preview(self.service.select("draft", [malicious["document_id"]]), self.config, "html")
        self.assertNotIn("file:///", p["html"])
        self.assertNotIn("https://example", p["html"])
        self.assertNotIn("<script>", p["html"])
        self.assertIn("&lt;script&gt;", p["html"])
        self.assertIn("替代图片", p["html"])

    def test_format_failure_keeps_sources_and_does_not_create_output(self):
        rows = self.service.select()
        p = self.service.preview(rows, self.config, "txt")
        with patch("novel_studio.export_formats.render", side_effect=CoreError("EXPORT_FAILED", "合成转换失败")):
            with self.assertRaises(CoreError):
                self.service.export(p["handle"], self.root / "failure.txt", True)
        self.assertEqual(rows, self.service.select())
        self.assertFalse((self.root / "failure.txt").exists())

    @unittest.skipUnless(os.name == "nt", "Pinned converter is Windows-only")
    def test_unverified_converter_is_never_executed(self):
        p = self.service.preview(self.service.select(), self.config, "epub")
        with patch("novel_studio.export_formats.PANDOC_SHA", "0" * 64), patch("novel_studio.export_formats.subprocess.run") as child:
            with self.assertRaises(CoreError):
                self.service.export(p["handle"], self.root / "no-converter.epub", True)
            child.assert_not_called()


if __name__ == "__main__":
    unittest.main()
