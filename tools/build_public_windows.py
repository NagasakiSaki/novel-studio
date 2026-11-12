"""Build the Windows x64 download with a verified first-run dependency plan."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import zipfile

from build_workbench import ROOT, package


PYTHON = {
    "Url": "https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip",
    "Sha256": "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38",
    "Size": 11441267,
}
DEPENDENCY_DIRS = {"runtime", "qt", "parsers", "providers"}


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def wheel_plans(manifest):
    qt = json.loads((ROOT / "docs/versions/1.2.4/testing/qt-dependency-audit.json").read_bytes())
    locks = (
        ("qt", "qt-wheels", [(r["selected_wheel"]["filename"], r["selected_wheel"]) for r in qt]),
        ("parsers", "import-wheels", [
            (r["wheel"], r) for r in json.loads((ROOT / "docs/versions/1.2.4/testing/phase1_2_4_1/parser-dependencies.json").read_bytes())["packages"]
        ]),
        ("providers", "provider-wheels", [
            (r["wheel"], r) for r in json.loads((ROOT / "docs/versions/1.2.5/testing/provider-dependency-audit.json").read_bytes())["packages"]
        ]),
    )
    for target, cache, wheels in locks:
        remaining = {name: digest for name, digest in manifest["files"].items() if name.startswith(target + "/")}
        for filename, locked in wheels:
            wheel = ROOT / ".build" / cache / filename
            if sha(wheel) != locked["sha256"]:
                raise ValueError("Runtime wheel checksum changed: " + filename)
            selected = {}
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.namelist():
                    name = target + "/" + member
                    if name in remaining and hashlib.sha256(archive.read(member)).hexdigest() == remaining[name]:
                        selected[name] = member
                        del remaining[name]
            if selected:
                yield {"Url": locked["url"], "Sha256": locked["sha256"], "Size": wheel.stat().st_size, "Files": selected}
        if remaining:
            raise ValueError("Runtime wheel plan is incomplete: " + ", ".join(sorted(remaining)))


def python_plan(archive_path):
    if archive_path.stat().st_size != PYTHON["Size"] or sha(archive_path) != PYTHON["Sha256"]:
        raise ValueError("Unexpected CPython embeddable package")
    with zipfile.ZipFile(archive_path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
    if not {"pythonw.exe", "python.exe", "python313.zip", "python313._pth", "LICENSE.txt"} <= set(names):
        raise ValueError("Incomplete CPython archive")
    if len(names) != len(set(names)) or any(PurePosixPath(name).name != name or ":" in name or "\\" in name for name in names):
        raise ValueError("Unexpected path in CPython archive")
    return {**PYTHON, "Files": {"runtime/" + name: name for name in names if name != "python313._pth"}}


def public_package(output, python_archive):
    if not output.is_absolute() or ".." in output.parts or output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Use a new absolute output directory outside the checkout")
    if (ROOT / ".local/host-protection.json").exists():
        raise ValueError("Public builds require a checkout without a local protection file")
    python = python_plan(python_archive)
    with tempfile.TemporaryDirectory(prefix="novel-studio-release-", dir=output.parent) as temp:
        scratch = Path(temp)
        reference = scratch / "reference"
        package(reference, include_pandoc=False)
        manifest = json.loads((reference / "BUNDLE.json").read_bytes())
        if manifest["local_protection_sha256"] is not None or manifest["local_export_converter"] is not None:
            raise ValueError("Public runtime must omit local configuration and Pandoc")
        plan = [python, *wheel_plans(manifest)]
        output.mkdir()
        for name in manifest["files"]:
            if PurePosixPath(name).parts[0] in DEPENDENCY_DIRS or name in {
                "NovelStudio.exe", "NovelStudio-API-Setup.exe", "api-setup.pyw", "开始使用.txt",
            }:
                continue
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(reference / name, target)
        plan_path = output / "Windows-Runtime.json"
        plan_path.write_bytes(json.dumps(plan, separators=(",", ":")).encode("utf-8"))
        plan_digest = sha(plan_path)
        download_mib = (sum(row["Size"] for row in plan) + 1048575) // 1048576
        constants = scratch / "RuntimePlanDigest.cs"
        constants.write_text('internal static class RuntimePlanDigest {\n'
                             f'    public const string Value = "{plan_digest}";\n'
                             f'    public const int DownloadMiB = {download_mib};\n'
                             '}\n', encoding="ascii")
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        references = ("System.Windows.Forms", "System.Drawing", "System.Net.Http", "System.IO.Compression",
                      "System.IO.Compression.FileSystem", "System.Web.Extensions")
        subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:x64",
                        *("/reference:" + name + ".dll" for name in references),
                        "/win32icon:" + str(output / "assets/novel-studio.ico"),
                        "/out:" + str(output / "NovelStudio.exe"),
                        str(ROOT / "desktop/PublicWindowsLauncher.cs"), str(constants)], check=True)
    (output / "开始使用.txt").write_text(
        "Novel Studio " + manifest["version"] + " · Windows x64\n\n"
        "1. 解压整个 ZIP 到你自己的本机文件夹。\n"
        "2. 双击 NovelStudio.exe。首次启动时确认下载运行组件。\n"
        "3. 安装完成后，打开离线示例或新建作品。再次打开可离线写作。\n\n"
        "支持 Word、PDF、Markdown、TXT、HTML、ODT 导出。EPUB 和 RTF 导出暂缓。\n"
        "官方 AI 服务由作者选择并配置。详见 使用指南.txt。\n", encoding="utf-8-sig")
    for field in ("files", "public_release", "native_GUI_acceptance"):
        del manifest[field]
    manifest.update(distribution="windows-x64-first-run-setup", runtime_plan_sha256=plan_digest,
                    runtime_download_bytes=sum(row["Size"] for row in plan))
    manifest["files"] = {p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob("*")) if p.is_file()}
    (output / "BUNDLE.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--python-archive", required=True, type=Path)
    args = parser.parse_args()
    result = public_package(args.out, args.python_archive)
    print(json.dumps({"source_commit": result["source_commit"], "files": len(result["files"]),
                      "runtime_download_bytes": result["runtime_download_bytes"]}))
