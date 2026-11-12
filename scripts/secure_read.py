"""Bounded handle-based reads. No writes, network access or privilege changes."""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import stat


def _parts(root: Path, relative: str) -> tuple[Path, tuple[str, ...]]:
    root = Path(root)
    child = Path(relative)
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("Read root must be absolute without traversal")
    if child.is_absolute() or child.drive or ".." in child.parts or not child.parts:
        raise ValueError("Read file must be relative without traversal")
    if os.name == "nt":
        if root.drive.startswith("\\") or not root.drive or root.drive.startswith("\\\\?\\"):
            raise ValueError("Only local drive paths are supported")
        for part in (*root.parts[1:], *child.parts):
            if ":" in part or part.endswith((".", " ")):
                raise ValueError("Windows streams and ambiguous path components are rejected")
    return root, child.parts


def _collect(read, limit: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = read(min(65536, limit + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise ValueError("Input exceeds the configured read limit")


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _posix_read(root: Path, parts: tuple[str, ...], limit: int) -> bytes:
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise OSError("This platform cannot provide descriptor-relative no-follow reads")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    with ExitStack() as stack:
        descriptor = os.open(root.anchor, directory_flags)
        stack.callback(os.close, descriptor)
        for part in (*root.parts[1:], *parts[:-1]):
            descriptor = os.open(part, directory_flags, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
        parent = descriptor
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0), dir_fd=parent)
        stack.callback(os.close, descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Input must be a regular file")
        if before.st_nlink != 1:
            raise ValueError("Hard-linked inputs are not supported")
        if before.st_size > limit:
            raise ValueError("Input exceeds the configured read limit")
        payload = _collect(lambda size: os.read(descriptor, size), limit)
        after = os.fstat(descriptor)
        named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if _stamp(before) != _stamp(after) or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Input changed or was replaced during reading; retry")
        return payload


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _FileInfo(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("creation", wintypes.FILETIME),
                    ("access", wintypes.FILETIME), ("modified", wintypes.FILETIME),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL
    _kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInfo)]
    _kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    _kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    _kernel.GetDriveTypeW.restype = wintypes.UINT
    _kernel.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    _kernel.ReadFile.restype = wintypes.BOOL

    def _win_info(handle):
        info = _FileInfo()
        if not _kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400:
            raise ValueError("Reparse points are rejected by the opened handle")
        return info

    def _win_path(handle):
        length = _kernel.GetFinalPathNameByHandleW(handle, None, 0, 0)
        if not length:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(length + 1)
        result = _kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not result or result >= len(buffer):
            raise OSError("Cannot verify opened file path")
        value = buffer.value
        if value.startswith("\\\\?\\"):
            value = value[4:]
        return os.path.normcase(os.path.normpath(value))

    def _win_open(path, directory, stack):
        # OPEN_EXISTING only. No write/delete access; no FILE_SHARE_DELETE.
        # The final file additionally denies sharing with writers while reading.
        handle = _kernel.CreateFileW("\\\\?\\" + str(path), 0x80 if directory else 0x80000000,
                                     3 if directory else 1, None, 3, 0x00200000 | (0x02000000 if directory else 0), None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        stack.callback(_kernel.CloseHandle, handle)
        info = _win_info(handle)
        if bool(info.attributes & 0x10) != directory:
            raise ValueError("Unexpected file type at opened handle")
        if _win_path(handle) != os.path.normcase(os.path.normpath(str(path))):
            raise ValueError("Opened handle does not match the specified path")
        return handle, info

    def _windows_read(root: Path, parts: tuple[str, ...], limit: int) -> bytes:
        if _kernel.GetDriveTypeW(root.anchor) not in {2, 3, 6}:
            raise ValueError("Only local removable, fixed or RAM drives are supported")
        with ExitStack() as stack:
            path = Path(root.anchor)
            _win_open(path, True, stack)
            for part in (*root.parts[1:], *parts[:-1]):
                path = path / part
                _win_open(path, True, stack)
            path = path / parts[-1]
            handle, before = _win_open(path, False, stack)
            if before.links != 1:
                raise ValueError("Hard-linked inputs are not supported")
            if (before.size_high << 32) + before.size_low > limit:
                raise ValueError("Input exceeds the configured read limit")

            def read(size):
                buffer = ctypes.create_string_buffer(size)
                actual = wintypes.DWORD()
                if not _kernel.ReadFile(handle, buffer, size, ctypes.byref(actual), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                return buffer.raw[:actual.value]

            payload = _collect(read, limit)
            after = _win_info(handle)
            stamp = lambda info: (info.volume, info.index_high, info.index_low, info.size_high, info.size_low,
                                  info.modified.dwHighDateTime, info.modified.dwLowDateTime)
            if stamp(before) != stamp(after):
                raise ValueError("Input changed during reading; retry")
            return payload


def read_bytes(root: Path, relative: str, limit: int) -> bytes:
    """Read from pinned handles; reject linked components and observed mutation."""
    if limit < 1:
        raise ValueError("Read limit must be positive")
    root, parts = _parts(root, relative)
    if os.name == "nt":
        return _windows_read(root, parts, limit)
    if os.name == "posix":
        return _posix_read(root, parts, limit)
    raise OSError("Unsupported safe-read platform")
