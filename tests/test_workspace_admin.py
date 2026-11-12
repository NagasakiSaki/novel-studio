from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import workspace_admin as admin
from safe_project import SafeTree


def inventory(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


class AuthorWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-dual-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "合成工程"
        admin.initialize(self.root, ROOT, apply=True)
        self.target = "Canonical/Final-Manuscript/chapter.md"
        self.source = "Writer-Workspace/Drafts/chapter.md"
        with SafeTree(self.root) as tree:
            tree.create(self.target, "旧定稿。\n".encode())
            tree.create(self.source, "新草稿——只待作者批准。\n".encode())
            tree.create("Canonical/Canon-Characters/people.md", "人物：林舟和周岚。\n".encode())

    def plan(self):
        return admin.promote(self.root, self.source, self.target)

    def test_init_dry_run_no_directory_then_no_overwrite(self):
        other = self.root.parent / "另一本书"
        result = admin.initialize(other, ROOT)
        self.assertFalse(result["applied"])
        self.assertFalse(other.exists())
        before = inventory(self.root)
        with self.assertRaises(ValueError):
            admin.initialize(self.root, ROOT, apply=True)
        self.assertEqual(before, inventory(self.root))

    def test_session_scope_and_no_global_wildcard_allow(self):
        for role, work, snapshot in [("writer", "Writer-Workspace", "Context-Snapshot"), ("editor", "Editor-Workspace", "Manuscript-Snapshot")]:
            settings = json.loads((self.root/f".novel-studio/Sessions/{role}.json").read_text(encoding="utf-8"))
            perms = settings["permissions"]
            self.assertEqual(settings["agentMode"], "accept-edits")
            self.assertEqual(settings["toolPermission"], "request-review")
            self.assertFalse(settings["allowNonWorkspaceAccess"])
            self.assertIn(f"read_file({(self.root/'Canonical').as_posix()})", perms["deny"])
            self.assertIn(f"write_file({(self.root/work/snapshot).as_posix()})", perms["deny"])
            self.assertIn("command(*)", perms["deny"])
            self.assertFalse(any("(*)" in p for p in perms["allow"]))
        with self.assertRaises(ValueError):
            admin.policies(self.root, ROOT, [self.root.parent])

    def test_dry_run_diff_no_writes(self):
        before = inventory(self.root)
        plan = self.plan()
        self.assertIn("-旧定稿", plan["diff"])
        self.assertIn("+新草稿", plan["diff"])
        self.assertEqual(inventory(self.root), before)

    def test_missing_wrong_and_stale_approvals_refused(self):
        before = inventory(self.root)
        for token in [None, "x" * 64]:
            with self.assertRaises(ValueError):
                admin.promote(self.root, self.source, self.target, True, token)
        self.assertEqual(before, inventory(self.root))
        plan = self.plan()
        (self.root/self.source).write_text("新版本\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            admin.promote(self.root, self.source, self.target, True, plan["approval_digest"])
        self.assertEqual((self.root/self.target).read_text(encoding="utf-8"), "旧定稿。\n")
        plan = self.plan()
        (self.root/self.target).write_text("作者刚改了定稿\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            admin.promote(self.root, self.source, self.target, True, plan["approval_digest"])

    def test_approved_promotion_backup_and_approved_restore(self):
        before = (self.root/self.target).read_bytes()
        receipt = admin.promote(self.root, self.source, self.target, True, self.plan()["approval_digest"])
        folder = self.root/"Backups/Transactions"/receipt["transaction_id"]
        self.assertEqual((folder/"before.bin").read_bytes(), before)
        self.assertTrue((folder/"COMMITTED.json").exists())
        self.assertEqual((self.root/self.target).read_bytes(), (self.root/self.source).read_bytes())
        plan = admin.restore(self.root, receipt["transaction_id"])
        with self.assertRaises(ValueError):
            admin.restore(self.root, receipt["transaction_id"], True)
        restored = admin.restore(self.root, receipt["transaction_id"], True, plan["approval_digest"])
        self.assertEqual((self.root/self.target).read_bytes(), before)
        self.assertEqual(restored["parent_transaction"], receipt["transaction_id"])
        self.assertEqual(len(list((self.root/"Backups/Transactions").iterdir())), 2)

    def test_restore_refuses_newer_target(self):
        receipt = admin.promote(self.root, self.source, self.target, True, self.plan()["approval_digest"])
        (self.root/self.target).write_text("稍后独立修订", encoding="utf-8")
        with self.assertRaises(ValueError):
            admin.restore(self.root, receipt["transaction_id"])

    def test_snapshot_stable_and_editor_copy_independent(self):
        before = inventory(self.root)
        dry = admin.context_snapshot(self.root)
        self.assertFalse(dry["applied"])
        self.assertEqual(before, inventory(self.root))
        context = admin.context_snapshot(self.root, True)
        manifest = context["destination"] + "/SNAPSHOT.json"
        result = admin.review_snapshot(self.root, self.source, manifest, True)
        saved = self.root/result["destination"]/"Drafts/chapter.md"
        initial = saved.read_bytes()
        (self.root/self.source).write_text("后续修订", encoding="utf-8")
        self.assertEqual(saved.read_bytes(), initial)
        (self.root/self.target).write_text("作者后续定稿", encoding="utf-8")
        self.assertEqual((self.root/context["destination"]/self.target).read_text(encoding="utf-8"), "旧定稿。\n")
        self.assertTrue((self.root/"Backups/Checkpoints"/result["snapshot_id"]/"receipt.json").exists())

    def test_modified_snapshot_rejected(self):
        context = admin.context_snapshot(self.root, True)
        (self.root/context["destination"]/self.target).write_text("被改过", encoding="utf-8")
        with self.assertRaises(ValueError):
            admin.review_snapshot(self.root, self.source, context["destination"]+"/SNAPSHOT.json", True)

    def test_traversal_proposal_scope_and_reserved_names_refused(self):
        for source, target in [("../outside.md", self.target), (self.source, "Canonical/../outside.md"), (self.source, "Canonical/Canon-Characters/people.md"), (self.source, "Canonical/Final-Manuscript/NUL.md"), (self.source, "Canonical/Final-Manuscript/chapter.md:stream")]:
            with self.assertRaises(ValueError):
                admin.promote(self.root, source, target)
        with self.assertRaises(ValueError):
            admin.initialize(self.root/"nested-plugin", self.root)

    def test_hardlink_input_refused(self):
        hard = self.root/"Writer-Workspace/Drafts/linked.md"
        os.link(self.root/self.source, hard)
        with self.assertRaises(ValueError):
            admin.promote(self.root, hard.relative_to(self.root).as_posix(), self.target)
        hard.unlink()

    def test_symlink_input_refused(self):
        link = self.root/"Writer-Workspace/Drafts/symbolic.md"
        try:
            link.symlink_to(self.root/self.source)
        except OSError as exc:
            self.skipTest("OS does not allow synthetic symlink: " + str(exc))
        with self.assertRaises((OSError, ValueError)):
            admin.promote(self.root, link.relative_to(self.root).as_posix(), self.target)

    def test_lock_and_last_moment_revision_refuse_write(self):
        with SafeTree(self.root) as tree:
            with tree.lock():
                with self.assertRaises(FileExistsError):
                    admin.promote(self.root, self.source, self.target, True, self.plan()["approval_digest"])
        original = SafeTree.publish
        def competing_write(tree, path, data, expected):
            (tree.root/path).write_text("独立并发修改", encoding="utf-8")
            return original(tree, path, data, expected)
        with patch.object(SafeTree, "publish", competing_write):
            with self.assertRaises(ValueError):
                admin.promote(self.root, self.source, self.target, True, self.plan()["approval_digest"])
        self.assertEqual((self.root/self.target).read_text(encoding="utf-8"), "独立并发修改")
        self.assertTrue(list((self.root/"Backups/Transactions").glob("*/PREPARED.json")))

    def test_interruption_after_publish_recoverable_with_new_approval(self):
        original = SafeTree.create
        def interrupt(tree, path, payload):
            if path.endswith("/COMMITTED.json"):
                raise OSError("synthetic receipt interruption")
            return original(tree, path, payload)
        with patch.object(SafeTree, "create", interrupt):
            with self.assertRaises(OSError):
                admin.promote(self.root, self.source, self.target, True, self.plan()["approval_digest"])
        identifier = next((self.root/"Backups/Transactions").iterdir()).name
        plan = admin.restore(self.root, identifier)
        admin.restore(self.root, identifier, True, plan["approval_digest"])
        self.assertEqual((self.root/self.target).read_text(encoding="utf-8"), "旧定稿。\n")

    def test_first_publication_does_not_overwrite_or_automatically_delete(self):
        target = "Canonical/Final-Manuscript/new.md"
        plan = admin.promote(self.root, self.source, target)
        result = admin.promote(self.root, self.source, target, True, plan["approval_digest"])
        with self.assertRaises(ValueError):
            admin.restore(self.root, result["transaction_id"])

    def test_launcher_binds_role_cwd_and_profile_and_rejects_tampering(self):
        cli = self.root.parent/("agy.exe" if os.name == "nt" else "agy")
        cli.write_bytes(b"synthetic placeholder; never executed")
        with patch.object(admin.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertEqual(admin.launch(self.root, "editor", cli), 0)
            args, kwargs = run.call_args
            self.assertIn("novel-editor", args[0])
            self.assertIn(str(self.root/".novel-studio/Sessions/editor.json"), args[0])
            self.assertEqual(kwargs["cwd"], self.root/"Editor-Workspace/Review-Reports")
            self.assertNotIn("shell", kwargs)
            self.assertNotIn("--dangerously-skip-permissions", args[0])
            run.reset_mock()
            settings = self.root/".novel-studio/Sessions/editor.json"
            data = json.loads(settings.read_text(encoding="utf-8"))
            data["permissions"]["deny"] = []
            settings.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                admin.launch(self.root, "editor", cli)
            run.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows junction negative test")
    def test_junction_target_rejected_without_writing_outside(self):
        outside = self.root.parent/"outside"
        outside.mkdir()
        link = self.root/"Canonical/Final-Manuscript/junction"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        if result.returncode:
            self.skipTest("OS does not allow junction creation")
        try:
            with self.assertRaises((ValueError, OSError)):
                admin.promote(self.root, self.source, "Canonical/Final-Manuscript/junction/escaped.md")
            self.assertFalse((outside/"escaped.md").exists())
        finally:
            os.rmdir(link)  # Remove this junction, never recurse into its target.


if __name__ == "__main__":
    unittest.main()
