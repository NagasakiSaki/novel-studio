"""Model/output broker isolation using a fake transport, never a model account."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_studio.core import Core, CoreError, SafeTree, admin, digest, READ, WRITE
from novel_studio.host import ModelService, OfficialCLI
from novel_studio.bridge import rpc


class FakeCLI:
    def __init__(self, path):
        self.path = path
        self.kind = "drafts"
        self.requests = []
        self.cid = str(uuid.uuid4())

    def version(self):
        return "1.5.0"

    def stream(self, args, cwd, prompt, notify=None, evidence=None):
        self.requests.append((args, cwd, prompt))
        if notify:
            notify({"type": "init", "cid": self.cid})
        return {"init": {"model": "synthetic-model"}, "result": {"status": "SUCCESS", "conversation_id": self.cid, "num_turns": 1,
                "structured_output": {"message": "仅为合成传输测试", "kind": self.kind, "text": "# 合成章节\n铜钥匙。"},
                "usage": {"input_tokens": 10, "output_tokens": 5}}, "latency_seconds": 0.01}


class HostAdapterTests(unittest.TestCase):
    def test_version_is_metadata_and_capabilities_decide_unknown_patches(self):
        class VersionOnly(OfficialCLI):
            observed = "1.5.3"
            def command(self, args, cwd=None):
                return self.observed
        cli = VersionOnly()
        self.assertEqual(cli.version(), "1.5.3")
        cli.observed = "1.5.4"
        self.assertEqual(cli.version(), "1.5.4")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.core = Core.create(root, "传输合成工程", ROOT)
        # A harmless nonexecutable test placeholder; fake stream never launches it.
        executable = root / "agy.exe"
        executable.write_bytes(b"synthetic test placeholder")
        self.fake = FakeCLI(executable)
        self.models = ModelService(self.core, self.fake)
        self.models.save("writer", model_id="synthetic-model", effort="high")
        self.core.snapshot()

    def test_fixed_args_deny_all_writes_and_broker(self):
        value = self.models.run("writer", "仅生成合成草稿")
        self.assertEqual(self.core.document(value["artifact"]["document_id"])["text"], "# 合成章节\n铜钥匙。")
        args, cwd, prompt = self.fake.requests[0]
        settings = json.loads(args[args.index("--settings") + 1])
        self.assertIn("write_file(*)", settings["permissions"]["deny"])
        self.assertIn("command(*)", settings["permissions"]["deny"])
        self.assertFalse(any(v.startswith("write_file(") for v in settings["permissions"]["allow"]))
        self.assertEqual(args[args.index("--model") + 1], "synthetic-model")
        self.assertEqual(args[args.index("--effort") + 1], "high")
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertEqual(self.models.preferences("writer")["cid"], self.fake.cid)
        self.assertNotIn("history", self.models.configuration("writer"))

    def test_wrong_role_artifact_cannot_fall_back_to_file_tools(self):
        self.fake.kind = "reports"
        with self.assertRaises(CoreError):
            self.models.run("writer", "合成测试")
        self.assertEqual(self.core.documents("writer"), [])
        self.assertFalse(self.core._active)

    def test_corrupt_snapshot_stops_before_host_call(self):
        state = admin.ui_state(self.core.project)
        with SafeTree(self.core.project) as tree:
            name = state["context"]
            old = tree.read(name)
            tree.publish(name, b"{}", digest(old))
        with self.assertRaises(CoreError):
            self.models.run("writer", "合成测试")
        self.assertEqual(self.fake.requests, [])

    def test_legacy_cid_and_preferences_survive_restart(self):
        old = str(uuid.uuid4())
        admin.ui_state(self.core.project, {"sessions": {"editor": old}})
        self.models.save("editor", model_id="editor-only", effort="low")
        reopened = ModelService(Core(self.core.project), self.fake)
        self.assertEqual(reopened.preferences("editor")["cid"], old)
        self.assertEqual(reopened.preferences("writer")["model_id"], "synthetic-model")
        self.assertEqual(reopened.preferences("editor")["model_id"], "editor-only")

    def test_json_rpc_readonly_transport_and_stable_errors(self):
        cap = self.core.bind("observer")
        result = rpc(self.core, cap, {"jsonrpc": "2.0", "id": 1, "method": "get_current_model"})
        self.assertEqual(result["result"]["data"]["writer"]["model_id"], "synthetic-model")
        denied = rpc(self.core, cap, {"jsonrpc": "2.0", "id": 2, "method": "create_draft", "params": {"params": {"text": "x"}}})
        self.assertEqual(denied["error"]["data"]["error"]["code"], "ROLE_DENIED")


if __name__ == "__main__":
    unittest.main()
