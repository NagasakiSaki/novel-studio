from pathlib import Path
import hashlib
import importlib.util
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "tools/build_plugin.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PackageTests(unittest.TestCase):
    def test_runtime_package_excludes_developer_files_and_keeps_upstream_bytes(self):
        with tempfile.TemporaryDirectory(prefix="novel-package-synthetic-") as directory:
            out = Path(directory).resolve() / "package"
            manifest = builder.build(out)
            self.assertFalse((out / "AGENTS.md").exists())
            self.assertFalse((out / "tests").exists())
            self.assertFalse((out / ".git").exists())
            self.assertTrue((out / "rules/AGENTS.md").is_file())
            for relative, digest in manifest["files"].items():
                self.assertEqual(hashlib.sha256((out / relative).read_bytes()).hexdigest(), digest)
                if relative.startswith("vendor/"):
                    self.assertEqual((out / relative).read_bytes(), (ROOT / relative).read_bytes())

    def test_existing_output_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory(prefix="novel-package-synthetic-") as directory:
            out = Path(directory).resolve()
            (out / "sentinel.md").write_text("KEEP", encoding="utf-8")
            with self.assertRaises(ValueError):
                builder.build(out)
            self.assertEqual((out / "sentinel.md").read_text(encoding="utf-8"), "KEEP")

    def test_output_inside_repository_rejected(self):
        with self.assertRaises(ValueError):
            builder.build(ROOT / "forbidden-package")
        self.assertFalse((ROOT / "forbidden-package").exists())
