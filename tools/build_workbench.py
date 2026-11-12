"""New local portable folder; explicit Qt closure, no user data or downloads."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import sys
import zipfile
from build_plugin import build

ROOT = Path(__file__).resolve().parents[1]
QT_FILES = ("__init__.py", "_config.py", "_git_pyside_version.py", "QtCore.pyd", "QtGui.pyd", "QtWidgets.pyd",
            "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "pyside6.abi3.dll")
VC = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_codecvt_ids.dll", "concrt140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
PARSER_MODULES = {"markdown-it-py": "markdown_it", "mdurl": "mdurl", "pypdf": "pypdf",
                  "python-docx": "docx", "lxml": "lxml", "striprtf": "striprtf", "typing_extensions": "typing_extensions.py"}
PARSER_EXTENSIONS = {".py", ".pyd", ".dll", ".docx", ".xml", ".xsl", ".rng", ".dtd"}
PROVIDER_MODULES = {"annotated_types", "anthropic", "anyio", "certifi", "cffi", "_cffi_backend.cp313-win_amd64.pyd",
    "charset_normalizer", "cryptography", "distro", "docstring_parser", "google", "h11", "httpcore", "httpcore2", "httpx", "httpx2",
    "idna", "jiter", "openai", "opentelemetry", "pyasn1", "pyasn1_modules", "pycparser", "pydantic", "pydantic_core", "requests",
    "sniffio", "tenacity", "truststore", "typing_extensions.py", "typing_inspection", "urllib3", "websockets"}


def source_provenance(root=ROOT):
    """Bind copied inputs to a clean commit, including ignored runtime files.

    Local audit notes may stay untracked. They are never copied into a runtime.
    This records provenance, not a public-distribution or security certification.
    """
    root = Path(root).resolve()
    def git(*args):
        value = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
        if value.returncode:
            raise ValueError('A clean committed source checkout is required for a candidate build')
        return value.stdout.decode('utf-8').strip()
    commit, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
    git('diff', '--quiet', 'HEAD')
    runtime = ('src', 'scripts', 'gui', 'desktop', 'agents', 'skills', 'rules', 'vendor',
               'assets', 'schema', 'third_party', 'templates', 'tools')
    others = git('ls-files', '--others', '-z', '--', *runtime).split('\0')
    if any(n and '__pycache__' not in Path(n).parts and Path(n).suffix != '.pyc' for n in others):
        raise ValueError('Untracked runtime inputs are not allowed in a candidate build')
    inputs = {}
    for name in git('ls-files', '-z').split('\0'):
        if not name:
            continue
        path = root / name
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Linked candidate source input rejected')
            if part == root:
                break
        inputs[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'commit': commit, 'tree': tree, 'inputs': inputs}


def package(output):
    if os.name != "nt" or not output.is_absolute() or ".." in output.parts or output.exists() or output.is_relative_to(ROOT):
        raise ValueError("Build into a NEW Windows folder outside the source checkout")
    version = re.search(r'^__version__ = "([0-9A-Za-z.-]+)"$', (ROOT / "src/novel_studio/__init__.py").read_text(encoding="utf-8"), re.M).group(1)
    core_version = re.search(r'^VERSION = "([0-9A-Za-z.-]+)"$', (ROOT / "src/novel_studio/core.py").read_text(encoding="utf-8"), re.M).group(1)
    if version != core_version:
        raise ValueError("Desktop runtime versions differ; refuse a mislabeled bundle")
    provenance = source_provenance()
    sys.path.insert(0, str(ROOT))
    from gui.service import LocalStore, SafeTree
    with SafeTree(ROOT) as tree:
        local_protection = tree.maybe_read('.local/host-protection.json')
    LocalStore.protection_paths(local_protection)
    local_protection_sha = hashlib.sha256(local_protection).hexdigest() if local_protection is not None else None
    runtime = Path(sys.base_prefix)
    site = ROOT / ".venv-workbench/Lib/site-packages"
    dependency_lock = json.loads((ROOT / "docs/versions/1.2.4/testing/qt-dependency-audit.json").read_text(encoding="utf-8"))
    wheel_payloads = {}
    for dependency in dependency_lock:
        entry = dependency["selected_wheel"]
        wheel = ROOT / ".build/qt-wheels" / entry["filename"]
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Dependency wheel checksum differs from audited official source")
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                if name.startswith(("PySide6/", "shiboken6/")) and not name.endswith("/"):
                    wheel_payloads[name] = hashlib.sha256(archive.read(name)).hexdigest()
    parser_lock = json.loads((ROOT / "docs/versions/1.2.4/testing/phase1_2_4_1/parser-dependencies.json").read_text(encoding="utf-8"))["packages"]
    if {p["name"] for p in parser_lock} != set(PARSER_MODULES):
        raise ValueError("Parser dependency closure must be explicitly reviewed")
    parser_files = []
    for dependency in parser_lock:
        wheel = ROOT / ".build/import-wheels" / dependency["wheel"]
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != dependency["sha256"]:
            raise ValueError("Parser wheel differs from the audited official source")
        for notice in dependency["notices"]:
            if hashlib.sha256((ROOT / notice["file"]).read_bytes()).hexdigest() != notice["sha256"]:
                raise ValueError("Original parser license notice changed")
        module = PARSER_MODULES[dependency["name"]]
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                parts = Path(name).parts
                if not (name == module or name.startswith(module + "/")):
                    continue
                if any(p in {"tests", "test", "__pycache__", "includes"} for p in parts) or Path(name).suffix not in PARSER_EXTENSIONS:
                    continue
                wheel_payloads[name] = hashlib.sha256(archive.read(name)).hexdigest()
                parser_files.append(name)
        if not any(n == module or n.startswith(module + "/") for n in parser_files):
            raise ValueError("Audited parser package has no runtime files")
    provider_lock = json.loads((ROOT / "docs/versions/1.2.5/testing/provider-dependency-audit.json").read_bytes())["packages"]
    provider_files = set()
    for dependency in provider_lock:
        wheel = ROOT / ".build/provider-wheels" / dependency["wheel"]
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != dependency["sha256"]:
            raise ValueError("Provider wheel differs from the audited official source")
        for notice in dependency["notices"]:
            if hashlib.sha256((ROOT / notice["file"]).read_bytes()).hexdigest() != notice["sha256"]:
                raise ValueError("Provider license notice changed")
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                parts = Path(name).parts
                if not parts or name.endswith("/") or any(p in {"tests", "test", "__pycache__"} for p in parts):
                    continue
                if parts[0] not in PROVIDER_MODULES and not parts[0].endswith(".dist-info"):
                    raise ValueError("Unreviewed provider wheel surface: " + name)
                if Path(name).suffix in {".pth", ".exe", ".bat", ".cmd", ".ps1"}:
                    raise ValueError("Provider lifecycle/executable input rejected")
                # Dist-info RECORD is rewritten by pip and contains installation
                # metadata. Keep original wheel metadata, never installed paths.
                if parts[0].endswith(".dist-info"):
                    continue
                wheel_payloads[name] = hashlib.sha256(archive.read(name)).hexdigest()
                provider_files.add(name)
    def verify(path):
        for part in (path, *path.parents):
            if not part.exists():
                continue
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Linked packaging input or output rejected")
    verify(output)
    output.mkdir(parents=True)
    def copy(source, name):
        verify(source)
        if source.is_relative_to(ROOT) and not source.is_relative_to(site):
            expected = provenance['inputs'].get(source.relative_to(ROOT).as_posix())
            if source.relative_to(ROOT).as_posix() == '.local/host-protection.json':
                expected = local_protection_sha
            if expected is None or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise ValueError('Copied runtime source differs from the frozen committed input')
        if source.is_relative_to(site):
            expected = wheel_payloads.get(source.relative_to(site).as_posix())
            if expected is None or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise ValueError("Installed dependency differs from the audited wheel")
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(source.read_bytes())
    for name in ("python.exe", "pythonw.exe", "python313.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"):
        if (runtime / name).is_file():
            copy(runtime / name, "runtime/" + name)
    for folder in ("Lib", "DLLs"):
        for source in (runtime / folder).rglob("*"):
            relative = source.relative_to(runtime)
            if any(part.lower() in {"site-packages", "__pycache__", "test", "tests", "idlelib", "ensurepip", "venv", "turtledemo", "tkinter"} for part in relative.parts):
                continue
            if source.is_file() and source.suffix not in {".pyc", ".pdb", ".lib"} and not source.name.startswith("_tkinter"):
                copy(source, "runtime/" + relative.as_posix())
    (output / "runtime/python313._pth").write_text("Lib\nDLLs\n.\n../src\n../qt\n../parsers\n../providers\n..\n", encoding="ascii")
    for source in (ROOT / "src").rglob("*.py"):
        copy(source, source.relative_to(ROOT).as_posix())
    for name in ("safe_project.py", "secure_read.py", "workspace_admin.py", "novelctl.py"):
        copy(ROOT / "scripts" / name, "scripts/" + name)
    for name in ("service.py", "deployment.py"):
        copy(ROOT / "gui" / name, "gui/" + name)
    copy(ROOT / "desktop/desktop-entry.pyw", "desktop-entry.pyw")
    copy(ROOT / "desktop/api-setup.pyw", "api-setup.pyw")
    for name in ("novel-studio.ico", "novel-studio.svg", "novel-studio.png", 'check-light.png', 'check-dark.png', 'partial-light.png', 'partial-dark.png'):
        copy(ROOT / "assets" / name, "assets/" + name)
    for name in QT_FILES + VC:
        copy(site / "PySide6" / name, "qt/PySide6/" + name)
    for source in (site / "PySide6/support").rglob("*.py"):
        copy(source, "qt/" + source.relative_to(site).as_posix())
    for name in ("qwindows.dll", "qoffscreen.dll", "qminimal.dll"):
        copy(site / "PySide6/plugins/platforms" / name, "qt/PySide6/plugins/platforms/" + name)
    for name in ("__init__.py", "_config.py", "_git_shiboken_module_version.py", "Shiboken.pyd", "shiboken6.abi3.dll") + VC:
        copy(site / "shiboken6" / name, "qt/shiboken6/" + name)
    for name in sorted(parser_files):
        copy(site / name, "parsers/" + name)
    for name in sorted(provider_files):
        copy(site / name, "providers/" + name)
    for dependency in provider_lock:
        with zipfile.ZipFile(ROOT / ".build/provider-wheels" / dependency["wheel"]) as archive:
            for name in archive.namelist():
                parts = Path(name).parts
                if parts and parts[0].endswith(".dist-info") and not name.endswith("/") and parts[-1] != "RECORD":
                    target = output / "providers" / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as stream:
                        stream.write(archive.read(name))
    for source in (ROOT / "third_party").rglob("*"):
        if source.is_file():
            copy(source, source.relative_to(ROOT).as_posix())
    for source in (ROOT / "gui/runtime-licenses").iterdir():
        if source.is_file():
            copy(source, "Runtime-Licenses/" + source.name)
    for name in ("LICENSE", "requirements-desktop.txt", "requirements-import.txt", "requirements-providers.txt"):
        copy(ROOT / name, name)
    # Local non-public candidate only. Independent GPL executable retains its
    # original notices; this is not a public redistribution qualification.
    converter = json.loads((ROOT / "tools/pandoc-lock.json").read_bytes())
    converter_zip = ROOT / ".build/pandoc-3.13-audit" / converter["archive"]
    verify(converter_zip)
    if hashlib.sha256(converter_zip.read_bytes()).hexdigest() != converter["sha256"]:
        raise ValueError("Official converter release archive changed")
    with zipfile.ZipFile(converter_zip) as archive:
        for name, expected in converter["files"].items():
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Converter release member differs from the reviewed input")
            target = output / ("tools/pandoc/pandoc.exe" if name.endswith(".exe") else "Runtime-Licenses/Pandoc/" + Path(name).name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.open("xb").write(data)
    copy(ROOT / "tools/pandoc-lock.json", "Runtime-Licenses/Pandoc/SOURCE.json")
    guide = ROOT / "docs/versions/beginner-workbench/user-guide/FIRST_SESSION.md"
    copy(guide, "使用指南.txt")
    if local_protection is not None:
        copy(ROOT / '.local/host-protection.json', '.local/host-protection.json')
    for source in (ROOT / "schema").glob("*.json"):
        copy(source, "schema/" + source.name)
    build(output / "payload/novel-studio")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    result = subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:anycpu", "/reference:System.Windows.Forms.dll", "/win32icon:" + str(output / "assets/novel-studio.ico"), "/out:" + str(output / "NovelStudio.exe"), str(ROOT / "desktop/WorkbenchLauncher.cs")], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    setup = subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:anycpu", "/reference:System.Windows.Forms.dll", "/win32icon:" + str(output / "assets/novel-studio.ico"), "/out:" + str(output / "NovelStudio-API-Setup.exe"), str(ROOT / "desktop/ApiSetupLauncher.cs")], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if setup.returncode:
        raise RuntimeError("API settings launcher compilation failed")
    (output / "开始使用.txt").write_text(
        f"Novel Studio Desktop · 本地开发预览 / {version}\n\n"
        "保留完整文件夹，双击 NovelStudio.exe。\n"
        "在「作品」页新建作品或打开已有作品。首次使用可以先阅读离线写作示例。\n"
        "输入书名并选择保存位置，随后点击「写草稿」和「新建草稿」。\n"
        "需要 AI 时，在作品页点「首次 AI 协作」，配置两个角色并预览发送范围。\n"
        "详见 使用指南.txt。\n",
        encoding="utf-8",
    )
    files = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob("*") if p.is_file()}
    if source_provenance() != provenance:
        raise ValueError('Source inputs changed during the candidate build; candidate not sealed')
    with SafeTree(ROOT) as tree:
        if tree.maybe_read('.local/host-protection.json') != local_protection:
            raise ValueError('Local protection configuration changed during the build')
    manifest = {"format": "novel-studio-desktop-bundle-v1", "version": version, "qt": "6.12.0", "python": sys.version,
                "source_commit": provenance['commit'], "source_tree": provenance['tree'], "source_inputs": provenance['inputs'],
                "build_platform": sys.platform,
                "import_parsers": [{k: p[k] for k in ("name", "version", "wheel", "sha256")} for p in parser_lock],
                "official_api_dependencies": [{k: p[k] for k in ("name", "version", "wheel", "sha256")} for p in provider_lock],
                "local_export_converter": converter,
                "local_protection_sha256": local_protection_sha,
                "files": files, "native_GUI_acceptance": "NOT TESTED", "public_release": False}
    (output / "BUNDLE.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output": str(output), "files": len(files), "bytes": sum(p.stat().st_size for p in output.rglob("*") if p.is_file())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    print(json.dumps(package(parser.parse_args().out), ensure_ascii=False, indent=2))
