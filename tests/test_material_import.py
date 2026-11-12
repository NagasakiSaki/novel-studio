"""Synthetic source preservation, actual parsers and context/version boundaries."""
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_studio.core import Core, CoreError, SafeTree, digest, READ, WRITE
from novel_studio.materials import parse_source, TYPES, BASE
from novel_studio.material_formats import heading_index
from novel_studio.material_search import search
from material_fixture_factory import samples, preset, external_decoder_pdf

PARSERS = all(importlib.util.find_spec(n) for n in ("markdown_it", "docx", "lxml", "striprtf", "pypdf"))


@unittest.skipUnless(PARSERS, "Install the audited import parser environment")
class MaterialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-material-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.files = samples(self.base / "input")
        self.core = Core.create(self.base, "合成资料测试工程", ROOT)

    def approved(self, path=None, kind="global_preset", update_id=None, mode="retrieval"):
        item = self.core.materials.prepare(path or self.files[".md"], kind, update_id)
        ids = [s["id"] for s in item.parsed["sections"] if s["title"] in {"全局写作规则", "文风要求", "特殊剧情约束"}]
        preview = self.core.materials.preview(item, ids, mode, True)
        result = self.core.author_commit(preview["handle"], True)
        return self.core.materials.get(preview["material_id"]), result

    def test_all_claimed_formats_use_real_samples(self):
        expected = {".md": "林舟", ".txt": "中文原文", ".json": "全局规则", ".docx": "表格右侧：周岚", ".pdf": "PDF第二页", ".epub": "EPUB第二章", ".rtf": "RTF人物", ".html": "HTML设定", ".odt": "ODT时间线"}
        for suffix, target in expected.items():
            with self.subTest(format=suffix):
                before = self.files[suffix].read_bytes()
                raw, parsed = parse_source(self.files[suffix])
                self.assertIn(target, parsed["text"])
                self.assertEqual(raw, before)
                self.assertEqual(self.files[suffix].read_bytes(), before)
                self.assertTrue(parsed["sections"])
        _, pdf = parse_source(self.files[".pdf"])
        self.assertLess(pdf["text"].index("PDF第一页"), pdf["text"].index("PDF第二页"))
        _, epub = parse_source(self.files[".epub"])
        self.assertLess(epub["text"].index("EPUB第一章"), epub["text"].index("EPUB第二章"))
        _, html = parse_source(self.files[".html"])
        self.assertNotIn("CANARY_SCRIPT", html["text"])
        self.assertIn("铜钥匙", html["text"])
        self.assertTrue(html["warnings"])

    def test_complete_bundle_original_toc_search_and_both_roles(self):
        record, receipt = self.approved()
        self.assertEqual(record["text"], preset())
        self.assertEqual(len(self.core.materials.records()), 1)
        with SafeTree(self.core.project) as tree:
            self.assertEqual(tree.read(record["asset_path"]), self.files[".md"].read_bytes())
        joined = "".join(record["text"][s["start"]:s["end"]] for s in record["sections"])
        self.assertEqual(joined, preset())
        self.assertTrue(search(record, "林舟", 5))
        self.core.snapshot()
        draft = self.core.dispatch(self.core.bind("writer", READ | WRITE), "create_draft", {"text": "合成稿：林舟走向钟楼。"}, uuid.uuid4().hex)
        self.core.handoff(draft["document_id"])
        writer, wr = self.core.materials.context("writer", "林舟与周岚")
        editor, er = self.core.materials.context("editor", "林舟与周岚")
        self.assertEqual(writer, editor)
        self.assertEqual(wr, er)
        self.assertIn("不得提前泄露", writer)
        self.assertIn("林舟是钟楼修理工", writer)
        self.assertIn("not_proof", wr[0]["delivery"])

    def test_large_background_not_repeated_and_always_rules_not_lost(self):
        source = self.base / "大型预设.md"
        source.write_bytes(preset(True).encode("utf-8"))
        record, _ = self.approved(source)
        self.assertGreater(len(record["text"].encode()), 500_000)
        self.core.snapshot()
        provided, receipt = self.core.materials.context("writer", "林舟与周岚，修理钟楼")
        self.assertLess(len(provided.encode()), 40_000)
        self.assertIn("不得提前泄露", provided)
        self.assertIn("保留铜钥匙伏笔", provided)
        self.assertNotIn("档案0899", provided)
        self.assertEqual(self.core.materials.get(record["material_id"])["text"], preset(True))
        _, manifest = self.core.validated_context("writer")
        with SafeTree(self.core.project) as tree:
            data = json.loads(tree.read(manifest))
            copied = b"".join(tree.read(str(Path(manifest).parent / n)) for n in data["files"])
            self.assertLess(len(copied), 16_000, "Role snapshot must not repeat a huge heading tree")
        self.assertNotIn("该处记录雾港第899号街区".encode(), copied)

    def test_update_old_editor_version_and_restore(self):
        first, _ = self.approved()
        self.core.snapshot()
        draft = self.core.dispatch(self.core.bind("writer", READ | WRITE), "create_draft", {"text": "草稿"}, uuid.uuid4().hex)
        self.core.handoff(draft["document_id"])
        old_bytes = self.files[".md"].read_bytes()
        self.files[".md"].write_bytes(old_bytes + "\r\n## 新版追加\r\n林舟新增了一件工具。".encode())
        second, receipt = self.approved(update_id=first["material_id"])
        self.assertEqual(second["version"], 2)
        self.core.snapshot()
        self.assertEqual(self.core.materials.context("writer", "林舟")[1][0]["version"], 2)
        self.assertEqual(self.core.materials.context("editor", "林舟")[1][0]["version"], 1)
        plan = self.core.author_preview(transaction=receipt["transaction_id"])
        self.assertIn("资料版本恢复", plan["target"])
        self.core.author_commit(plan["handle"], True)
        restored = self.core.materials.get(first["material_id"])
        self.assertEqual(restored["version"], 1)
        self.assertEqual(restored["text"], first["text"])
        with SafeTree(self.core.project) as tree:
            self.assertEqual(tree.read(first["asset_path"]), old_bytes)

    def test_preview_cancel_duplicate_and_source_change(self):
        item = self.core.materials.prepare(self.files[".md"], "global_preset")
        self.assertEqual(self.core.materials.records(), [])
        self.assertEqual(self.core.history(), [])
        with self.assertRaises(CoreError):
            self.core.materials.preview(item, [], "retrieval", False)
        plan = self.core.materials.preview(item, [], "retrieval", True)
        self.files[".md"].write_bytes(b"changed")
        with self.assertRaises(CoreError):
            self.core.author_commit(plan["handle"], True)
        self.assertEqual(self.core.materials.records(), [])
        self.files[".md"].write_bytes(preset().encode())
        self.approved()
        item = self.core.materials.prepare(self.files[".md"], "global_preset")
        self.assertEqual(len(self.core.materials.duplicates(item)), 1)
        with self.assertRaises(CoreError) as caught:
            self.core.materials.preview(item, [], "retrieval", True)
        self.assertEqual(caught.exception.code, "DUPLICATE_MATERIAL")

    def test_scope_budget_never_silently_drops_rules(self):
        path = self.base / "巨大规则.md"
        path.write_text("# 总规则\n" + "始终尊重作者裁决与知情边界。" * 4000, encoding="utf-8")
        item = self.core.materials.prepare(path, "global_preset")
        plan = self.core.materials.preview(item, ["s0000"], "retrieval", True)
        self.core.author_commit(plan["handle"], True)
        self.core.snapshot()
        context, receipt = self.core.materials.context("writer", "林舟")
        self.assertIn(item.parsed['text'], context)
        self.assertGreater(len(context.encode('utf-8')), 50_000)
        self.assertEqual(receipt[0]['sections'][0]['section_id'], 's0000')
        self.assertEqual(receipt[0]['sections'][0]['mode'], 'always')

    def test_bad_empty_mismatched_and_scanned_inputs(self):
        from pypdf import PdfWriter
        cases = [("empty.txt", b""), ("fake.pdf", b"not a PDF"), ("broken.docx", b"PKbroken"), ("broken.rtf", b"{\\rtf1 unclosed"), ("broken.json", b"{wrong}")]
        writer = PdfWriter()
        writer.add_blank_page(300, 400)
        out = io.BytesIO()
        writer.write(out)
        cases.append(("scanned.pdf", out.getvalue()))
        for name, content in cases:
            path = self.base / name
            path.write_bytes(content)
            with self.subTest(name=name), self.assertRaises(CoreError):
                parse_source(path)
        self.assertEqual(self.core.materials.records(), [])

    def test_archive_traversal_and_entities_rejected(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr("../outside.txt", "DO_NOT_CREATE")
            z.writestr("content.xml", "<xml/>")
        path = self.base / "escape.odt"
        path.write_bytes(data.getvalue())
        with self.assertRaises(CoreError):
            parse_source(path)
        self.assertFalse((self.base.parent / "outside.txt").exists())
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr("content.xml", '<!DOCTYPE x [<!ENTITY private SYSTEM "file:///must-not-read">]><x>&private;</x>')
        path.write_bytes(data.getvalue())
        with self.assertRaisesRegex(CoreError, "实体"):
            parse_source(path)

    def test_heading_maps_skip_fences_and_preserve_setext(self):
        text = "导语\r\n\r\n大标题\r\n===\r\n```md\r\n# 不是标题\r\n```\r\n## 子节\r\n正文……——\r\n"
        sections = heading_index(text)
        self.assertEqual([s["title"] for s in sections], ["文件导语", "大标题", "子节"])
        self.assertEqual("".join(text[s["start"]:s["end"]] for s in sections), text)

    def test_pdf_never_launches_optional_external_decoder(self):
        from unittest.mock import patch
        from pypdf import apply_configuration, get_configuration
        from novel_studio.material_formats import parse, ParseError
        payload = external_decoder_pdf()
        with apply_configuration(jbig2dec_binary="SYNTHETIC_CANARY_NOT_EXECUTABLE"):
            with patch("pypdf.filters.subprocess.run", side_effect=AssertionError("MUST NOT EXECUTE")) as launch:
                with self.assertRaises(ParseError) as caught:
                    parse(payload, ".pdf")
                self.assertEqual(caught.exception.code, "PDF_DECODER_DISABLED")
                launch.assert_not_called()
            self.assertEqual(get_configuration().jbig2dec_binary, "SYNTHETIC_CANARY_NOT_EXECUTABLE")
        source = self.base / "external-decoder.pdf"
        source.write_bytes(payload)
        with self.assertRaises(CoreError) as caught:
            parse_source(source)
        self.assertEqual(caught.exception.code, "PDF_DECODER_DISABLED")


class DependencyNoticeTests(unittest.TestCase):
    def test_pinned_parser_closure_and_original_notices(self):
        lock = json.loads((ROOT / "docs/versions/1.2.4/testing/phase1_2_4_1/parser-dependencies.json").read_bytes())
        requirements = (ROOT / "requirements-import.txt").read_text(encoding="utf-8")
        self.assertEqual(len(lock["packages"]), 7)
        for package in lock["packages"]:
            self.assertIn(package["name"] + "==" + package["version"], requirements)
            self.assertIn("sha256:" + package["sha256"], requirements)
            for notice in package["notices"]:
                self.assertEqual(digest((ROOT / notice["file"]).read_bytes()), notice["sha256"])
        extra = json.loads((ROOT / "docs/versions/1.2.4/testing/phase1_2_4_1/additional-license-source.json").read_bytes())["lxml_elementtree"]
        raw = (ROOT / extra["local"]).read_bytes()
        self.assertEqual(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(), extra["blob_sha"])


if __name__ == "__main__":
    unittest.main()
