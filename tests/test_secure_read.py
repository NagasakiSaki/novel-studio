"""Synthetic filesystem tests; no manuscripts, accounts or host model calls."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import secure_read


class SecureReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-safe-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "合成工程"
        self.root.mkdir()
        (self.root / "草稿").mkdir()
        self.file = self.root / "草稿/正文.md"
        self.file.write_text("甲乙——……", encoding="utf-8")
        self.outside = self.base / "outside"
        self.outside.mkdir()
        (self.outside / "正文.md").write_text("OUTSIDE-SYNTHETIC", encoding="utf-8")

    def read(self, relative="草稿/正文.md", limit=1024):
        return secure_read.read_bytes(self.root, relative, limit)

    def symlink_case(self):
        link = self.root / "linked.md"
        try:
            link.symlink_to(self.outside / "正文.md")
        except OSError:
            self.skipTest("OS permission does not allow symlink creation")
        with self.assertRaises((OSError, ValueError)):
            self.read("linked.md")

    @unittest.skipUnless(sys.platform == "win32", "Windows-only symlink test")
    def test_windows_symlink_rejected_at_open(self):
        self.symlink_case()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux-only symlink test")
    def test_linux_symlink_rejected_at_open(self):
        self.symlink_case()

    @unittest.skipUnless(sys.platform == "darwin", "macOS-only symlink test")
    def test_macos_symlink_rejected_at_open(self):
        self.symlink_case()

    def test_chinese_regular_file(self):
        self.assertEqual(self.read().decode("utf-8"), "甲乙——……")

    def test_size_limit_is_enforced_on_handle(self):
        with self.assertRaises(ValueError):
            self.read(limit=2)

    def test_absolute_and_traversal_rejected(self):
        for relative in ("../outside/正文.md", str(self.file), "草稿/../../outside/正文.md"):
            with self.assertRaises(ValueError):
                self.read(relative)

    def test_hardlink_rejected(self):
        link = self.root / "hardlinked.md"
        try:
            os.link(self.outside / "正文.md", link)
        except OSError:
            self.skipTest("Filesystem does not support test hardlink")
        with self.assertRaises(ValueError):
            self.read("hardlinked.md")

    @unittest.skipUnless(os.name == "nt", "Windows-only junction race test")
    def test_windows_junction_inserted_after_precheck_rejected(self):
        # Simulate replacement after an earlier name-based precheck returned.
        link = self.root / "草稿"
        self.assertTrue(self.file.is_file())
        previous = self.root / "original-parent"
        self.assertTrue(previous.resolve().is_relative_to(self.base))
        self.assertTrue(link.resolve().is_relative_to(self.base))
        link.rename(previous)
        environment = dict(os.environ, NOVEL_TEST_LINK=str(link), NOVEL_TEST_TARGET=str(self.outside))
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "New-Item -ItemType Junction -Path $env:NOVEL_TEST_LINK -Target $env:NOVEL_TEST_TARGET -ErrorAction Stop | Out-Null"], env=environment, capture_output=True, creationflags=0x08000000)
        self.assertEqual(result.returncode, 0)
        with self.assertRaises((OSError, ValueError)):
            self.read("草稿/正文.md")

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_windows_replacement_blocked_while_read_handle_open(self):
        substitute = self.root / "replacement.md"
        substitute.write_text("NEW-SYNTHETIC", encoding="utf-8")
        original = secure_read._collect

        def attempt(read, limit):
            with self.assertRaises(PermissionError):
                os.replace(substitute, self.file)
            return original(read, limit)

        with patch.object(secure_read, "_collect", side_effect=attempt):
            self.assertEqual(self.read().decode("utf-8"), "甲乙——……")

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_windows_concurrent_writer_blocked(self):
        original = secure_read._collect

        def attempt(read, limit):
            with self.assertRaises(PermissionError):
                self.file.write_text("CHANGED", encoding="utf-8")
            return original(read, limit)

        with patch.object(secure_read, "_collect", side_effect=attempt):
            self.assertEqual(self.read().decode("utf-8"), "甲乙——……")

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor semantics")
    def test_posix_replacement_detected(self):
        substitute = self.root / "replacement.md"
        substitute.write_text("NEW-SYNTHETIC", encoding="utf-8")
        original = secure_read._collect

        def attempt(read, limit):
            os.replace(substitute, self.file)
            return original(read, limit)

        with patch.object(secure_read, "_collect", side_effect=attempt):
            with self.assertRaises(ValueError):
                self.read()

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor semantics")
    def test_posix_parent_swap_does_not_follow_new_symlink(self):
        original = secure_read._collect

        def attempt(read, limit):
            (self.root / "草稿").rename(self.root / "pinned-old")
            (self.root / "草稿").symlink_to(self.outside, target_is_directory=True)
            return original(read, limit)

        with patch.object(secure_read, "_collect", side_effect=attempt):
            self.assertEqual(self.read().decode("utf-8"), "甲乙——……")

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor semantics")
    def test_posix_in_place_mutation_detected(self):
        original = secure_read._collect

        def attempt(read, limit):
            payload = original(read, limit)
            self.file.write_text("CHANGED-SYNTHETIC-LONGER", encoding="utf-8")
            return payload

        with patch.object(secure_read, "_collect", side_effect=attempt):
            with self.assertRaises(ValueError):
                self.read()

    @unittest.skipUnless(os.name == "posix", "POSIX filesystem node test")
    def test_fifo_rejected_without_blocking(self):
        os.mkfifo(self.root / "pipe.md")
        with self.assertRaises(ValueError):
            self.read("pipe.md")

    @unittest.skipUnless(os.name == "nt", "Windows paths")
    def test_windows_stream_and_ambiguous_names_rejected(self):
        for relative in ("草稿/正文.md:stream", "草稿/正文.md.", "草稿/正文.md "):
            with self.assertRaises(ValueError):
                self.read(relative)


class VerifiedSourceTests(unittest.TestCase):
    def test_verified_payload_is_executed_without_loader_reopen(self):
        import novelctl
        real_read = novelctl.read_bytes
        reads = []

        def capture(root, relative, limit):
            reads.append(relative)
            return real_read(root, relative, limit)

        with patch.object(novelctl, "read_bytes", side_effect=capture):
            module = novelctl.load_upstream("wordcount_core.py")
        self.assertEqual(module.count_visible_chars("甲乙"), 2)
        self.assertEqual(reads, ["integrations.lock.json", "vendor/oh-story/skills/story-long-write/scripts/wordcount_core.py"])
