"""Build a local Windows GUI folder with a signed existing CPython runtime.

No downloads, pip, runtime site-packages, private workspace or global changes.
The output must be new; preserve Python/Tcl/Tk notices and per-file hashes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from build_plugin import build

ROOT = Path(__file__).resolve().parents[1]


def package(output: Path):
    if os.name != "nt" or not output.is_absolute() or ".." in output.parts or output.exists() or output.is_relative_to(ROOT):
        raise ValueError("Choose a new absolute Windows output directory outside the source checkout")
    sys.path.insert(0, str(ROOT))
    from gui.service import LocalStore, SafeTree
    with SafeTree(ROOT) as tree:
        local_protection = tree.maybe_read('.local/host-protection.json')
    LocalStore.protection_paths(local_protection)
    runtime = Path(sys.executable).parent
    for p in (output.parent, *output.parent.parents):
        info = p.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked build path rejected")
    output.mkdir()
    def copy(source, target):
        for p in (source, *source.parents):
            info=p.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Linked packaging source rejected")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(source.read_bytes())
    for name in ("pythonw.exe", "python313.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"):
        if (runtime/name).is_file():
            copy(runtime/name,output/"runtime"/name)
    for folder in ("Lib", "DLLs", "tcl"):
        for source in (runtime/folder).rglob("*"):
            relative=source.relative_to(runtime)
            if any(part.lower() in {"site-packages", "__pycache__", "test", "tests", "idlelib", "ensurepip", "venv", "turtledemo"} for part in relative.parts):
                continue
            if source.is_file() and source.suffix not in {".pyc", ".pdb", ".lib"}:
                copy(source,output/"runtime"/relative)
    (output/"runtime/python313._pth").write_text("Lib\nDLLs\n.\n../app\n",encoding="ascii")
    for name in ("app.pyw", "service.py", "deployment.py"):
        copy(ROOT/"gui"/name,output/"app"/name)
    for name in ("workspace_admin.py", "safe_project.py", "secure_read.py"):
        copy(ROOT/"scripts"/name,output/"app"/name)
    for source in (ROOT/"gui/runtime-licenses").iterdir():
        if source.is_file():
            copy(source,output/"Runtime-Licenses"/source.name)
    build(output/"payload/novel-studio")
    copy(ROOT/"LICENSE",output/"LICENSE")
    if local_protection is not None:
        copy(ROOT / '.local/host-protection.json', output / '.local/host-protection.json')
        if (output / '.local/host-protection.json').read_bytes() != local_protection:
            raise ValueError('Local protection configuration changed during the build')
    compiler=Path(os.environ["WINDIR"])/"Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    command=[str(compiler),"/nologo","/target:winexe","/platform:anycpu","/reference:System.Windows.Forms.dll","/out:"+str(output/"NovelStudio.exe"),str(ROOT/"desktop/Launcher.cs")]
    result=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace")
    if result.returncode:
        raise RuntimeError(result.stdout+result.stderr)
    (output/"开始使用.txt").write_text("Novel Studio 图形试用版\n\n双击 NovelStudio.exe → 安装与连接 → 新建作品。\n请保留整个文件夹，不要只复制 exe。\n程序自带 Python/Tk，不需要输入命令或编辑 JSON。\n需要已安装并登录的官方 Antigravity CLI（本机已具备）。\n主笔、责编是本程序的图形窗口，使用官方 CLI 后端，不是原生 App 自动权限导入。\n正式稿须您预览差异并点批准；恢复也须确认。\n不修改全局安全设置，不迁移或删除私人小说，不上传到 Novel Studio 服务器。\n未签名的本地构建；若 Windows 阻止运行，请保留提示，不要关闭防护。\n",encoding="utf-8")
    inventory={p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob("*") if p.is_file()}
    manifest={"format":"novel-studio-gui-bundle-v1","python":sys.version,"runtime_origin":str(runtime),"runtime_notice":"Python Software Foundation License plus incorporated notices; Tcl/Tk license.terms retained. No site-packages bundled.","files":inventory}
    (output/"BUNDLE.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    return {"output":str(output),"files":len(inventory),"bytes":sum(p.stat().st_size for p in output.rglob('*') if p.is_file()),"manifest_sha256":hashlib.sha256((output/"BUNDLE.json").read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,required=True)
    print(json.dumps(package(parser.parse_args().out),ensure_ascii=False,indent=2))
