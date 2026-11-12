"""Offline adapters around maintained parsers. Originals never rewritten here."""
from __future__ import annotations

import io
import json
from pathlib import PurePosixPath
import posixpath
import re
import stat
from urllib.parse import unquote, urlsplit
import zipfile

SOURCE_LIMIT = 32 * 1024 * 1024
TEXT_LIMIT = SOURCE_LIMIT
FORMATS = (".txt", ".md", ".markdown", ".json", ".docx", ".pdf", ".epub", ".rtf", ".html", ".htm", ".odt")


class ParseError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def text_decode(payload):
    try:
        if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
            return payload.decode("utf-16")
        return payload.decode("utf-8-sig")
    except UnicodeError:
        raise ParseError("ENCODING_UNSUPPORTED", "文本编码无法准确识别，请用原软件另存为 UTF-8 后重试；程序没有忽略坏字节。") from None


def xml(payload):
    from lxml import etree
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ParseError("UNSAFE_XML", "文件包含 DTD/实体声明，已停止解析，不会读取外部文件或网络。")
    return etree.fromstring(payload, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False))


def archive(payload):
    result = zipfile.ZipFile(io.BytesIO(payload))
    entries = result.infolist()
    if len(entries) > 2500 or sum(i.file_size for i in entries) > 48 * 1024 * 1024:
        raise ParseError("ARCHIVE_LIMIT", "压缩文档展开后过大或文件过多，请先精简附件。")
    seen = set()
    for item in entries:
        path = PurePosixPath(item.filename)
        if item.filename in seen or path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename or stat.S_ISLNK(item.external_attr >> 16):
            raise ParseError("UNSAFE_ARCHIVE", "压缩文档包含重复、越界或链接路径，已停止导入。")
        seen.add(item.filename)
        if item.file_size > 12 * 1024 * 1024 or item.file_size > max(1, item.compress_size) * 250:
            raise ParseError("ARCHIVE_LIMIT", "压缩文档存在异常膨胀风险，已停止导入。")
    return result


def html_text(payload):
    from lxml import html
    encoding = re.search(rb"charset\s*=\s*['\"]?([a-zA-Z0-9_-]+)", payload[:4096])
    tree = html.fromstring(payload, parser=html.HTMLParser(no_network=True, huge_tree=False, encoding=encoding[1].decode("ascii") if encoding else "utf-8"))
    warnings = []
    unsafe = tree.xpath("//script|//style|//iframe|//object|//embed|//svg")
    if unsafe:
        warnings.append("脚本、样式和嵌入资源未执行；原件完整保留，正文不包含这些非正文元素。")
    for node in unsafe:
        node.drop_tree()
    pieces = []
    blocks = {"p", "div", "section", "article", "li", "tr", "br", "hr", "blockquote", "header", "footer"}
    def visit(node):
        tag = node.tag.lower() if isinstance(node.tag, str) else ""
        heading = bool(re.fullmatch(r"h[1-6]", tag))
        if tag in blocks or heading:
            pieces.append("\n")
        if heading:
            pieces.append("#" * int(tag[1]) + " ")
        if tag == "img":
            pieces.append("[图片：" + node.get("alt", "无文字说明") + "]")
            warnings.append("文档含图片；没有读取远程图片或执行 OCR。")
        if node.text:
            pieces.append(node.text)
        for child in node:
            visit(child)
            if child.tail:
                pieces.append(child.tail)
        if tag in blocks or heading:
            pieces.append("\n")
    visit(tree)
    return "".join(pieces), list(dict.fromkeys(warnings))


def docx_text(payload):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    warnings, pieces = [], []
    with archive(payload) as package:
        if "word/document.xml" not in package.namelist():
            raise ParseError("FORMAT_MISMATCH", "文件不是有效的 Word DOCX 文档。")
        for name in package.namelist():
            if name.endswith((".xml", ".rels")):
                xml(package.read(name))
        body = package.read("word/document.xml")
        if any(mark in body for mark in (b"txbxContent", b"<w:ins", b"<w:del", b"drawing", b"oMath")):
            warnings.append("Word 含文本框、修订、图片或公式等复杂对象，正文提取可能不完整，请核对保留的原件。")
        if any(name in package.namelist() for name in ("word/footnotes.xml", "word/endnotes.xml", "word/comments.xml")):
            warnings.append("Word 含脚注、尾注或批注；这些附属对象需在原件核对，不伪称已无损转换。")
    document = Document(io.BytesIO(payload))
    def content(container):
        for item in container.iter_inner_content():
            if isinstance(item, Paragraph):
                style = item.style.name if item.style else ""
                match = re.search(r"(?:Heading|标题)\s*([1-9])", style, re.I)
                prefix = "#" * min(6, int(match[1])) + " " if match else ""
                pieces.append(prefix + item.text + "\n")
            elif isinstance(item, Table):
                for row in item.rows:
                    visited = set()
                    for cell in row.cells:
                        if id(cell._tc) not in visited:
                            visited.add(id(cell._tc))
                            content(cell)
                    pieces.append("\n")
    content(document)
    return "".join(pieces), warnings


