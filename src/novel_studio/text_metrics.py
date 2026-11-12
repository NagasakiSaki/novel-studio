"""Markdown-aware local counting; reuse the pinned Oh Story body preparation."""
from __future__ import annotations

from functools import lru_cache
import re
import unicodedata
from .core import checked_text

METRICS = {
    "novel_visible_chars_v1": "有效字符：排除篇首标题、可识别元数据、空白和 Markdown 标记；汉字、英文字母、数字、标点及其他可见字符逐个计数。",
    "han_letters_digits_v1": "汉字、英文字母和数字：排除标题/元数据/格式/空白/标点；英文字母与数字逐个计数。",
    "han_english_words_v1": "汉字加英文词：每个汉字计 1，连续英文词和连续数字各计 1；标点和空白不计。",
}


@lru_cache(maxsize=1)
def upstream():
    from novelctl import load_upstream
    return load_upstream("wordcount_core.py")


def plaintext(text):
    from markdown_it import MarkdownIt
    checked_text(text, 8 * 1024 * 1024)
    body = upstream().visible_body(text)
    fragments = []
    for token in MarkdownIt("commonmark", {"html": False}).parse(body):
        if token.type == "inline":
            fragments.append("".join(t.content if t.type in {"text", "code_inline"} else "\n" if t.type in {"softbreak", "hardbreak"} else "" for t in token.children or []))
        elif token.type in {"fence", "code_block"}:
            fragments.append(token.content)
    return "\n".join(fragments)


def measure(text, metric="novel_visible_chars_v1"):
    if metric not in METRICS:
        raise ValueError("Unknown local counting metric")
    body = plaintext(text)
    visible = [c for c in body if not c.isspace() and unicodedata.category(c) != "Cf"]
    han = sum(bool(re.fullmatch(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0003134f]", c)) for c in visible)
    latin = sum(c.isascii() and c.isalpha() for c in visible)
    digits = sum(c.isascii() and c.isdigit() for c in visible)
    punctuation = sum(unicodedata.category(c).startswith("P") for c in visible)
    english_words = len(re.findall(r"[A-Za-z]+(?:['’\-][A-Za-z]+)*|[0-9]+", body))
    counts = {"novel_visible_chars_v1": len(visible), "han_letters_digits_v1": han + latin + digits,
              "han_english_words_v1": han + english_words}
    return {"metric": metric, "description": METRICS[metric], "count": counts[metric], "han": han, "latin_letters": latin,
            "digits": digits, "punctuation": punctuation, "other_visible": len(visible) - han - latin - digits - punctuation,
            "english_or_number_words": english_words, "counting_note": "按 Unicode 字符而非视觉字形；组合字形可能包含多个字符。不用于自动补写或删稿。"}
