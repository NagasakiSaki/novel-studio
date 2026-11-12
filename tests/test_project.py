from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = load("novelctl", ROOT / "scripts/novelctl.py")
gate = load("security_check", ROOT / "scripts/security_check.py")


def inventory(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


class ComponentTests(unittest.TestCase):
    def official_schema(self):
        path = ROOT / "docs/research/antigravity-plugin.schema.json"
        if not path.is_file():
            self.skipTest("Optional pinned upstream schema is not included in the source distribution")
        payload = path.read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), "0eea5e483af6f03fa099f32b065d2924a323f1a616ed0323cfa0b7a74ff9543c")
        return json.loads(payload)

    def test_manifest_official_schema(self):
        schema = self.official_schema()
        import jsonschema  # Development-only dependency; never imported by runtime tools.
        jsonschema.Draft202012Validator.check_schema(schema)
        manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(manifest)
        self.assertNotIn("repository", manifest)  # No fabricated public repository.

    def test_skill_structure_and_names(self):
        paths = sorted((ROOT / "skills").glob("*/SKILL.md"))
        self.assertEqual(len(paths), 4)
        for path in paths:
            header = path.read_text(encoding="utf-8").split("---", 2)[1]
            name = re.search(r"^name: (.+)$", header, re.M).group(1)
            description = re.search(r"^description: (.+)$", header, re.M).group(1)
            self.assertEqual(name, path.parent.name)
            self.assertRegex(name, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
            self.assertTrue(0 < len(description) <= 1024)

    def test_agent_declared_allowlists_match_dual_workspace_roles(self):
        paths = sorted((ROOT / "agents").glob("*.md"))
        self.assertEqual(len(paths), 2)
        for path in paths:
            header = path.read_text(encoding="utf-8").split("---", 2)[1]
            self.assertIn("mainAgent: true", header)
            self.assertIn("subagent: false", header)
            self.assertIn("inheritMcp: false", header)
            self.assertIn("inheritCustomizations: false", header)
            self.assertIn('commandExecutionPolicy: "off"', header)
            expected = {"view_file", "grep_search", "find_by_name", "write_to_file", "finish"}
            if path.stem == "novel-writer":
                expected.add("replace_file_content")
            self.assertEqual(set(re.findall(r"^  - ([a-z_]+)$", header, re.M)), expected)
            for relative in re.findall(r"^  - (skills/[^\s]+)$", header, re.M):
                self.assertTrue((ROOT / relative / "SKILL.md").is_file())

    def test_readonly_session_profile_requires_explicit_host_denies(self):
        # Configuration regression check, not evidence of runtime isolation.
        profile = json.loads((ROOT / "assets/permissions/read-only-session.json").read_text(encoding="utf-8"))
        permissions = profile["permissions"]
        self.assertEqual(permissions["allow"], [])
        self.assertEqual(permissions["ask"], [])
        self.assertTrue({"write_file(*)", "command(*)", "read_url(*)", "execute_url(*)", "mcp(*)"}.issubset(permissions["deny"]))
        self.assertFalse(profile["allowNonWorkspaceAccess"])
        self.assertEqual(profile["toolPermission"], "request-review")

    def test_rules_have_supported_triggers(self):
        for path in (ROOT / "rules").glob("*.md"):
            if path.name == "AGENTS.md":
                continue
            header = path.read_text(encoding="utf-8").split("---", 2)[1]
            self.assertIn("trigger: manual", header)

    def test_upstream_inventory_and_runtime_gate(self):
        self.assertEqual(gate.check_repository(), [])

    def test_licenses_follow_components(self):
        lock = json.loads((ROOT / "integrations.lock.json").read_text(encoding="utf-8"))
        for component in {row["component"] for row in lock["files"]}:
            self.assertIn("MIT License", (ROOT / "vendor" / component / "LICENSE").read_text(encoding="utf-8"))

    def test_schema_rejects_invented_field(self):
        schema = self.official_schema()
        import jsonschema
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(schema).validate({"name": "novel-studio", "modelProxy": True})


class SafetyGateTests(unittest.TestCase):
    def fixture(self, root):
        (root / "vendor").mkdir()
        (root / "scripts").mkdir()
        (root / "agents").mkdir()
        (root / "plugin.json").write_text('{"name":"synthetic-test"}', encoding="utf-8")
        (root / "integrations.lock.json").write_text('{"files":[]}', encoding="utf-8")
        (root / "agents/novel-editor.md").write_bytes((ROOT / "agents/novel-editor.md").read_bytes())

    def test_hidden_service_surface_rejected(self):
        with tempfile.TemporaryDirectory(prefix="novel-gate-synthetic-") as directory:
            root = Path(directory)
            self.fixture(root)
            (root / "hidden-relay.js").write_text("// must not be accepted", encoding="utf-8")
            self.assertTrue(any("service surface" in row for row in gate.check_repository(root)))

    def test_hooks_and_mcp_rejected(self):
        with tempfile.TemporaryDirectory(prefix="novel-gate-synthetic-") as directory:
            root = Path(directory)
            self.fixture(root)
            for name in ("hooks.json", "mcp_config.json"):
                (root / name).write_text("{}", encoding="utf-8")
            findings = gate.check_repository(root)
            self.assertTrue(any("hooks.json" in row for row in findings))
            self.assertTrue(any("mcp_config.json" in row for row in findings))

    def test_upstream_checksum_tampering_rejected(self):
        with tempfile.TemporaryDirectory(prefix="novel-gate-synthetic-") as directory:
            root = Path(directory)
            self.fixture(root)
            row = json.loads((ROOT / "integrations.lock.json").read_text(encoding="utf-8"))["files"][0]
            path = root / row["destination"]
            path.parent.mkdir(parents=True)
            path.write_text("# altered synthetic copy", encoding="utf-8")
            (root / "integrations.lock.json").write_text(json.dumps({"files": [row]}), encoding="utf-8")
            self.assertTrue(any("checksum mismatch" in value for value in gate.check_repository(root)))

    def test_network_and_command_imports_rejected(self):
        for text in ("import requests", "from urllib.request import urlopen", "import socket", "import subprocess", "import keyring"):
            self.assertTrue(gate.scan_python(text, "candidate.py"), text)

    def test_upload_telemetry_dependency_rejected(self):
        for text in ("import sentry_sdk", "import opentelemetry", "import boto3"):
            self.assertTrue(gate.scan_python(text, "candidate.py"), text)

    def test_mutations_and_dynamic_execution_rejected(self):
        for text in ("p.unlink()", "p.write_text('x')", "open('x', 'w')", "open('x', mode=mode)", "eval(source)", "os.system(command)"):
            self.assertTrue(gate.scan_python(text, "candidate.py"), text)

    def test_endpoint_and_credential_rejected(self):
        self.assertTrue(gate.scan_python("endpoint = 'https://example.invalid/relay'", "candidate.py"))
        self.assertTrue(gate.scan_python("key = 'sk-' + 'x' * 24", "candidate.py"))
        self.assertTrue(gate.scan_python("key = 'sk-" + "x" * 24 + "'", "candidate.py"))

    def test_read_only_open_accepted(self):
        self.assertEqual(gate.scan_python("from pathlib import Path\np = Path('chapter.md')\ntext = p.read_text(encoding='utf-8')", "candidate.py"), [])


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="novel-studio-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / "合成小说"
        self.project.mkdir()
        (self.project / "03_草稿箱").mkdir()
        (self.project / "04_正文定稿").mkdir()
        self.chapter = self.project / "03_草稿箱/第001章.md"
        self.chapter.write_text('# 测试标题\n\n她说：“等……”\n灯——灭了。\n', encoding="utf-8")
        (self.project / "04_正文定稿/第001章.md").write_text("已确认正文，请勿改动。", encoding="utf-8")
        (self.project / "卷纲.md").write_bytes((ROOT / "examples/合成示例/02_大纲/卷纲.md").read_bytes())

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/novelctl.py"), *args], capture_output=True, text=True, encoding="utf-8")

    def test_chinese_count_and_no_changes(self):
        before = inventory(self.project)
        result = self.run_cli("count", "--project", str(self.project), "--file", "03_草稿箱/第001章.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["actual"], 14)
        self.assertEqual(before, inventory(self.project))

    def test_count_frontmatter_and_bom(self):
        self.assertEqual(cli.count_text('\ufeff---\ntitle: test\n---\n# 标题\n甲 乙\n')["actual"], 2)

    def test_draft_is_explicit_even_with_final_directory(self):
        file = cli.input_file(cli.project_root(self.project), "03_草稿箱/第001章.md")
        self.assertEqual(file, self.chapter)

    def test_outline_slice_no_other_unit_or_retired_material(self):
        before = inventory(self.project)
        result = self.run_cli("outline", "--project", str(self.project), "--file", "卷纲.md", "--chapter", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("第一段剧情", result.stdout)
        self.assertNotIn("第二段剧情", result.stdout)
        self.assertNotIn("旧草案", result.stdout)
        self.assertEqual(before, inventory(self.project))

    def test_outline_second_range(self):
        text = (self.project / "卷纲.md").read_text(encoding="utf-8")
        self.assertIn("第二段剧情", cli.outline_slice(text, 3))

    def test_missing_or_undeclared_scope_fails_closed(self):
        with self.assertRaises(ValueError):
            cli.outline_slice("## 未声明\n未来秘密\n", 1)
        with self.assertRaises(ValueError):
            cli.outline_slice((self.project / "卷纲.md").read_text(encoding="utf-8"), 99)

    def test_traversal_absolute_and_unsupported_file_rejected(self):
        root = cli.project_root(self.project)
        for value in ("../secret.md", str(self.chapter), "03_草稿箱/../../secret.md"):
            with self.assertRaises(ValueError):
                cli.input_file(root, value)
        (self.project / "settings.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            cli.input_file(root, "settings.json")

    def test_plugin_directory_rejected_as_user_project(self):
        with self.assertRaises(ValueError):
            cli.project_root(ROOT)

    def test_link_escape_rejected(self):
        outside = self.base / "outside.md"
        outside.write_text("synthetic outside data", encoding="utf-8")
        link = self.project / "linked.md"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest("OS does not permit test symlink: " + str(exc))
        with self.assertRaises(ValueError):
            cli.input_file(cli.project_root(self.project), "linked.md")

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_windows_junction_escape_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "synthetic.md").write_text("synthetic data", encoding="utf-8")
        junction = self.project / "linked"
        env = dict(os.environ, NOVEL_TEST_LINK=str(junction), NOVEL_TEST_TARGET=str(outside))
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "New-Item -ItemType Junction -Path $env:NOVEL_TEST_LINK -Target $env:NOVEL_TEST_TARGET -ErrorAction Stop | Out-Null"], env=env, capture_output=True, creationflags=0x08000000)
        self.assertEqual(result.returncode, 0)
        with self.assertRaises(ValueError):
            cli.input_file(cli.project_root(self.project), "linked/synthetic.md")

    def test_no_write_commands(self):
        before = inventory(self.project)
        for command in ("init", "track", "backup", "restore", "finalize"):
            result = self.run_cli(command, "--project", str(self.project))
            self.assertEqual(result.returncode, 2)
        self.assertEqual(before, inventory(self.project))

    def test_vendor_imports_do_not_create_bytecode(self):
        before = inventory(ROOT / "vendor")
        cli.count_text("甲乙")
        cli.outline_slice((self.project / "卷纲.md").read_text(encoding="utf-8"), 1)
        self.assertEqual(before, inventory(ROOT / "vendor"))


if __name__ == "__main__":
    unittest.main()
