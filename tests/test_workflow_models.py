from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT)]
from novel_studio.core import Core, CoreError, SafeTree
from novel_studio.host import ModelService, TASK_ROLES
from novel_studio.api_service import ProviderRegistry
from novel_studio.providers import official_client, parameter_config
from gui.service import LocalStore
from test_official_providers import MemoryVault, response, SDK
from test_workflow_planning import fixture


class WorkflowModelTests(unittest.TestCase):
    def test_fixed_role_registry_has_no_caller_supplied_role_escalation(self):
        self.assertEqual(TASK_ROLES["outline_plan"], "editor")
        self.assertEqual(TASK_ROLES["workflow_write"], "writer")
        self.assertEqual(TASK_ROLES["workflow_review"], "editor")
        with tempfile.TemporaryDirectory() as directory:
            models = ModelService(Core.create(Path(directory), "合成固定用途", ROOT))
            with self.assertRaises(CoreError):
                models.structured_task("author_commit", "x", {}, {})

    def test_unsupported_effort_and_output_limits_never_silently_fall_back(self):
        with self.assertRaises(CoreError):
            parameter_config("openai", {"model_id": "synthetic", "effort": "max"}, {})
        with self.assertRaises(CoreError):
            parameter_config("anthropic", {"model_id": "synthetic", "max_output_tokens": 1024}, {"max_output_tokens": 512})

    @unittest.skipUnless(SDK, "Official SDK test environment unavailable")
    def test_three_real_sdk_mock_outlines_use_editor_api_and_keep_writer_cli(self):
        import httpx
        import httpx2
        for provider in ("openai", "anthropic", "gemini"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory(prefix="novel-workflow-sdk-synthetic-") as directory:
                base = Path(directory)
                core = Core.create(base, "合成独立模型", ROOT)
                with SafeTree(core.project) as tree:
                    tree.create("Canonical/Canon-Worldbuilding/资料.md", "雾港的两个人物修理钟楼，蓝信的知情有边界。".encode())
                models = ModelService(core)
                models.save("writer", provider="antigravity", model_id="writer-cli-synthetic", effort="low")
                module = httpx if provider == "gemini" else httpx2
                seen = []
                @contextmanager
                def factory(selected_provider, secret):
                    self.assertEqual(selected_provider, provider)
                    def handler(request):
                        seen.append(json.loads(request.content))
                        output = fixture()
                        native = ([{"type": "function_call", "id": "fc_plan", "call_id": "call_plan", "name": "deliver_outline_plan", "arguments": json.dumps(output)}] if provider == "openai" else
                                  [{"type": "tool_use", "id": "call_plan", "name": "deliver_outline_plan", "input": output}] if provider == "anthropic" else
                                  [{"type": "function_call", "id": "call_plan", "name": "deliver_outline_plan", "arguments": output}])
                        return module.Response(200, json=response(provider, native))
                    with official_client(provider, secret, transport=module.MockTransport(handler)) as client:
                        yield client
                registry = ProviderRegistry(LocalStore(base / "state"), MemoryVault(), factory)
                models.attach_api(registry)
                registry.save_key("editor", provider, "SYNTHETIC-WORKFLOW-NOT-A-REAL-KEY")
                registry.configure(core, "editor", provider, {"model_id": "synthetic-model", "max_output_tokens": 1024}, True)
                plan = core.workflow.planning.generate("依据合成设定规划")
                self.assertEqual(plan["model"]["provider"], provider)
                self.assertEqual(len(seen), 1)
                self.assertIn("deliver_outline_plan", json.dumps(seen))
                self.assertEqual(models.selected("writer")["model_id"], "writer-cli-synthetic")
                self.assertEqual(models.selected("writer")["provider"], "antigravity")
                self.assertIsNone(core.workflow.store.read()["approved_outline"])


if __name__ == "__main__":
    unittest.main()
