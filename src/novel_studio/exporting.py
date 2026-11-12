"""Author-only version-pinned export. No Bridge method, no source mutations."""
from __future__ import annotations

import json
from pathlib import Path
import re
import secrets
import time
from .core import CoreError, ROOT, SafeTree, admin, checked_text, digest
from .document_titles import numbered_title
from .text_metrics import measure

FORMATS = {"docx": "Word 文档", "pdf": "PDF 阅读/打印", "epub": "EPUB 电子书", "md": "Markdown", "txt": "纯文本", "html": "HTML 网页", "rtf": "RTF 文档", "odt": "OpenDocument"}
DEFAULTS = {"title": "", "author": "", "subtitle": "", "copyright": "", "preface": "", "toc": True,
    "volumes": True, "header": "", "footer": "", "page_numbers": True, "font": "Microsoft YaHei",
    "font_size": 11, "line_spacing": 1.5, "paragraph_spacing": 6, "first_indent": 2, "chapter_break": True}
TEMPLATES = {"普通阅读": {}, "网络小说": {"line_spacing": 1.7, "paragraph_spacing": 10, "chapter_break": False},
    "正式投稿": {"font": "SimSun", "font_size": 12, "line_spacing": 2., "paragraph_spacing": 0, "first_indent": 2}}