def epub_text(payload):
    pieces, warnings = [], []
    with archive(payload) as package:
        names = set(package.namelist())
        if "META-INF/container.xml" not in names:
            raise ParseError("FORMAT_MISMATCH", "EPUB 缺少 container.xml。")
        container = xml(package.read("META-INF/container.xml"))
        roots = container.findall(".//{*}rootfile")
        if not roots:
            raise ParseError("FORMAT_MISMATCH", "EPUB 缺少书籍目录包。")
        opf_name = roots[0].get("full-path", "")
        if opf_name not in names:
            raise ParseError("UNSAFE_ARCHIVE", "EPUB 目录包路径无效。")
        opf = xml(package.read(opf_name))
        items = {el.get("id"): el for el in opf.findall(".//{*}manifest/{*}item")}
        order = [el.get("idref") for el in opf.findall(".//{*}spine/{*}itemref")]
        used = set()
        for identifier in order:
            item = items.get(identifier)
            if item is None:
                raise ParseError("FORMAT_MISMATCH", "EPUB spine 引用了缺失的章节。")
            href = urlsplit(item.get("href", ""))
            name = posixpath.normpath(posixpath.join(posixpath.dirname(opf_name), unquote(href.path)))
            if href.scheme or href.netloc or name.startswith(("../", "/")) or name not in names:
                raise ParseError("UNSAFE_ARCHIVE", "EPUB 章节指向外部或缺失资源，不会联网读取。")
            body, notes = html_text(package.read(name))
            pieces.append("\n# EPUB 章节：" + PurePosixPath(name).name + "\n" + body)
            warnings.extend(notes)
            used.add(name)
        extras = sorted(name for name in names if name.lower().endswith((".xhtml", ".html", ".htm")) and name not in used)
        for name in extras:
            body, notes = html_text(package.read(name))
            pieces.append("\n# EPUB 附加文档：" + PurePosixPath(name).name + "\n" + body)
            warnings.extend(notes)
        if extras:
            warnings.append("已在 spine 顺序之后附上未被 spine 引用的 HTML，可能包含导航重复。")
    return "".join(pieces), list(dict.fromkeys(warnings))


def odt_text(payload):
    with archive(payload) as package:
        if "content.xml" not in package.namelist():
            raise ParseError("FORMAT_MISMATCH", "ODT 缺少 content.xml。")
        root = xml(package.read("content.xml"))
    namespace = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    pieces = []
    def text(node):
        if node.tag == namespace + "s":
            return " " * min(1000, int(node.get(namespace + "c", "1")))
        if node.tag == namespace + "tab":
            return "\t"
        if node.tag == namespace + "line-break":
            return "\n"
        return (node.text or "") + "".join(text(child) + (child.tail or "") for child in node)
    for node in root.iter():
        if node.tag not in {namespace + "p", namespace + "h"}:
            continue
        if any(p.tag in {namespace + "p", namespace + "h"} for p in node.iterancestors()):
            continue
        prefix = "#" * min(6, max(1, int(node.get(namespace + "outline-level", "1")))) + " " if node.tag == namespace + "h" else ""
        pieces.append(prefix + text(node) + "\n")
    return "".join(pieces), ["ODT 仅提取正文文本与标题；图像、公式和版式请核对原件。"]


