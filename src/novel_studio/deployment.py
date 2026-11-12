"""Visible per-user desktop install using the existing hardened I/O/COM code."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import uuid
from .core import ROOT, CoreError, SafeTree, admin, digest

sys.path.insert(0, str(ROOT))
from gui.deployment import desktop_path, shortcut_bytes


def install(bundle, home, notify=None, desktop=None):
    notify = notify or (lambda event: None)
    bundle, home = Path(bundle), Path(home)
    with SafeTree(bundle) as source:
        manifest = json.loads(source.read("BUNDLE.json"))
    if manifest.get("format") != "novel-studio-desktop-bundle-v1" or "NovelStudio.exe" not in manifest.get("files", {}):
        raise CoreError("BUNDLE_INVALID", "请使用完整的 Novel Studio Desktop 安装文件夹。")
    with SafeTree(home.parent) as parent:
        parent.mkdir(home.name)
    identifier = uuid.uuid4().hex
    with SafeTree(home) as tree:
        tree.mkdir("Apps/" + identifier)
    destination = home / "Apps" / identifier
    with SafeTree(bundle, limit=256 * 1024 * 1024) as source, SafeTree(destination, limit=256 * 1024 * 1024) as target:
        for index, (relative, expected) in enumerate(manifest["files"].items()):
            payload = source.read(relative)
            if digest(payload) != expected:
                raise CoreError("BUNDLE_INVALID", "安装文件校验失败，已停止。")
            target.mkdir(str(Path(relative).parent)) if len(Path(relative).parts) > 1 else None
            target.create(relative, payload)
            if index % 100 == 0:
                notify({"type": "install", "completed": index, "total": len(manifest["files"])})
        target.create("BUNDLE.json", admin.encode(manifest))
        target.mkdir(".novel-studio/Shortcut-Staging")
    payload = shortcut_bytes(destination / "NovelStudio.exe", None, destination / ".novel-studio/Shortcut-Staging")
    # One explicit desktop entry. Do not overwrite any existing user shortcut.
    desktop = Path(desktop) if desktop else desktop_path()
    name = "Novel Studio Desktop.lnk"
    with SafeTree(desktop) as tree:
        if tree.maybe_read(name) is not None:
            # Keep old versions, old apps and modified/user-owned links.
            name = "Novel Studio Desktop " + identifier[:6] + ".lnk"
        tree.create(name, payload)
    return {"installed": str(destination), "shortcut": Path(name).stem, "old_versions_preserved": True}
