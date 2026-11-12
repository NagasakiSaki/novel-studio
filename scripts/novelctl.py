#!/usr/bin/env python3
"""Offline, read-only adapters for pinned upstream novel tools (Python 3.10+)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
from types import ModuleType

from secure_read import read_bytes

sys.dont_write_bytecode = True
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MAX_INPUT_BYTES = 8 * 1024 * 1024


def reject_links(path: Path) -> None:
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("Symbolic links and reparse points are not accepted for project inputs")


def project_root(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Project must be an explicit absolute path without '..'")
    reject_links(path)
    real = path.absolute()
    if not real.is_dir():
        raise ValueError("Project must be a directory")
    if real.is_relative_to(PLUGIN_ROOT):
        raise ValueError("User projects must be outside the plugin installation")
    return real


def input_file(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValueError("Input must be a relative file inside the specified project")
    candidate = root / relative
    reject_links(candidate)
    real = candidate.absolute()
    if not real.is_relative_to(root) or real.is_relative_to(PLUGIN_ROOT):
        raise ValueError("Input escapes the project or enters the plugin installation")
    if not real.is_file() or real.suffix.lower() not in {".md", ".txt"}:
        raise ValueError("Input must be a Markdown or text file")
    if real.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Input exceeds the 8 MiB read limit")
    return real


def load_upstream(filename: str):
    relative = "vendor/oh-story/skills/story-long-write/scripts/" + filename
    # Desktop keeps its reviewed plugin payload next to the application. This
    # location comes from our code directory, never from a novel's metadata.
    component_root = PLUGIN_ROOT / "payload/novel-studio" if (PLUGIN_ROOT / "payload/novel-studio").is_dir() else PLUGIN_ROOT
    lock = json.loads(read_bytes(component_root, "integrations.lock.json", 1024 * 1024).decode("utf-8"))
    matches = [row for row in lock["files"] if row["destination"] == relative]
    if len(matches) != 1:
        raise ValueError("Upstream component is missing from the reviewed lock")
    path = component_root / relative
    reject_links(path)
    payload = read_bytes(component_root, relative, MAX_INPUT_BYTES)
    if hashlib.sha256(payload).hexdigest() != matches[0]["sha256"]:
        raise ValueError("Upstream component integrity check failed")
    module = ModuleType("novel_studio_" + path.stem)
    module.__file__ = str(path)
    # Execute exactly the bytes already read and verified, without reopening.
    code = compile(payload, str(path), "exec")
    exec(code, module.__dict__)
    return module


def count_text(text: str) -> dict:
    return load_upstream("wordcount_core.py").measure_wordcount(text)


def outline_slice(text: str, chapter: int) -> str:
    if chapter < 1:
        raise ValueError("Chapter must be positive")
    module = load_upstream("outline_view.py")
    # Compatibility belongs here; upstream bytes remain unchanged.
    module.CH_RANGE = re.compile(r"章节范围\*?\*?[：:]\s*第?\s*(\d+)\s*章?\s*[-–—~至到]\s*第?\s*(\d+)\s*章")
    lines, sections = module.parse(text)
    if not sections or any(section.scope is None or section.kind is None for section in sections):
        raise ValueError("Every outline section needs an explicit valid scope")
    active = [section for section in sections if section.status != "已退役"]
    unit = module.unit_for_chapter(active, chapter)
    if not unit:
        raise ValueError("No declared outline unit covers this chapter")
    span = module.unit_chapter_range(active, unit)
    chosen = [section for section in active if section.kind == "卷级常任" or (section.unit == unit and section.kind == "单元级")]
    output = ["\n".join(module.strip_retired(lines[:sections[0].start], False)).rstrip()]
    for section in chosen:
        body = module.slice_arc(section, span) if "情绪弧线" in section.title or "排班" in section.title else section.lines
        output.append("\n".join(module.strip_retired(body, False)).rstrip())
    return "\n\n".join(part for part in output if part) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("count", "outline"):
        child = commands.add_parser(command)
        child.add_argument("--project", required=True, help="Absolute path to the user's novel project")
        child.add_argument("--file", required=True, help="Explicit relative .md/.txt file within that project")
        if command == "outline":
            child.add_argument("--chapter", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        root = project_root(args.project)
        path = input_file(root, args.file)
        text = read_bytes(root, path.relative_to(root).as_posix(), MAX_INPUT_BYTES).decode("utf-8-sig")
        if args.command == "count":
            print(json.dumps(count_text(text), ensure_ascii=False))
        else:
            print(outline_slice(text, args.chapter), end="")
    except (ValueError, OSError, UnicodeError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
