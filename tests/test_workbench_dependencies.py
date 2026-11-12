"""Offline source/notice checks. No package downloads in tests or CI."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DesktopDependencyTests(unittest.TestCase):
    def test_pinned_desktop_lock_and_notices(self):
        lock = (ROOT / "requirements-desktop.txt").read_text(encoding="utf-8")
        audit = json.loads((ROOT / "docs/versions/1.2.4/testing/qt-dependency-audit.json").read_text(encoding="utf-8"))
        self.assertEqual(len(audit), 2)
        for row in audit:
            self.assertIn(row["name"] + "==" + row["version"], lock)
            self.assertIn("--hash=sha256:" + row["selected_wheel"]["sha256"], lock)
        for name in ("qt-source-license-audit.json", "qtbase-notices-audit.json"):
            data = json.loads((ROOT / "docs/versions/1.2.4/testing" / name).read_text(encoding="utf-8"))
            rows = data["files"] if isinstance(data, dict) else data
            for row in rows:
                if "file" not in row:
                    continue
                path = ROOT / row["file"].replace("\\", "/")
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row["sha256"], str(path))


if __name__ == "__main__":
    unittest.main()
