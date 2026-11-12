import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_studio.core import Core, CoreError, SafeTree, digest
from novel_studio.workflow_store import alive


class RecoveryTests(unittest.TestCase):
    def test_progress_reader_waits_for_concurrent_checkpoint_publication(self):
        with tempfile.TemporaryDirectory(prefix="novel-checkpoint-concurrent-synthetic-") as directory:
            core = Core.create(Path(directory), "合成状态并发", ROOT)
            store = core.workflow.store
            store.mutate("seed", lambda s, t: s.update(test_value="before"))
            writing, release, reading, completed = (threading.Event() for _ in range(4))
            results, errors = [], []
            def mutate(state, tree):
                state["test_value"] = "after"
                writing.set()
                if not release.wait(5):
                    raise AssertionError("test publication release missing")
            def writer():
                try:
                    store.mutate("concurrent", mutate)
                except Exception as error:
                    errors.append(error)
            def reader():
                reading.set()
                try:
                    results.append(store.read()["test_value"])
                except Exception as error:
                    errors.append(error)
                finally:
                    completed.set()
            threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
            threads[0].start()
            try:
                self.assertTrue(writing.wait(5))
                threads[1].start()
                self.assertTrue(reading.wait(5))
                self.assertFalse(completed.wait(.15), "progress must wait for a stable checkpoint")
            finally:
                release.set()
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(5)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(results, ["after"])

    def test_process_probe_only_checks_liveness(self):
        self.assertIs(alive(os.getpid()), True)
        self.assertIsNone(alive(-1))
        self.assertIsNone(alive("123"))

    def test_explicit_recovery_requires_dead_process_and_exact_workflow_role_lock(self):
        with tempfile.TemporaryDirectory(prefix="novel-recovery-synthetic-") as directory:
            core = Core.create(Path(directory), "合成中断记录", ROOT)
            token = b"99999999:" + b"a" * 32
            core.workflow.store.mutate("test_record_lock", lambda s, t: s["outline_jobs"].update({"b" * 32: {"status": "IN_FLIGHT", "owner_pid": 99999999, "role_lock_sha256": digest(token)}}))
            with SafeTree(core.project) as tree:
                tree.create(".novel-studio/.ns-editor-turn.lock", token)
            with self.assertRaises(CoreError):
                core.workflow.store.recover_locks(False)
            with patch("novel_studio.workflow_store.alive", return_value=True), self.assertRaises(CoreError):
                core.workflow.store.recover_locks(True)
            with patch("novel_studio.workflow_store.alive", return_value=False):
                self.assertEqual(core.workflow.store.recover_locks(True), [".ns-editor-turn.lock"])
            with SafeTree(core.project) as tree:
                self.assertIsNone(tree.maybe_read(".novel-studio/.ns-editor-turn.lock"))
                tree.create(".novel-studio/.ns-writer-turn.lock", b"99999999:" + b"c" * 32)
            with patch("novel_studio.workflow_store.alive", return_value=False), self.assertRaises(CoreError):
                core.workflow.store.recover_locks(True)

    def test_legacy_or_unidentifiable_author_lock_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix="novel-recovery-synthetic-") as directory:
            core = Core.create(Path(directory), "合成旧锁", ROOT)
            with SafeTree(core.project) as tree:
                tree.create(".novel-studio/.ns-author.lock", b"a" * 32)
            with self.assertRaises(CoreError):
                core.workflow.store.recover_locks(True)
            with SafeTree(core.project) as tree:
                self.assertEqual(tree.read(".novel-studio/.ns-author.lock"), b"a" * 32)


if __name__ == "__main__":
    unittest.main()
