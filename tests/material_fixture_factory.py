"""Synthetic documents only. No user's books, external converters or font files."""
import io
from pathlib import Path
import zipfile


def preset(large=False):
    body = "# 雾港综合预设\r\n\r\n## 全局写作规则\r\n- 作者拥有最终裁决权。\r\n- 林舟不知道蓝信内容，不得提前泄露。\r\n### 叙事约束\r\n保留铜钥匙伏笔，暂不回收。\r\n\r\n## 人物关系\r\n### 林舟\r\n林舟是钟楼修理工，与周岚旧识，尚不知道蓝信的秘密。\r\n### 周岚\r\n周岚保管蓝信，知道父亲的往事，不替林舟作决定。\r\n\r\n## 文风要求\r\n保持克制和具体动作，允许安静场景，不强制商业断章。\r\n\r\n## 世界观资料\r\n雾港的钟楼年份在两份档案中记载不同，留作者裁决。\r\n\r\n## 时间线\r\n6月4日回镇；6月5日早晨审查钟楼。\r\n\r\n## 特殊剧情约束\r\n铜钥匙保持未回收；钟楼1971/1973年份不自动统一。\r\n"
    if large:
        body += "\r\n## 大型背景档案\r\n"
        for i in range(900):
            body += f"### 档案{i:04d}\r\n" + (f"该处记录雾港第{i}号街区的雨季、旧厂、灯塔与居民往来。仅为合成背景数据，不能覆盖全局规则。" * 6) + "\r\n"
    return body


def pdf_bytes():
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, ArrayObject, NumberObject, TextStringObject, DecodedStreamObject
    writer = PdfWriter()
    descendant = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/CIDFontType0"),
        NameObject("/BaseFont"): NameObject("/STSong-Light"), NameObject("/CIDSystemInfo"): DictionaryObject({NameObject("/Registry"): TextStringObject("Adobe"), NameObject("/Ordering"): TextStringObject("GB1"), NameObject("/Supplement"): NumberObject(4)})})
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type0"),
        NameObject("/BaseFont"): NameObject("/STSong-Light"), NameObject("/Encoding"): NameObject("/UniGB-UCS2-H"), NameObject("/DescendantFonts"): ArrayObject([descendant])})
    for text in ("PDF第一页：林舟修理钟楼。", "PDF第二页：周岚保管蓝信。"):
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 18 Tf 40 770 Td <" + text.encode("utf-16-be").hex().encode("ascii") + b"> Tj ET")
        page[NameObject("/Contents")] = stream
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def samples(folder):
    from docx import Document
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    result = {".md": preset().encode("utf-8"), ".txt": "中文原文……——\r\n林舟与周岚。\r\n".encode("utf-8"),
              ".json": '{"全局规则":"保留作者裁决","人物":["林舟","周岚"]}'.encode("utf-8"),
              ".html": '<!DOCTYPE html><html><meta charset="utf-8"><body><h1>HTML设定</h1><p>林舟在雾港。</p><script>CANARY_SCRIPT_MUST_NOT_EXECUTE</script><img src="https://example.invalid/no-network.png" alt="铜钥匙"></body></html>'.encode("utf-8")}
    document = Document()
    document.add_heading("Word人物设定", level=1)
    document.add_paragraph("林舟修理钟楼，周岚保管蓝信。")
    document.add_heading("人物关系", level=2)
    document.add_table(rows=1, cols=2).rows[0].cells[0].text = "表格左侧：林舟"
    document.tables[0].rows[0].cells[1].text = "表格右侧：周岚"
    data = io.BytesIO()
    document.save(data)
    result[".docx"] = data.getvalue()
    rtf = "RTF人物：林舟与周岚。\n保留铜钥匙。"
    encoded = "".join("\\par " if c == "\n" else c if ord(c) < 128 else "\\u" + str(ord(c) if ord(c) < 32768 else ord(c) - 65536) + "?" for c in rtf)
    result[".rtf"] = ("{\\rtf1\\ansi\\ansicpg1252 " + encoded + "}").encode("ascii")
    result[".pdf"] = pdf_bytes()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
        z.writestr("OEBPS/book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="first" href="first.xhtml" media-type="application/xhtml+xml"/><item id="second" href="second.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="first"/><itemref idref="second"/></spine></package>')
        z.writestr("OEBPS/second.xhtml", '<html><meta charset="utf-8"><body><h1>EPUB第二章</h1><p>周岚保管蓝信。</p></body></html>')
        z.writestr("OEBPS/first.xhtml", '<html><meta charset="utf-8"><body><h1>EPUB第一章</h1><p>林舟来到雾港。</p></body></html>')
    result[".epub"] = out.getvalue()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        z.writestr("content.xml", '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text><text:h text:outline-level="1">ODT时间线</text:h><text:p>林舟6月4日回镇。</text:p><text:p>周岚<text:s text:c="2"/>等待蓝信。</text:p></office:text></office:body></office:document-content>')
    result[".odt"] = out.getvalue()
    files = {}
    for suffix, payload in result.items():
        path = folder / ("合成资料" + suffix)
        path.write_bytes(payload)
        files[suffix] = path
    return files


def external_decoder_pdf():
    from pypdf import PdfWriter
    from pypdf.generic import EncodedStreamObject, NameObject, DictionaryObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=400)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = EncodedStreamObject()
    stream._data = b"SYNTHETIC_MUST_NOT_LAUNCH_DECODER"
    stream[NameObject("/Filter")] = NameObject("/JBIG2Decode")
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