def parse(payload, suffix):
    suffix = suffix.lower()
    if suffix not in FORMATS:
        raise ParseError("FORMAT_UNSUPPORTED", "此格式尚未实现解析。")
    if not payload or len(payload) > SOURCE_LIMIT:
        raise ParseError("SOURCE_LIMIT", "文件为空或超过 32 MiB 上限；没有截断导入。")
    warnings = []
    if suffix in {".txt", ".md", ".markdown", ".json"}:
        body = text_decode(payload)
        if suffix == ".json":
            json.loads(body)
            warnings.append("JSON 作为完整原文保留，不执行酒馆宏、脚本或自动推断其提示优先级。")
    elif suffix in {".html", ".htm"}:
        body, warnings = html_text(payload)
    elif suffix == ".docx":
        body, warnings = docx_text(payload)
    elif suffix == ".epub":
        body, warnings = epub_text(payload)
    elif suffix == ".odt":
        body, warnings = odt_text(payload)
    elif suffix == ".rtf":
        from striprtf.striprtf import rtf_to_text
        if not payload.lstrip().startswith(b"{\\rtf"):
            raise ParseError("FORMAT_MISMATCH", "文件扩展名是 RTF，但内容不是 RTF。")
        depth = 0
        for token in re.finditer(rb"\\[\\{}]|[{}]", payload):
            depth += 1 if token[0] == b"{" else -1 if token[0] == b"}" else 0
            if depth < 0 or depth > 200:
                raise ParseError("MALFORMED_RTF", "RTF 分组损坏或嵌套过深。")
        if depth:
            raise ParseError("MALFORMED_RTF", "RTF 文档没有完整结束。")
        match = re.search(rb"\\ansicpg(\d+)", payload[:1024])
        codec = "cp" + match[1].decode("ascii") if match else "cp1252"
        body = rtf_to_text(payload.decode(codec), encoding=codec, errors="strict")
        warnings.append("RTF 提取正文，不执行对象；图片和排版请核对保留的原件。")
    else:
        from pypdf import PdfReader, apply_configuration
        from pypdf.errors import DependencyError
        if not payload.startswith(b"%PDF-"):
            raise ParseError("FORMAT_MISMATCH", "文件扩展名是 PDF，但内容不是 PDF。")
        # Official library configuration disables its optional external
        # jbig2dec subprocess, even if that binary exists on the user's PATH.
        with apply_configuration(jbig2dec_binary=None, disable_legacy_handling=True):
            reader = PdfReader(io.BytesIO(payload), strict=True)
            if reader.is_encrypted:
                raise ParseError("PDF_ENCRYPTED", "加密 PDF 暂不支持，请在原软件合法解密后重试。")
            if len(reader.pages) > 300:
                raise ParseError("PDF_PAGE_LIMIT", "PDF 超过本版 300 页上限，请分卷导入。")
            texts = []
            for i, page in enumerate(reader.pages, 1):
                try:
                    value = page.extract_text() or ""
                except DependencyError:
                    raise ParseError("PDF_DECODER_DISABLED", "PDF 需要本版禁用的外部或可选解码器；不会执行外部命令，请在原软件另存为普通文本 PDF 后重试。") from None
                if not value.strip():
                    warnings.append(f"第 {i} 页没有可提取文本，可能为扫描页；本程序不支持 OCR。")
                texts.append((i, value))
        if not any(t.strip() for _, t in texts):
            raise ParseError("PDF_OCR_REQUIRED", "PDF 没有可提取正文，可能为扫描件；当前不支持 OCR。")
        body = "\n\n".join(f"## 第 {i} 页\n" + value for i, value in texts)
        warnings.append("PDF 按页提取；多栏、表格及复杂排版的文本顺序需要人工核对原件。")
    if not body.strip() or "\0" in body:
        raise ParseError("EMPTY_TEXT", "未能提取有效正文，未创建导入记录。")
    if len(body.encode("utf-8")) > TEXT_LIMIT:
        raise ParseError("TEXT_LIMIT", "提取正文超过本版 3 MiB 上限，未截断或导入，请先分卷处理。")
    return {"text": body, "format": suffix, "warnings": list(dict.fromkeys(warnings)), "sections": heading_index(body)}


def heading_index(body):
    from markdown_it import MarkdownIt
    lines = body.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    tokens = MarkdownIt("commonmark", {"html": False}).parse(body)
    headings = [(token.map[0], int(token.tag[1]), tokens[i + 1].content) for i, token in enumerate(tokens) if token.type == "heading_open" and token.map and token.level == 0]
    if len(headings) > 4096:
        raise ParseError("INDEX_LIMIT", "标题数量超过本版安全上限。")
    if not headings or headings[0][0] > 0:
        headings.insert(0, (0, 0, "文件导语" if headings else "全文"))
    result, stack = [], []
    for i, (line, level, title) in enumerate(headings):
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1] if stack and level else None
        identifier = f"s{i:04d}"
        end_line = headings[i + 1][0] if i + 1 < len(headings) else len(lines)
        result.append({"id": identifier, "title": title, "level": level, "parent": parent,
                       "start": offsets[line], "end": offsets[end_line], "start_line": line, "end_line": end_line})
        if level:
            stack.append((level, identifier))
    return result
