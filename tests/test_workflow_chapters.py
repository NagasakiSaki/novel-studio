from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from novel_studio.core import Core, CoreError, SafeTree, READ, WRITE, admin
from novel_studio.workflow_scale import chapter_id
from novel_studio.workflow_chapters import formal_target
from test_workflow_planning import fixture


class ChapterAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-accept-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.core = Core.create(Path(self.temp.name), "合成收录", ROOT)
        self.core.workflow.store.mutate("scale_test", lambda s, t: s["scale"].update(total_chapters=2))
        outline = self.core.workflow.planning.save(fixture())
        preview = self.core.workflow.planning.preview(outline["version_id"])
        self.core.author_commit(preview["handle"], True)
        self.service = self.core.workflow.chapters
        self.service.ensure()
        self.ids = [chapter_id(self.core.project_id, 1, n) for n in (1, 2)]
        for n, cid in enumerate(self.ids):
            draft = self.core._artifact("writer", "drafts", f"# 合成第{n+1}章\n周岚递出钟轮，林舟接过。", f"synthetic-chapter-draft-{n}")
            report = self.core._artifact("editor", "reports", "独立合成审稿，作者裁决。", f"synthetic-chapter-review-{n}")
            self.core.lengths.link(cid, draft["document_id"])
            self.core.workflow.store.mutate("test_reviewed", lambda s, t, key=cid, d=draft, r=report: s["chapters"][key].update(draft=d, review=r, next_direction={"plot_goal": "继续"}, status="AUTHOR_APPROVAL_PENDING"))

    def test_preview_is_readonly_explicit_batch_approval_and_duplicate_rejected(self):
        preview = self.service.preview(list(reversed(self.ids)))
        with SafeTree(self.core.project) as tree:
            self.assertIsNone(tree.maybe_read(formal_target(1, 1)))
        self.core.author_commit(preview["handle"], True)
        for n, cid in enumerate(self.ids, 1):
            row = self.core.workflow.store.read()["chapters"][cid]
            self.assertEqual(row["status"], "ARCHIVED")
            with SafeTree(self.core.project) as tree:
                self.assertIn("林舟", tree.read(formal_target(1, n)).decode())
            self.assertEqual(self.core.lengths.plan()["nodes"][cid]["source"]["document_id"], row["canonical"]["document_id"])
        with self.assertRaises(CoreError):
            self.service.preview(self.ids)

    def test_rejection_does_not_publish_and_number_gap_blocks(self):
        with self.assertRaises(CoreError):
            self.service.preview([self.ids[1]])
        preview = self.service.preview([self.ids[0]])
        with self.assertRaises(CoreError):
            self.core.author_commit(preview["handle"], False)
        with SafeTree(self.core.project) as tree:
            self.assertIsNone(tree.maybe_read(formal_target(1, 1)))

    def test_source_changed_after_preview_blocks_entire_batch_before_first_write(self):
        preview = self.service.preview(self.ids)
        chapter = self.core.workflow.store.read()["chapters"][self.ids[1]]
        row = self.core.document(chapter["draft"]["document_id"])
        with SafeTree(self.core.project) as tree:
            tree.publish(row["relative"], b'changed', row["revision"])
        with self.assertRaises(CoreError):
            self.core.author_commit(preview["handle"], True)
        with SafeTree(self.core.project) as tree:
            self.assertIsNone(tree.maybe_read(formal_target(1, 1)))

    def test_new_target_and_unindexed_formal_files_cannot_be_overwritten(self):
        with SafeTree(self.core.project) as tree:
            tree.create("Canonical/Final-Manuscript/001_已有正文.md", "已有正式作品".encode())
        self.assertEqual(len(self.service.unindexed()), 1)
        with self.assertRaises(CoreError):
            self.service.preview([self.ids[0]])
        with SafeTree(self.core.project) as tree:
            self.assertEqual(tree.read("Canonical/Final-Manuscript/001_已有正文.md"), "已有正式作品".encode())

    def test_explicit_numbering_cannot_duplicate_or_traverse(self):
        with self.assertRaises(CoreError):
            self.service.preview(self.ids, {self.ids[0]: (1, 1), self.ids[1]: (1, 1)})
        with self.assertRaises(CoreError):
            formal_target("../", 1)
        preview = self.service.preview(self.ids, {self.ids[0]: (1, 2), self.ids[1]: (1, 1)})
        self.assertIn("2 个", preview["target"])

    def test_crash_after_publication_recovers_receipt_without_rewriting_canonical(self):
        preview = self.service.preview([self.ids[0]])
        original = admin._commit
        def interrupted(tree, bundle, approval, **kwargs):
            original(tree, bundle, approval, **kwargs)
            raise OSError("synthetic interruption after publication before index")
        with patch.object(admin, "_commit", interrupted), self.assertRaises(OSError):
            self.core.author_commit(preview["handle"], True)
        with SafeTree(self.core.project) as tree:
            before = tree.read(formal_target(1, 1))
        result = self.service.recover_batch(preview["batch_id"], confirmed=True)
        self.assertEqual(result["recovered"], [self.ids[0]])
        with SafeTree(self.core.project) as tree:
            self.assertEqual(before, tree.read(formal_target(1, 1)))
        with self.assertRaises(CoreError):
            self.service.preview([self.ids[0]])

    def test_editor_dispatch_has_no_acceptance_or_recovery_route(self):
        cap = self.core.bind("editor", READ | WRITE)
        for name in ("workflow_chapters", "accept_chapter", "recover_batch", "author_commit"):
            with self.assertRaises(CoreError):
                self.core.dispatch(cap, name, {"confirmed": True})

    def test_explicit_revision_and_history_restore_keep_formal_index_consistent(self):
        cid = self.ids[0]
        p = self.service.preview([cid])
        self.core.author_commit(p["handle"], True)
        original = self.core.workflow.store.read()["chapters"][cid]
        before = self.core.document(original["canonical"]["document_id"])["text"]
        revised = self.core._artifact("writer", "drafts", "# 作者修订\n雨停后，林舟把钟轮递还给周岚。", "synthetic-explicit-author-revision")
        self.service.adopt_draft(cid, revised["document_id"])
        self.core.workflow.store.mutate("test_new_review", lambda s, t: s["chapters"][cid].update(review=original["review"], next_direction=original["next_direction"]))
        p = self.service.preview_revision(cid)
        result = self.core.author_commit(p["handle"], True)
        self.assertNotEqual(self.core.document(original["canonical"]["document_id"])["text"], before)
        restore = self.core.author_preview(transaction=result["transaction_id"])
        self.core.author_commit(restore["handle"], True)
        restored = self.core.workflow.store.read()["chapters"][cid]
        row = self.service.checked_document(restored["canonical"], "canonical")
        self.assertEqual(row["text"], before)
        from novel_studio.text_metrics import measure
        self.assertEqual(restored["local_length"]["count"], measure(before)["count"])


if __name__ == "__main__":
    unittest.main()