def options(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise CoreError("EXPORT_INVALID", "导出排版选项不完整。")
    for key in ("title", "author", "subtitle", "copyright", "preface", "header", "footer", "font"):
        checked_text(value[key], 60000 if key in {"copyright", "preface"} else 600)
    if not value["title"].strip() or not re.fullmatch(r"[\w .\-]{1,80}", value["font"], re.UNICODE):
        raise CoreError("EXPORT_INVALID", "请填写书名，并选择普通字体名称。")
    if any(type(value[k]) is not bool for k in ("toc", "volumes", "page_numbers", "chapter_break")):
        raise CoreError("EXPORT_INVALID", "排版开关格式不正确。")
    for key, low, high in (("font_size", 9, 28), ("line_spacing", 1, 3), ("paragraph_spacing", 0, 30), ("first_indent", 0, 4)):
        if type(value[key]) not in {int, float} or not low <= value[key] <= high:
            raise CoreError("EXPORT_INVALID", "字号、行距、段距或缩进超出支持范围。")
    return json.loads(admin.encode(value))


class ExportService:
    def __init__(self, core):
        self.core, self.previews = core, {}

    def catalog(self):
        docs = self.core.documents(_areas=('drafts', 'canonical'), _manuscript_counts=True)
        rows = [{"source_id": row["document_id"], "title": row["display_title"], "version": "当前正式版本" if row["area"] == "canonical" else "当前草稿",
            "draft": row["area"] == "drafts", "relative": row["relative"], "revision": row["revision"], "document_id": row["document_id"], "word_count": row['word_count']}
            for row in docs if row["area"] == "drafts" or row["relative"].startswith("Canonical/Final-Manuscript/")]
        directory = {}
        for chapter in self.core.workflow.chapters.catalog():
            for kind in ("draft", "canonical"):
                reference = chapter.get(kind)
                if reference and chapter.get("title"):
                    number = chapter.get("canonical_number", chapter["number"]) if kind == "canonical" else chapter["number"]
                    volume = chapter.get("canonical_volume", chapter["volume"]) if kind == "canonical" else chapter["volume"]
                    placed = chapter.get('directory', {}) if kind == 'draft' else {}
                    volume, number = placed.get('volume', volume), placed.get('position', number)
                    directory[reference["document_id"]] = {"title": numbered_title(number, chapter['title']),
                        "volume": numbered_title(volume, placed.get('volume_title', chapter['volume_title']), '卷'),
                        'volume_number':volume, 'chapter_number':number, 'chapter_id':chapter['chapter_id']}
        for row in rows:
            row.update(directory.get(row["document_id"], {}))
        with SafeTree(self.core.project) as tree:
            for path in tree.files("Backups/Transactions"):
                if not path.endswith("/COMMITTED.json"):
                    continue
                receipt = json.loads(tree.read(path))
                identifier = receipt.get("transaction_id")
                if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
                    raise CoreError("EXPORT_INVALID", "正式版本历史记录格式异常。")
                target = receipt.get("target", "")
                if not isinstance(target, str) or not target.startswith("Canonical/Final-Manuscript/") or Path(target).suffix.lower() not in {".md", ".txt"}:
                    continue
                relative = "Backups/Transactions/" + identifier + "/after.bin"
                raw = tree.read(relative)
                if digest(raw) != receipt.get("after_sha256"):
                    raise CoreError("EXPORT_CHANGED", "正式历史版本的哈希不匹配，已停止导出。")
                text = raw.decode("utf-8-sig")
                title = next((line.lstrip("#").strip() for line in text.splitlines() if line.strip()), Path(target).stem)
                rows.append({"source_id": "history_" + identifier, "title": title[:120], "version": "已批准历史 · " + identifier[:8],
                    "draft": False, "relative": relative, "revision": digest(raw), "document_id": None, "transaction_id": identifier, "target": target,
                    "word_count": self.core.measure_text(text)['count']})
            by_target = {r['relative']: r for r in rows if r['relative'].startswith('Canonical/Final-Manuscript/')}
            for row in rows:
                row['historical'] = row['source_id'].startswith('history_')
                if row['historical'] and row.get('target') in by_target:
                    current = by_target[row['target']]
                    row.update({k:current[k] for k in ('volume', 'volume_number', 'chapter_number', 'chapter_id') if k in current})
                    if row.get('chapter_number'):
                        row['title'] = numbered_title(row['chapter_number'], row['title'])
                row.setdefault('volume', '未分卷')
        return sorted(rows, key=lambda r:(r.get('volume_number', 10001), r.get('chapter_number', 10001), r.get('chapter_id', r.get('target', r['relative'])), r['draft'], r['historical'], r['version']))

    def _ordered(self, node_id, rows):
        nodes = self.core.lengths.plan()["nodes"]
        if node_id not in nodes or nodes[node_id]["kind"] not in {"book", "volume", "chapter"}:
            raise CoreError("EXPORT_INVALID", "请选择已有全书、分卷或章节规划。")
        by_id = {row["document_id"]: row for row in rows if row["document_id"]}
        selected = []
        def visit(key, volume=""):
            node = nodes[key]
            if node["kind"] == "volume":
                volume = node["title"]
            if node["kind"] == "chapter":
                source = node.get("source") or {}
                row = by_id.get(source.get("document_id"))
                if row is None:
                    raise CoreError("EXPORT_INVALID", "规划中的章节没有关联所选正式版本；请先在字数管理中关联，或使用自选范围。")
                selected.append({**row, "title": node["title"], "volume": volume})
                return
            children = [k for k, n in nodes.items() if n.get("parent") == key and n["kind"] != "scene"]
            for child in sorted(children, key=lambda k: nodes[k].get("order", 0)):
                visit(child, volume)
        visit(node_id)
        return selected

    def select(self, scope="approved", source_ids=None, node_id=None):
        rows = self.catalog()
        if scope == "approved":
            return [r for r in rows if not r["draft"] and not r["source_id"].startswith("history_")]
        if scope == "plan":
            # This route intentionally permits only formal versions. A planned
            # draft must be explicitly selected through custom/current-draft.
            return self._ordered(node_id, [r for r in rows if not r["draft"]])
        if scope not in {"custom", "draft", "history"} or not isinstance(source_ids, list) or len(source_ids) != len(set(source_ids)):
            raise CoreError("EXPORT_INVALID", "导出范围或版本选择不正确。")
        available = {r["source_id"]: r for r in rows}
        selected = []
        for key in source_ids:
            row = available.get(key)
            if row is None or scope == "draft" and not row["draft"] or scope == "history" and not key.startswith("history_"):
                raise CoreError("EXPORT_INVALID", "所选版本不属于当前作品或不适合此导出范围。")
            selected.append(row)
        return selected

    def preview(self, selected, config, format, cover=None):
        from .export_formats import assemble, cover_image, warnings
        if format not in FORMATS:
            raise CoreError("EXPORT_INVALID", "请选择支持的导出格式。")
        config = options(config)
        if not isinstance(selected, list) or not 1 <= len(selected) <= 500:
            raise CoreError("EXPORT_EMPTY", "所选范围没有可导出的章节，或超过单次 500 章限制。")
        available = {r["source_id"]: r for r in self.catalog()}
        sources, seen, size = [], set(), 0
        with self.core.mutex, SafeTree(self.core.project) as tree:
            self.core.require_idle()
            for item in selected:
                row = available.get(item.get("source_id"))
                if row is None or row["source_id"] in seen or row["revision"] != item.get("revision"):
                    raise CoreError("EXPORT_CHANGED", "所选章节已改变或重复，请重新选择。")
                seen.add(row["source_id"])
                raw = tree.read(row["relative"])
                if digest(raw) != row["revision"]:
                    raise CoreError("EXPORT_CHANGED", "正文在预览时变化，请重新预览。")
                size += len(raw)
                if size > 8 * 1024 * 1024:
                    raise CoreError("EXPORT_LIMIT", "本次正文超过 8 MB，请按分卷导出。")
                title, volume = item.get("title", row["title"]), item.get("volume", "")
                checked_text(title, 600)
                checked_text(volume, 600)
                sources.append({**row, "title": title, "volume": volume, "text": raw.decode("utf-8-sig")})
        image = cover_image(Path(cover)) if cover else None
        bundle = assemble(sources, config, image)
        handle = secrets.token_urlsafe(24)
        self.previews = {k: v for k, v in self.previews.items() if v["expires"] > time.monotonic()}
        if len(self.previews) >= 3:
            self.previews.pop(next(iter(self.previews)))
        value = {"expires": time.monotonic() + 600, "sources": sources, "config": config, "format": format, "cover": image, "bundle": bundle}
        self.previews[handle] = value
        return {"handle": handle, "html": bundle["html"], "text": bundle["text"], "sources": [{k: r[k] for k in ("source_id", "title", "version", "draft", "revision")} for r in sources],
            "warnings": warnings(format, image), "draft": any(r["draft"] for r in sources), "format": format,
            "note": "内容预览。PDF/DOCX 与流式电子书的实际分页由各格式排版；不是跨格式像素一致承诺。"}

    def export(self, handle, target, confirmed=False):
        from .export_formats import render
        value = self.previews.get(handle)
        if not confirmed or not value or value["expires"] < time.monotonic():
            raise CoreError("EXPORT_PREVIEW_REQUIRED", "请先查看导出内容和来源，并确认导出。")
        target = Path(target)
        if not target.is_absolute() or ".." in target.parts or target.suffix.lower() != "." + value["format"]:
            raise CoreError("EXPORT_INVALID", "请使用正确格式的本地完整保存位置。")
        if any(target.is_relative_to(root) for root in (self.core.project, ROOT, self.core.plugin)):
            raise CoreError("EXPORT_PATH_DENIED", "请选择作品工程和插件安装目录之外的导出文件夹，避免影响原始资料。")
        with self.core.mutex, SafeTree(self.core.project) as source_tree:
            self.core.require_idle()
            with source_tree.lock():
                for row in value["sources"]:
                    if digest(source_tree.read(row["relative"])) != row["revision"]:
                        raise CoreError("EXPORT_CHANGED", "源版本在预览后变化，请重新预览。")
                with SafeTree(target.parent, limit=64 * 1024 * 1024) as output:
                    receipt_name = target.name + ".sources.json"
                    if output.maybe_read(target.name) is not None or output.maybe_read(receipt_name) is not None:
                        raise CoreError("EXPORT_EXISTS", "目标文件或来源记录已存在，请选择新的名称；不会覆盖。")
                    data = render(value["bundle"], value["config"], value["format"], value["cover"])
                    if not data or len(data) > 64 * 1024 * 1024:
                        raise CoreError("EXPORT_LIMIT", "导出结果为空或超过 64 MB，未发布。")
                    for row in value["sources"]:
                        if digest(source_tree.read(row["relative"])) != row["revision"]:
                            raise CoreError("EXPORT_CHANGED", "源版本在生成期间变化，未发布。")
                    receipt = {"format": "novel-studio-export-v1", "created_at": time.time(), "output": target.name, "sha256": digest(data),
                        "file_format": value["format"], "settings": value["config"], "sources": [{k: v for k, v in row.items() if k not in {"text", "relative"}} for row in value["sources"]],
                        "draft": any(r["draft"] for r in value["sources"]), "cover_sha256": value["cover"]["original_sha256"] if value["cover"] else None,
                        "publication": "source receipt written first; retained if no-clobber file publication fails"}
                    # Both names are no-clobber. On a crash/failure the receipt
                    # may remain without output; never delete or modify sources.
                    output.publish(receipt_name, admin.encode(receipt), None)
                    output.publish(target.name, data, None)
        self.previews.pop(handle, None)
        return {"path": str(target), "receipt": str(target.with_name(receipt_name)), "sha256": digest(data), "bytes": len(data)}
