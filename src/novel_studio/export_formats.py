"""Offline author exporters: existing Markdown/Qt/docx and pinned Pandoc CLI.

No raw user HTML, resource URLs, filters, Lua, templates or PDF engines are run.
"""
from __future__ import annotations

import base64
from contextlib import ExitStack
import hashlib
import html
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile
from .core import CoreError, ROOT, SafeTree, digest
from .text_metrics import upstream
from .document_titles import same_heading

PANDOC_SHA = "7ab6244548ba15494fab956230050516fe8f57c36ec2dfb50133719ad3b5a791"
CONVERTER_FORMATS = frozenset({"epub", "rtf"})


def cover_image(path):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImageReader
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise CoreError("EXPORT_COVER", "封面只接受本地 PNG/JPEG 图片。")
    with SafeTree(path.parent, limit=8 * 1024 * 1024) as tree:
        raw = tree.read(path.name)
    buffer = QBuffer()
    buffer.setData(raw)
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > 16_000_000:
        raise CoreError("EXPORT_COVER", "封面为空、损坏或像素过多；请使用不超过一千六百万像素的图片。")
    image = reader.read()
    del reader
    buffer.close()
    if image.isNull():
        raise CoreError("EXPORT_COVER", "封面图片无法读取。")
    from PySide6.QtCore import Qt
    if max(image.width(), image.height()) > 2000:
        image = image.scaled(2000, 2000, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    out = QBuffer()
    out.open(QIODevice.WriteOnly)
    if not image.save(out, "PNG"):
        raise CoreError("EXPORT_COVER", "封面处理失败。")
    return {"png": bytes(out.data()), "original_sha256": digest(raw), "width": image.width(), "height": image.height()}


def warnings(format, cover):
    result = ["正文中的图片、外链和原始 HTML 不会执行或联网；图片以替代文字保留，链接只保留可见文字。复杂表格按普通文本处理。"]
    if format in {"txt", "md"}:
        result.append("此格式没有字体、页眉页脚和固定页码；封面不附带，缩进/行距依赖阅读软件。")
    elif format in {"html", "epub"}:
        result.append("流式阅读不使用固定页码；页眉/页脚作为开头/结尾文字，章节分页取决于阅读器。")
    elif format in {"odt", "rtf"}:
        result.append("当前该格式保留正文/标题/目录；页眉页脚为开头/结尾文字，不承诺动态页码、字体/段落与 Word/PDF 一致。")
    result.append("来源记录会另存到同名 .sources.json；草稿在封面和对应章节显著标记。")
    return result


def markdown_blocks(text):
    from markdown_it import MarkdownIt
    parser = MarkdownIt("commonmark", {"html": False})
    blocks, heading = [], 0
    for token in parser.parse(upstream().visible_body(text)):
        if token.type == "heading_open":
            heading = min(int(token.tag[1:]) + 1, 6)
        elif token.type == "heading_close":
            heading = 0
        elif token.type == "inline":
            spans, flags = [], {"bold": False, "italic": False}
            for child in token.children or []:
                if child.type in {"strong_open", "strong_close"}:
                    flags["bold"] = child.type.endswith("open")
                elif child.type in {"em_open", "em_close"}:
                    flags["italic"] = child.type.endswith("open")
                elif child.type in {"text", "code_inline", "image", "softbreak", "hardbreak"}:
                    content = "\n" if child.type.endswith("break") else child.content
                    spans.append({"text": content, **flags})
            blocks.append({"kind": "heading" if heading else "paragraph", "level": heading, "spans": spans})
        elif token.type in {"fence", "code_block"}:
            blocks.append({"kind": "paragraph", "level": 0, "spans": [{"text": token.content, "bold": False, "italic": False}]})
        elif token.type == "hr":
            blocks.append({"kind": "paragraph", "level": 0, "spans": [{"text": "——", "bold": False, "italic": False}]})
    return blocks


def block(kind, text, level=0, **extra):
    return {"kind": kind, "level": level, "spans": [{"text": text, "bold": False, "italic": False}], **extra}


def css(config):
    return ("body { font-family:'" + config["font"] + "'; font-size:" + str(config["font_size"]) + "pt; line-height:" + str(config["line_spacing"]) + "; color:#202020; background:#fff; } "
        "p { margin:0 0 " + str(config["paragraph_spacing"]) + "pt; text-indent:" + str(config["first_indent"]) + "em; } "
        "h1,h2,h3 { text-indent:0; margin:18pt 0 12pt; } .chapter {" + ("page-break-before:always;" if config["chapter_break"] else "") + "} "
        ".cover { text-align:center; page-break-after:always; } .draft { color:#9b302c; font-weight:bold; } img { max-width:100%; } .toc p { text-indent:0; }")


def assemble(sources, config, cover=None):
    draft = any(r["draft"] for r in sources)
    blocks = [block("title", config["title"] + ("【含未批准草稿】" if draft else ""), 1)]
    if config["subtitle"]:
        blocks.append(block("paragraph", config["subtitle"]))
    if config["author"]:
        blocks.append(block("paragraph", "作者：" + config["author"]))
    if cover:
        blocks.append(block("image", "封面"))
    for title, key in (("版权声明", "copyright"), ("序言", "preface")):
        if config[key]:
            blocks.append(block("heading", title, 1))
            blocks.extend(block("paragraph", line) for line in config[key].splitlines() if line.strip())
    if config["header"]:
        blocks.append(block("header", config["header"]))
    if config["toc"]:
        blocks.append(block("heading", "目录", 1))
        blocks.extend(block("paragraph", (r.get("volume", "") + " · " if config["volumes"] and r.get("volume") else "") + r["title"]) for r in sources)
    previous_volume = None
    for row in sources:
        volume = row.get("volume")
        if config["volumes"] and volume and volume != previous_volume:
            blocks.append(block("volume", volume, 1))
            previous_volume = volume
        blocks.append(block("chapter", row["title"] + ("【草稿，未经批准】" if row["draft"] else "【历史版本】" if row.get('historical') else ""), 2))
        lines = row['text'].splitlines(keepends=True)
        first = next((i for i, line in enumerate(lines) if line.strip()), None)
        if first is not None and lines[first].startswith('#') and same_heading(lines[first], row['title']):
            lines.pop(first)
        blocks.extend(markdown_blocks(''.join(lines)))
    if config["footer"]:
        blocks.append(block("footer", config["footer"]))
    rendered, text, markdown = [], [], []
    for item in blocks:
        kind = item["kind"]
        if kind == "image":
            rendered.append('<p style="text-indent:0;text-align:center"><img width="360" src="data:image/png;base64,' + base64.b64encode(cover["png"]).decode() + '"></p>')
            continue
        content = "".join(r["text"] for r in item["spans"])
        text.append(content)
        markup = ""
        for span in item["spans"]:
            part = html.escape(span["text"]).replace("\n", "<br>")
            if span["bold"]:
                part = "<strong>" + part + "</strong>"
            if span["italic"]:
                part = "<em>" + part + "</em>"
            markup += part
        level = item["level"]
        tag = "h" + str(level) if level else "p"
        rendered.append("<" + tag + (' class="chapter"' if kind in {"chapter", "volume"} else ' class="export-running"' if kind in {"header", "footer"} else "") + ">" + markup + "</" + tag + ">")
        # Escape literal Markdown metacharacters: preserve text, never active
        # image/file/link references in re-exported Markdown.
        escaped = "".join("\\" + character if character in "\\`*_{}[]<>!" else character for character in content)
        markdown.append(("#" * level + " " if level else "") + escaped)
    body = "\n".join(rendered)
    page = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; img-src data:; style-src &#39;unsafe-inline&#39;"><title>' + html.escape(config["title"]) + "</title><style>" + css(config) + "</style></head><body>" + body + "</body></html>"
    return {"blocks": blocks, "html": page, "body_html": body, "text": "\n\n".join(text) + "\n", "markdown": "\n\n".join(markdown) + "\n"}


def qt_document(bundle, config, paged=False):
    from PySide6.QtGui import QFont, QFontDatabase, QTextBlockFormat, QTextCursor, QTextDocument
    import re
    if not QFontDatabase.families():
        raise CoreError("EXPORT_FONT_MISSING", "没有可用的本机字体，已停止生成；请在正常 Windows 窗口中重试。")
    document = QTextDocument()
    font = QFont(config["font"])
    font.setPointSizeF(config["font_size"])
    document.setDefaultFont(font)
    page = bundle["html"]
    if paged:
        page = re.sub(r'<p class="export-running">.*?</p>', "", page, flags=re.DOTALL)
    document.setHtml(page)
    document.setDocumentMargin(0)
    current = document.begin()
    while current.isValid():
        fmt = current.blockFormat()
        if not fmt.headingLevel():
            fmt.setLineHeight(config["line_spacing"] * 100, QTextBlockFormat.ProportionalHeight.value)
            fmt.setTextIndent(config["font_size"] * 96 / 72 * config["first_indent"] if current.text().strip() and "\ufffc" not in current.text() else 0)
            fmt.setBottomMargin(config["paragraph_spacing"] * 96 / 72)
        cursor = QTextCursor(current)
        cursor.setBlockFormat(fmt)
        current = current.next()
    return document


def pdf(bundle, config):
    from PySide6.QtCore import QBuffer, QIODevice, QMarginsF, QRectF, QSizeF
    from PySide6.QtGui import QColor, QFont, QPageSize, QPainter, QPdfWriter
    output = QBuffer()
    output.open(QIODevice.WriteOnly)
    writer = QPdfWriter(output)
    writer.setResolution(96)
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    writer.setTitle(config["title"])
    writer.setCreator("Novel Studio")
    document = qt_document(bundle, config, paged=True)
    margin = 76
    width, height = writer.width() - 2 * margin, writer.height() - 2 * margin
    document.setPageSize(QSizeF(width, height))
    count = document.pageCount()
    if count > 2500:
        raise CoreError("EXPORT_LIMIT", "PDF 超过单次 2500 页限制，请按分卷导出。")
    painter = QPainter(writer)
    if not painter.isActive():
        raise CoreError("EXPORT_FAILED", "无法创建 PDF 排版。")
    try:
        for page in range(count):
            if page and not writer.newPage():
                raise CoreError("EXPORT_FAILED", "PDF 新页创建失败。")
            painter.setClipping(False)
            painter.resetTransform()
            painter.setPen(QColor("#202020"))
            painter.setFont(QFont(config["font"], 9))
            painter.drawText(margin, 40, config["header"])
            footer = config["footer"] + ("  " + str(page + 1) + " / " + str(count) if config["page_numbers"] else "")
            painter.drawText(margin, writer.height() - 36, footer)
            painter.save()
            painter.setClipRect(QRectF(margin, margin, width, height))
            painter.translate(margin, margin - page * height)
            document.drawContents(painter, QRectF(0, page * height, width, height))
            painter.restore()
    finally:
        painter.end()
    # QPdfWriter borrows the QIODevice. Finalize it while that buffer is alive;
    # Python frame cleanup order is not a C++ device lifetime guarantee.
    del painter
    del writer
    data = bytes(output.data())
    output.close()
    return data


def docx(bundle, config, cover):
    from docx import Document
    from docx.shared import Inches, Pt
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    document = Document()
    document.core_properties.title = config["title"]
    document.core_properties.author = config["author"]
    normal = document.styles["Normal"]
    normal.font.name = config["font"]
    normal.font.size = Pt(config["font_size"])
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), config["font"])
    fmt = normal.paragraph_format
    fmt.line_spacing = config["line_spacing"]
    fmt.space_after = Pt(config["paragraph_spacing"])
    fmt.first_line_indent = Pt(config["font_size"] * config["first_indent"])
    for name in ("Title", "Heading 1", "Heading 2", "Heading 3"):
        style = document.styles[name]
        style.font.name = config["font"]
        style._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), config["font"])
    for item in bundle["blocks"]:
        kind = item["kind"]
        if kind in {"header", "footer"}:
            continue
        if kind == "image":
            document.add_picture(io.BytesIO(cover["png"]), width=Inches(4.5))
            continue
        paragraph = document.add_paragraph(style="Title" if kind == "title" else "Heading " + str(min(item["level"], 3)) if item["level"] else "Normal")
        if item["level"]:
            paragraph.paragraph_format.first_line_indent = Pt(0)
        if kind in {"chapter", "volume"}:
            paragraph.paragraph_format.page_break_before = config["chapter_break"]
        for span in item["spans"]:
            run = paragraph.add_run(span["text"])
            run.bold, run.italic = span["bold"], span["italic"]
    section = document.sections[0]
    section.header.paragraphs[0].text = config["header"]
    footer = section.footer.paragraphs[0]
    footer.text = config["footer"]
    if config["page_numbers"]:
        field = OxmlElement("w:fldSimple")
        field.set(qn("w:instr"), "PAGE")
        footer._p.append(field)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def odt(bundle, config):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QTextDocumentWriter
    output = QBuffer()
    output.open(QIODevice.WriteOnly)
    if not QTextDocumentWriter(output, b"ODF").write(qt_document(bundle, config)):
        raise CoreError("EXPORT_FAILED", "ODT 文档生成失败。")
    return bytes(output.data())


