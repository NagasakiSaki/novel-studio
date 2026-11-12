"""Per-user installation and three shortcuts. No registry, services or shell commands."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import uuid

from safe_project import SafeTree, digest, relative_parts
import workspace_admin as admin

MAX_BINARY = 128 * 1024 * 1024


class GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte*8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _method(pointer, index, *args):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *args)(table[index])


def _ok(result):
    if result < 0:
        raise OSError("Windows 快捷方式接口未完成操作："+str(result))


def desktop_path():
    shell = ctypes.OleDLL("shell32")
    ole = ctypes.OleDLL("ole32")
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    path = ctypes.c_wchar_p()
    folder = GUID.parse("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")
    _ok(shell.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(path)))
    try:
        return Path(path.value)
    finally:
        ole.CoTaskMemFree(ctypes.cast(path, ctypes.c_void_p))


def shortcut_bytes(application: Path, role: str | None, staging: Path):
    """Serialize IShellLink inside our new install directory, not an arbitrary desktop file."""
    ole = ctypes.OleDLL("ole32")
    ole.CoInitialize.argtypes = [ctypes.c_void_p]
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    initialized = ole.CoInitialize(None)
    _ok(initialized)
    shell = ctypes.c_void_p()
    persist = ctypes.c_void_p()
    try:
        clsid = GUID.parse("00021401-0000-0000-C000-000000000046")
        iid = GUID.parse("000214F9-0000-0000-C000-000000000046")
        _ok(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(shell)))
        _ok(_method(shell, 20, wintypes.LPCWSTR)(shell, str(application)))
        _ok(_method(shell, 9, wintypes.LPCWSTR)(shell, str(application.parent)))
        _ok(_method(shell, 7, wintypes.LPCWSTR)(shell, "Novel Studio 本地图形化文学创作"))
        _ok(_method(shell, 11, wintypes.LPCWSTR)(shell, "--role "+role if role else ""))
        icon = application.parent / "assets/novel-studio.ico"
        if icon.is_file():
            _ok(_method(shell, 17, wintypes.LPCWSTR, ctypes.c_int)(shell, str(icon), 0))
        iid_file = GUID.parse("0000010B-0000-0000-C000-000000000046")
        _ok(_method(shell, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(shell, ctypes.byref(iid_file), ctypes.byref(persist)))
        name = (role or "center")+".lnk"
        with SafeTree(staging) as tree:
            if tree.maybe_read(name) is not None:
                raise FileExistsError(name)
            _ok(_method(persist, 6, wintypes.LPCWSTR, wintypes.BOOL)(persist, str(staging/name), True))
            return tree.read(name)
    finally:
        if persist:
            _method(persist, 2)(persist)
        if shell:
            _method(shell, 2)(shell)
        if initialized >= 0:
            ole.CoUninitialize()


def install_app(bundle: Path, home: Path, emit):
    if os.name != "nt":
        raise OSError("此安装包面向 Windows")
    with SafeTree(bundle) as source:
        manifest = json.loads(source.read("BUNDLE.json"))
    if manifest.get("format") != "novel-studio-gui-bundle-v1" or "NovelStudio.exe" not in manifest["files"]:
        raise ValueError("图形安装包清单不完整，请重新取得完整安装文件夹。")
    identifier = uuid.uuid4().hex
    with SafeTree(home) as tree:
        tree.mkdir("Apps/"+identifier)
    destination = home/"Apps"/identifier
    with SafeTree(bundle, limit=MAX_BINARY) as source, SafeTree(destination, limit=MAX_BINARY) as target:
        for index, (name, expected) in enumerate(manifest["files"].items()):
            relative_parts(name)
            payload = source.read(name)
            if digest(payload) != expected:
                raise ValueError("安装包文件校验失败："+name)
            if len(Path(name).parts) > 1:
                target.mkdir(str(Path(name).parent))
            target.create(name, payload)
            if index % 100 == 0:
                emit({"action": "复制已校验的本地程序", "completed": index, "total": len(manifest["files"])})
        target.create("BUNDLE.json", admin.encode(manifest))
        target.mkdir(".novel-studio/Shortcut-Staging")
    desktop = desktop_path()
    with SafeTree(home) as tree:
        tree.mkdir("State")
    state_root = home/"State"
    with SafeTree(state_root) as state:
        old = state.maybe_read("shortcuts.json")
        known = json.loads(old) if old else {}
        current = {}
        for role, name in [("writer", "Novel Studio 主笔.lnk"), ("editor", "Novel Studio 责编.lnk"), (None, "Novel Studio 作者控制中心.lnk")]:
            payload = shortcut_bytes(destination/"NovelStudio.exe", role, destination/".novel-studio/Shortcut-Staging")
            with SafeTree(desktop) as links:
                previous = links.maybe_read(name)
                if previous is not None and digest(previous) != known.get(name):
                    emit({"action": "保留已有同名快捷方式", "name": name, "reason": "不是本程序登记的版本或已由用户修改"})
                    continue
                links.publish(name, payload, digest(previous) if previous is not None else None)
                current[name] = digest(payload)
            emit({"action": "桌面入口已创建", "name": name})
        state.publish("shortcuts.json", admin.encode(current), digest(old) if old is not None else None)
    return destination
