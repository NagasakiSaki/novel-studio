"""Real SDK serialization against synthetic in-memory HTTP; no live API claims."""
from contextlib import contextmanager
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from novel_studio.core import Core, CoreError, READ, WRITE
from novel_studio.credentials import WindowsVault, validate_reference
from novel_studio.providers import official_client, NativeTurn, discover, safe_error, RequestBoundary
from novel_studio.prompting import compile_prompt
from novel_studio.api_service import ProviderRegistry, ToolBroker, definitions
from novel_studio.host import ModelService
from gui.service import LocalStore
try:
    import httpx
    import httpx2
    import openai
    import anthropic
    from google import genai
    SDK = True
except ImportError:
    SDK = False


def response(provider, calls=None):
    message = "NOVEL_STUDIO_CONNECTION_OK"
    if provider == "openai":
        return {"id": "resp_synthetic", "object": "response", "status": "completed", "model": "synthetic-model",
                "output": calls if calls is not None else [{"type": "message", "id": "msg_synthetic", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": message, "annotations": []}]}],
                "usage": {"input_tokens": 20, "output_tokens": 4, "total_tokens": 24, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
    if provider == "anthropic":
        return {"id": "msg_synthetic", "type": "message", "role": "assistant", "model": "synthetic-model", "stop_reason": "tool_use" if calls else "end_turn",
                "content": calls if calls is not None else [{"type": "text", "text": message}],
                "usage": {"input_tokens": 20, "output_tokens": 4, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}
    return {"id": "synthetic-interaction", "model": "synthetic-model", "status": "completed", "steps": calls if calls is not None else [{"type": "model_output", "content": [{"type": "text", "text": message}]}],
            "usage": {"total_input_tokens": 20, "total_output_tokens": 4, "total_thought_tokens": 0, "total_cached_tokens": 0, "total_tokens": 24}}


class MemoryVault:
    def __init__(self):
        self.values = {}

    def save(self, provider, secret):
        ref = "NovelStudio:official:" + provider + ":" + hashlib.sha256(secret.encode()).hexdigest()[:32]
        self.values[ref] = secret
        return ref

    def read(self, reference, provider):
        validate_reference(reference, provider)
        return self.values[reference]

    def delete(self, reference):
        self.values.pop(reference, None)


class CredentialTests(unittest.TestCase):
    def test_foreign_credentials_rejected(self):
        for value in ("Chrome:password", "NovelStudio:official:openai:../x", "NovelStudio:*", None):
            with self.assertRaises(CoreError):
                validate_reference(value)
        with self.assertRaises(CoreError):
            validate_reference("NovelStudio:official:openai:" + "a" * 32, "gemini")

    @unittest.skipUnless(os.name == "nt", "Windows Credential Manager only")
    def test_real_windows_roundtrip_synthetic_only(self):
        vault = WindowsVault()
        synthetic = "SYNTHETIC-INVALID-CREDENTIAL-NEVER-A-REAL-API-KEY"
        reference = vault.save("openai", synthetic)
        try:
            self.assertEqual(vault.read(reference, "openai"), synthetic)
        finally:
            vault.delete(reference)
        with self.assertRaises(CoreError):
            vault.read(reference, "openai")


@unittest.skipUnless(SDK, "Optional official SDK developer environment not installed")
class SDKContractTests(unittest.TestCase):
    def test_native_serialization_all_three(self):
        for provider in ("openai", "anthropic", "gemini"):
            with self.subTest(provider=provider):
                module = httpx if provider == "gemini" else httpx2
                seen = []
                def handler(request):
                    seen.append(request)
                    return module.Response(200, json=response(provider))
                compiled = compile_prompt(role="writer", project_id="synthetic", request="固定测试")
                with official_client(provider, "SYNTHETIC-INVALID", transport=module.MockTransport(handler)) as client:
                    turn = NativeTurn(provider, client, compiled, {"model_id": "synthetic-model"}, {"max_output_tokens": 65536})
                    value = turn.request()
                self.assertEqual(value["text"], "NOVEL_STUDIO_CONNECTION_OK")
                self.assertEqual(len(seen), 1)
                body = json.loads(seen[0].content)
                self.assertNotIn("SYNTHETIC-INVALID", seen[0].content.decode())
                self.assertNotIn("previous_interaction_id", body)
                if provider != "anthropic":
                    self.assertIs(body["store"], False)
                if provider == "gemini":
                    self.assertEqual(body["input"][0]["type"], "user_input")

    def test_no_environment_routing_or_raw_debug_logs(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logging.getLogger().addHandler(handler)
        self.addCleanup(lambda: logging.getLogger().removeHandler(handler))
        env = {"HTTP_PROXY": "http://example.invalid", "HTTPS_PROXY": "http://example.invalid", "OPENAI_BASE_URL": "https://example.invalid/",
               "ANTHROPIC_BASE_URL": "https://example.invalid/", "ANTHROPIC_CUSTOM_HEADERS": "x-secret-extra: DO-NOT-TRANSMIT\nauthorization: BAD-AUTH",
               "OPENAI_LOG": "debug", "ANTHROPIC_LOG": "debug", "GOOGLE_GENAI_DEBUG": "1", "GOOGLE_GENAI_CLIENT_MODE": "record", "GOOGLE_GENAI_REPLAYS_DIRECTORY": "DO-NOT-WRITE"}
        with patch.dict(os.environ, env):
            for provider in ("openai", "anthropic", "gemini"):
                module = httpx if provider == "gemini" else httpx2
                def transport(request):
                    self.assertNotIn("example.invalid", str(request.url))
                    self.assertNotIn("x-secret-extra", request.headers)
                    self.assertNotIn("BAD-AUTH", str(request.headers))
                    return module.Response(200, json=response(provider))
                with official_client(provider, "SYNTHETIC-LOG-CANARY", transport=module.MockTransport(transport)) as client:
                    NativeTurn(provider, client, compile_prompt(role="writer", project_id="s", request="PROMPT-CANARY"), {"model_id": "synthetic-model"}, {"max_output_tokens": 65536}).request()
        self.assertNotIn("SYNTHETIC-LOG-CANARY", output.getvalue())
        self.assertNotIn("PROMPT-CANARY", output.getvalue())

    def test_rate_limit_is_not_retried(self):
        for provider in ("openai", "anthropic", "gemini"):
            module = httpx if provider == "gemini" else httpx2
            calls = []
            def transport(request):
                calls.append(1)
                return module.Response(429, json={"error": {"message": "PRIVATE-ERROR-CANARY", "type": "rate_limit_error", "code": 429}})
            with self.assertRaises(CoreError) as error:
                with official_client(provider, "SYNTHETIC-INVALID", transport=module.MockTransport(transport)) as client:
                    NativeTurn(provider, client, compile_prompt(role="writer", project_id="s"), {"model_id": "synthetic-model"}, {"max_output_tokens": 65536}).request()
            self.assertEqual(len(calls), 1)
            self.assertNotIn("PRIVATE-ERROR-CANARY", str(error.exception))

    def test_network_boundary_rejects_uploads_redirects_and_other_hosts(self):
        boundary = RequestBoundary("openai", "SYNTHETIC")
        for url in ("https://example.invalid/v1/responses", "http://api.openai.com/v1/responses", "https://api.openai.com/v1/files", "https://api.openai.com:8443/v1/responses", "https://api.openai.com/v1/responses?api_key=SYNTHETIC"):
            with self.assertRaises(CoreError):
                boundary.request(httpx2.Request("POST", url))
        with self.assertRaises(CoreError):
            boundary.response(httpx2.Response(307, headers={"location": "https://example.invalid"}))

    def test_native_thought_and_function_sequences_preserved(self):
        for provider in ("openai", "anthropic", "gemini"):
            module = httpx if provider == "gemini" else httpx2
            native = ([{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "OPAQUE"}, {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "list_documents", "arguments": "{}"}] if provider == "openai" else
                      [{"type": "thinking", "thinking": "synthetic", "signature": "OPAQUE"}, {"type": "tool_use", "id": "call_1", "name": "list_documents", "input": {}}] if provider == "anthropic" else
                      [{"type": "thought", "signature": "T1BBUVVF"}, {"type": "function_call", "id": "call_1", "name": "list_documents", "arguments": {}}])
            seen = []
            def handler(request):
                seen.append(json.loads(request.content))
                return module.Response(200, json=response(provider, native if len(seen) == 1 else None))
            with official_client(provider, "SYNTHETIC", transport=module.MockTransport(handler)) as client:
                turn = NativeTurn(provider, client, compile_prompt(role="writer", project_id="s", tools=definitions("writer")), {"model_id": "synthetic-model"}, {"max_output_tokens": 65536})
                first = turn.request()
                self.assertEqual(first["calls"][0]["name"], "list_documents")
                turn.results([{"id": "call_1", "result": []}])
                turn.request()
            second = canonical_json = json.dumps(seen[1])
            self.assertIn("OPAQUE" if provider != "gemini" else "T1BBUVVF", second)
            self.assertIn("call_1", second)

    def test_real_sdk_core_broker_draft_then_independent_editor(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            core = Core.create(base, "三家原生协议合成工程", ROOT)
            core.snapshot()
            models = ModelService(core)
            store = LocalStore(base / "state")
            vault = MemoryVault()
            role = "writer"
            @contextmanager
            def factory(provider, secret):
                def transport(request):
                    artifact = {"message": "合成产物", "kind": "drafts" if role == "writer" else "reports", "text": "# 合成正文\n铜钥匙尚未回收。"}
                    calls = [{"type": "function_call", "id": "fc_artifact", "call_id": "call_artifact", "name": "submit_artifact", "arguments": json.dumps(artifact, ensure_ascii=False)}]
                    return httpx2.Response(200, json=response(provider, calls))
                with official_client(provider, secret, transport=httpx2.MockTransport(transport)) as client:
                    yield client
            registry = ProviderRegistry(store, vault, factory)
            models.attach_api(registry)
            registry.save_key("writer", "openai", "SYNTHETIC-SECRET", both=True)
            registry.configure(core, "writer", "openai", {"model_id": "synthetic-model"}, True)
            registry.configure(core, "editor", "openai", {"model_id": "synthetic-model"}, True)
            first = models.run("writer", "写合成草稿")
            core.handoff(first["artifact"]["document_id"])
            role = "editor"
            second = models.run("editor", "只出报告")
            self.assertEqual(second["artifact"]["area"], "reports")
            self.assertNotEqual(first["local_conversation_id"], second["local_conversation_id"])
            self.assertEqual(core.document(first["artifact"]["document_id"])["text"], "# 合成正文\n铜钥匙尚未回收。")
            self.assertNotIn("SYNTHETIC-SECRET", (store.root / "center.json").read_text("utf-8"))
            for path in core.project.rglob("*.json"):
                self.assertNotIn("SYNTHETIC-SECRET", path.read_text("utf-8"))


class BrokerTests(unittest.TestCase):
    def test_editor_cannot_borrow_writer_or_author_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            core = Core.create(Path(folder), "权限合成工程", ROOT)
            cap = core.bind("writer", READ | WRITE)
            draft = core.dispatch(cap, "create_draft", {"text": "合成未改正文"}, "synthetic-draft")
            core.snapshot()
            core.handoff(draft["document_id"])
            broker = ToolBroker(core, "editor")
            for name, args in (("delete_file", {}), ("author_commit", {}), ("command", {}), ("submit_artifact", {"message": "x", "kind": "drafts", "text": "越权"}),
                               ("read_document", {"document_id": draft["document_id"], "revision": draft["revision"]})):
                with self.assertRaises(CoreError):
                    broker.call(name, args)
            self.assertEqual(core.document(draft["document_id"])["text"], "合成未改正文")

    def test_untrusted_project_cannot_authorize_api_or_bind_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            core = Core.create(base, "授权合成工程", ROOT)
            models = ModelService(core)
            registry = ProviderRegistry(LocalStore(base / "state"), MemoryVault())
            models.attach_api(registry)
            registry.save_key("writer", "openai", "SYNTHETIC")
            models.save("writer", provider="openai", api_configs={"openai": {"model_id": "synthetic-model"}})
            with self.assertRaises(CoreError):
                registry.authorize_send(core, "writer", "openai")
            with self.assertRaises(CoreError):
                models.dispatch("writer", "test_connection", {"confirmed": True})
            registry.configure(core, "writer", "openai", {"model_id": "synthetic-model"}, True)
            registry.authorize_send(core, "writer", "openai")
            registry.save_key("writer", "openai", "DIFFERENT-SYNTHETIC")
            with self.assertRaises(CoreError):
                registry.authorize_send(core, "writer", "openai")


if __name__ == "__main__":
    unittest.main()
