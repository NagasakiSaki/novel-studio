"""Build a new, explicit-whitelist install directory; never overwrite or delete."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import stat

ROOT = Path(__file__).resolve().parents[1]
FILES = ("plugin.json", "LICENSE", "integrations.lock.json", "UPSTREAM_SOURCES.md", "THIRD_PARTY_NOTICES.md", "SECURITY.md")
DIRECTORIES = ("agents", "skills", "rules", "scripts", "assets", "vendor")
DOCUMENTS = ("SECURITY_AND_COMPLIANCE.md", "PRIVACY_POLICY.md", "THREAT_MODEL.md")


def build(output: Path) -> dict:
    if not output.is_absolute() or ".." in output.parts:
        raise ValueError("Output must be absolute without traversal")
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(ROOT):
        raise ValueError("Output must be a new directory outside the source checkout")
    sources = [(ROOT / name, name) for name in FILES]
    for folder in DIRECTORIES:
        sources += [(p, p.relative_to(ROOT).as_posix()) for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    sources += [(ROOT / "docs" / name, "docs/" + name) for name in DOCUMENTS]
    sources += [(ROOT / "templates/PLUGIN_README.md", "README.md")]
    sources += [(ROOT / "templates/PLUGIN_SECURITY_AUDIT.md", "docs/SECURITY_AUDIT.md")]
    payloads = []
    for source, relative in sources:
        for part in (source, *source.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Linked build input rejected")
        payloads.append((relative, source.read_bytes()))
    output.mkdir(parents=True, exist_ok=False)
    inventory = {}
    for relative, content in payloads:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(content)
        inventory[relative] = hashlib.sha256(content).hexdigest()
    return {"output": str(output), "files": inventory}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.out), ensure_ascii=False, indent=2))
