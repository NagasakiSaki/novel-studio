"""Local author-tool I/O. Pins directories; never follows links or deletes user files.

The project lock coordinates these tools, not arbitrary same-user processes. Hash
checks and atomic replace do not constitute OS authorization or multi-file transactions.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import hashlib
import os
from pathlib import Path
import stat
import sys
import uuid

sys.dont_write_bytecode = True
import secure_read

LIMIT = 32 * 1024 * 1024


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def relative_parts(relative: str) -> tuple[str, ...]:
    path = Path(relative)
    if path.is_absolute() or path.drive or not path.parts or ".." in path.parts:
        raise ValueError("An explicit relative path without traversal is required")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}
    for part in path.parts:
        if any(c in part for c in ':*?<>|"\\') or part.endswith((".", " ")) or part.split(".")[0].upper() in reserved:
            raise ValueError("Ambiguous or reserved path component")
    return path.parts


class SafeTree:
    def __init__(self, root: Path, limit: int = LIMIT):
        # Bounded 32 MiB text/history I/O supports full native-model contexts.
        # This is a local memory/file guard, not a provider token limit. Only
        # reviewed callers opt into larger binaries (official converter 239 MiB).
        if not 1 <= limit <= 256 * 1024 * 1024:
            raise ValueError("Invalid bounded I/O limit")
        self.limit = limit
        self.root = Path(root)
        if not self.root.is_absolute() or ".." in self.root.parts:
            raise ValueError("Project root must be absolute without traversal")
        if os.name == "nt" and (self.root.drive.startswith("\\") or not self.root.drive):
            raise ValueError("Local drive required")
        if len(self.root.parts) > 1:
            relative_parts(str(Path(*self.root.parts[1:])))
        self.stack = ExitStack()

    def __enter__(self):
        try:
            if os.name == "nt":
                if secure_read._kernel.GetDriveTypeW(self.root.anchor) not in {2, 3, 6}:
                    raise ValueError("Only local drives supported")
                path = Path(self.root.anchor)
                secure_read._win_open(path, True, self.stack)
                for part in self.root.parts[1:]:
                    path /= part
                    secure_read._win_open(path, True, self.stack)
                self.fd = None
            elif os.name == "posix" and hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd:
                self.fd = os.open(self.root.anchor, self._flags())
                self.stack.callback(os.close, self.fd)
                for part in self.root.parts[1:]:
                    self.fd = os.open(part, self._flags(), dir_fd=self.fd)
                    self.stack.callback(os.close, self.fd)
            else:
                raise OSError("Safe author I/O unavailable on this platform")
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)

    @staticmethod
    def _flags():
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)

    @contextmanager
    def directory(self, parts=(), create=False):
        with ExitStack() as stack:
            path, fd = self.root, self.fd
            for part in parts:
                path /= part
                try:
                    if os.name == "nt":
                        secure_read._win_open(path, True, stack)
                    else:
                        fd = os.open(part, self._flags(), dir_fd=fd)
                        stack.callback(os.close, fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    if os.name == "nt":
                        os.mkdir(path)
                        secure_read._win_open(path, True, stack)
                    else:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                        fd = os.open(part, self._flags(), dir_fd=fd)
                        stack.callback(os.close, fd)
            yield path, fd

    def mkdir(self, relative: str):
        with self.directory(relative_parts(relative), create=True):
            pass

    def read(self, relative: str) -> bytes:
        relative_parts(relative)
        return secure_read.read_bytes(self.root, relative, self.limit)

    def maybe_read(self, relative: str):
        try:
            return self.read(relative)
        except FileNotFoundError:
            return None

    def create(self, relative: str, payload: bytes):
        parts = relative_parts(relative)
        if len(payload) > self.limit:
            raise ValueError("Output too large")
        with self.directory(parts[:-1]) as (parent, fd):
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            handle = os.open(parent / parts[-1], flags, 0o600) if os.name == "nt" else os.open(parts[-1], flags, 0o600, dir_fd=fd)
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())

    def _discard_internal(self, relative: str, expected: str):
        parts = relative_parts(relative)
        if not parts[-1].startswith(".ns-") or digest(self.read(relative)) != expected:
            raise ValueError("Refuse cleanup of unowned internal file")
        with self.directory(parts[:-1]) as (parent, fd):
            if os.name == "nt":
                os.unlink(parent / parts[-1])
            else:
                os.unlink(parts[-1], dir_fd=fd)

    def publish(self, relative: str, payload: bytes, expected: str | None, *, verify_staged=None):
        parts = relative_parts(relative)
        stage = str(Path(*parts[:-1]) / (".ns-" + uuid.uuid4().hex + ".tmp"))
        with self.directory(parts[:-1]) as (parent, fd):
            current = self.maybe_read(relative)
            if (digest(current) if current is not None else None) != expected:
                raise ValueError("Target revision changed")
            self.create(stage, payload)
            if verify_staged is not None:
                verify_staged(self.read(stage))
            current = self.maybe_read(relative)
            if (digest(current) if current is not None else None) != expected:
                raise ValueError("Target revision changed; staged file retained for inspection")
            stage_name = Path(stage).name
            if expected is None:
                # No-clobber publication. Windows rename fails if the name exists.
                if os.name == "nt":
                    os.rename(parent / stage_name, parent / parts[-1])
                else:
                    os.link(stage_name, parts[-1], src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                    first = os.stat(stage_name, dir_fd=fd, follow_symlinks=False)
                    second = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                    if (first.st_dev, first.st_ino) != (second.st_dev, second.st_ino):
                        raise ValueError("Publication identity changed; inspect staged file")
                    os.unlink(stage_name, dir_fd=fd)
            elif os.name == "nt":
                os.replace(parent / stage_name, parent / parts[-1])
            else:
                os.replace(stage_name, parts[-1], src_dir_fd=fd, dst_dir_fd=fd)

    @contextmanager
    def lock(self):
        name = ".novel-studio/.ns-author.lock"
        token = (str(os.getpid()) + ":" + uuid.uuid4().hex).encode("ascii")
        self.create(name, token)  # EXCL: no automatic stale-lock removal.
        try:
            yield
        finally:
            self._discard_internal(name, digest(token))

    def files(self, relative: str | None, *, file_limit=1000, include_directories=False) -> list[str]:
        if type(file_limit) is not int or not 1 <= file_limit <= 12000:
            raise ValueError('Invalid file inventory limit')
        result = []

        def walk(parts):
            with self.directory(parts) as (path, unused):
                with os.scandir(path) as entries:
                    names = sorted(entry.name for entry in entries)
                for name in names:
                    relative_parts(name)
                    info = (path / name).lstat()
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        raise ValueError("Linked snapshot input rejected")
                    child = (*parts, name)
                    if stat.S_ISDIR(info.st_mode):
                        if include_directories:
                            result.append(Path(*child).as_posix() + '/')
                            if len(result) > file_limit:
                                raise ValueError("Snapshot file limit exceeded")
                        walk(child)
                    elif stat.S_ISREG(info.st_mode):
                        result.append(Path(*child).as_posix())
                    else:
                        raise ValueError("Non-regular snapshot input")
                    if len(result) > file_limit:
                        raise ValueError("Snapshot file limit exceeded")

        walk(relative_parts(relative) if relative is not None else ())
        return result

    def children(self, relative: str):
        """Stream one directory without sorting/materializing the entire archive.

        The caller bounds each page and closes the iterator on expiry. Directory
        handles remain pinned while suspended; no descendant is enumerated here.
        """
        with self.directory(relative_parts(relative)) as (path, unused):
            with os.scandir(path) as entries:
                for entry in entries:
                    relative_parts(entry.name)
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                        raise ValueError('Linked snapshot input rejected')
                    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                        raise ValueError('Non-regular snapshot input')
                    yield entry.name, stat.S_ISDIR(info.st_mode)