def converter_path():
    if os.name == "nt":
        for name in ("tools/pandoc/pandoc.exe", ".build/pandoc-3.13-audit/pandoc-3.13/pandoc.exe"):
            candidate = ROOT / name
            if candidate.is_file():
                return candidate
    return None


def pandoc(bundle, config, format):
    executable = converter_path()
    if executable is None:
        raise CoreError("EXPORT_CONVERTER_MISSING", "当前版本未提供 EPUB 和 RTF 导出。请选择其他格式。")
    # Pin opened directories and, on Windows, deny write/delete sharing on the
    # verified executable until the child exits. No user executable path or PATH.
    import secure_read
    with ExitStack() as stack:
        stack.enter_context(SafeTree(executable.parent))
        _, info = secure_read._win_open(executable, False, stack)
        if info.links != 1:
            raise CoreError("EXPORT_CONVERTER_CHANGED", "转换组件不能是硬链接。")
        with executable.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != PANDOC_SHA:
                raise CoreError("EXPORT_CONVERTER_CHANGED", "转换组件校验失败，未执行。请重新获取已验证安装包。")
        # No includes, URL targets, raw HTML or external images enter this
        # fixed JSON AST; the sole optional image is author-approved PNG data.
        blocks = []
        for item in bundle["blocks"]:
            if item["kind"] == "image":
                # Read only our own assembled base64 image; no general parser.
                start = bundle["body_html"].index("data:image/png;base64,")
                uri = bundle["body_html"][start:].split('"', 1)[0]
                inline = {"t": "Image", "c": [["", [], []], [{"t": "Str", "c": "封面"}], [uri, "封面"]]}
                blocks.append({"t": "Para", "c": [inline]})
                continue
            inline = []
            for span in item["spans"]:
                parts = []
                for index, line in enumerate(span["text"].split("\n")):
                    if index:
                        parts.append({"t": "LineBreak"})
                    parts.append({"t": "Str", "c": line})
                if span["bold"]:
                    parts = [{"t": "Strong", "c": parts}]
                if span["italic"]:
                    parts = [{"t": "Emph", "c": parts}]
                inline.extend(parts)
            blocks.append({"t": "Header", "c": [item["level"], ["", [], []], inline]} if item["level"] else {"t": "Para", "c": inline})
        metadata = {key: {"t": "MetaString", "c": config[key]} for key in ("title", "author", "subtitle") if config[key]}
        metadata["fontsize"] = {"t": "MetaString", "c": str(config["font_size"]) + "pt"}
        source = json.dumps({"pandoc-api-version": [1, 23, 1, 2], "meta": metadata, "blocks": blocks}, ensure_ascii=False).encode()
        environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
        argv = [str(executable), "--sandbox", "--from=json", "--to=" + ("epub3" if format == "epub" else "rtf"), "--standalone", "--output=-",
                "--data-dir=" + str(executable.parent / "empty-data"), "+RTS", "-M512m", "-RTS"]
        try:
            response = subprocess.run(argv, input=source, capture_output=True, cwd=executable.parent, env=environment,
                timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise CoreError("EXPORT_TIMEOUT", "转换超过一分钟，已停止本地转换；原文没有变化。") from None
        if response.returncode or not response.stdout:
            raise CoreError("EXPORT_FAILED", "本地转换未完成，原文保持不变；可先选择其他格式。")
        data = response.stdout
    if format == "epub":
        # Preserve the mature EPUB container/navigation and add only our fixed
        # validated stylesheet. No scripts, links or arbitrary file inclusion.
        original, output = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
        with original, zipfile.ZipFile(output, "w") as archive:
            for info in original.infolist():
                payload = original.read(info)
                if info.filename.endswith(".css"):
                    payload += ("\n" + css(config)).encode()
                if info.filename.endswith(".opf") and any(b["kind"] == "image" for b in bundle["blocks"]):
                    from .material_formats import xml
                    from lxml.etree import SubElement, tostring
                    metadata_xml = xml(payload)
                    namespace = "{http://www.idpf.org/2007/opf}"
                    cover_item = next((n for n in metadata_xml.findall(".//" + namespace + "item") if n.get("media-type") == "image/png"), None)
                    if cover_item is not None:
                        cover_item.set("properties", "cover-image")
                        metadata_node = metadata_xml.find(namespace + "metadata")
                        if metadata_node is not None:
                            SubElement(metadata_node, namespace + "meta", name="cover", content=cover_item.get("id"))
                        payload = tostring(metadata_xml, encoding="utf-8", xml_declaration=True)
                archive.writestr(info, payload)
        data = output.getvalue()
    return data


def render(bundle, config, format, cover=None):
    if format in {"txt", "md", "html"}:
        return bundle[{"txt": "text", "md": "markdown", "html": "html"}[format]].encode("utf-8")
    if format == "docx":
        return docx(bundle, config, cover)
    if format == "pdf":
        return pdf(bundle, config)
    if format == "odt":
        return odt(bundle, config)
    if format in CONVERTER_FORMATS:
        return pandoc(bundle, config, format)
    raise CoreError("EXPORT_INVALID", "未知导出格式。")
