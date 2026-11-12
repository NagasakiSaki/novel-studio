"""Author-operated local project setup, immutable snapshots and approved promotion.

Not an Agent tool. No network, subprocess, ACL, accounts or global host settings.
All commands default to dry-run. Apply requires explicit flags; Canonical writes
also require the exact plan digest displayed after the diff.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import difflib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.dont_write_bytecode = True
from safe_project import SafeTree, digest, relative_parts

FORMAT = "novel-studio-dual-workspace-v1"
SNAPSHOT_FILE_LIMIT = 1000  # Includes SNAPSHOT.json; per snapshot, never per archive.
CANON = ("Final-Manuscript", "Canon-Characters", "Canon-Worldbuilding", "Approved-Timeline")
DIRECTORIES = [*("Canonical/" + name for name in CANON), "Writer-Workspace/Context-Snapshot", "Writer-Workspace/Drafts", "Writer-Workspace/Proposed-Changes", "Writer-Workspace/Locked", "Editor-Workspace/Manuscript-Snapshot", "Editor-Workspace/Review-Reports", "Backups/Transactions", "Backups/Checkpoints", ".novel-studio/Sessions"]


def encode(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def validate(tree):
    config = json.loads(tree.read(".novel-studio/project.json"))
    if config.get("format") != FORMAT:
        raise ValueError("Unknown project format")


def policies(project: Path, plugin: Path, private_roots=(), *, verify_plugin=True):
    if not plugin.is_absolute() or ".." in plugin.parts:
        raise ValueError("Plugin path must be absolute without traversal")
    if plugin == project or plugin.is_relative_to(project) or project.is_relative_to(plugin):
        raise ValueError("Plugin and project must be separate")
    # Local project opening can compare saved profiles after an app upgrade
    # has moved the component. Initialization and host launch still verify it.
    if verify_plugin:
        with SafeTree(plugin) as package:
            manifest = json.loads(package.read("plugin.json"))
            if manifest.get("name") != "novel-studio":
                raise ValueError("Not a Novel Studio plugin directory")
    result = {}
    for role in ("writer", "editor"):
        work = project / ("Writer-Workspace" if role == "writer" else "Editor-Workspace")
        other = project / ("Editor-Workspace" if role == "writer" else "Writer-Workspace")
        deny_read = [project / "Canonical", project / "Backups", project / ".novel-studio", other, *map(Path, private_roots)]
        for path in deny_read:
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError("Private deny roots must be absolute")
            if work == path or work.is_relative_to(path) or plugin == path or plugin.is_relative_to(path):
                raise ValueError("Deny root overlaps active workspace or plugin")
        snapshot = work / ("Context-Snapshot" if role == "writer" else "Manuscript-Snapshot")
        outputs = [work / name for name in (("Drafts", "Proposed-Changes") if role == "writer" else ("Review-Reports",))]
        deny = [f"read_file({p.as_posix()})" for p in deny_read]
        deny += [f"write_file({p.as_posix()})" for p in (snapshot, work / "WORKSPACE.md", work / ".agents", work / ".gemini", plugin)]
        if role == "writer":
            deny += [f"write_file({(work/'Locked').as_posix()})"]
        deny += ["command(*)", "read_url(*)", "execute_url(*)", "mcp(*)"]
        if role == "editor":
            deny += [f"write_file({(work/'Review-Reports'/p).as_posix()})" for p in (".agents", ".gemini")]
        result[role] = {"agentMode": "accept-edits", "toolPermission": "request-review", "allowNonWorkspaceAccess": False,
                        "permissions": {"allow": [f"read_file({snapshot.as_posix()})", f"read_file({(work/'WORKSPACE.md').as_posix()})", f"read_file({plugin.as_posix()})", *[f"write_file({p.as_posix()})" for p in outputs]], "deny": deny, "ask": []}}
    return result


def initialize(project: Path, plugin: Path, private_roots=(), apply=False):
    relative_parts(project.name)
    profiles = policies(project, plugin, private_roots)
    result = {"operation": "init", "project": str(project), "directories": DIRECTORIES, "policies": profiles, "applied": False}
    with SafeTree(project.parent) as parent:
        if project.exists() or project.is_symlink():
            raise ValueError("Initialization requires a new project; no merge/overwrite")
        if not apply:
            return result
        parent.mkdir(project.name)
        with SafeTree(project) as tree:
            for name in DIRECTORIES:
                tree.mkdir(name)
            tree.create(".novel-studio/project.json", encode({"format": FORMAT, "project_id": uuid.uuid4().hex, "plugin": str(plugin), "private_roots": list(map(str, private_roots))}))
            for role, config in profiles.items():
                tree.create(f".novel-studio/Sessions/{role}.json", encode(config))
            tree.create("Writer-Workspace/WORKSPACE.md", b"Novel Studio Writer workspace. Read Context-Snapshot; write Drafts and Proposed-Changes only. Canonical changes require the author-operated promotion tool.\n")
            tree.create("Editor-Workspace/WORKSPACE.md", b"Novel Studio Editor workspace. Read Manuscript-Snapshot; create versioned reports in Review-Reports only. Never edit snapshots or the writer's files.\n")
            tree.create("Writer-Workspace/Locked/KEEP.md", b"Author-managed locked file. Do not change or delete.\n")
    return {**result, "applied": True}


def context_snapshot(project: Path, apply=False):
    with SafeTree(project) as tree:
        validate(tree)
        with tree.lock() if apply else _no_lock():
            data = {name: tree.read(name) for name in tree.files("Canonical")}
            return _snapshot(tree, "Writer-Workspace/Context-Snapshot", data, apply)


def _snapshot(tree, folder, data, apply):
    if len(data) >= SNAPSHOT_FILE_LIMIT:
        raise ValueError("Snapshot file limit exceeded")
    if sum(map(len, data.values())) > 64 * 1024 * 1024:
        raise ValueError("Snapshot byte limit exceeded")
    identifier = uuid.uuid4().hex
    manifest = {"format": FORMAT, "snapshot_id": identifier, "files": {name: digest(value) for name, value in data.items()}}
    if apply:
        destination = folder + "/" + identifier
        tree.mkdir(destination)
        for name, payload in data.items():
            relative = destination + "/" + name
            tree.mkdir(str(Path(relative).parent))
            tree.create(relative, payload)
        # Publish manifest last. An incomplete snapshot is not selectable.
        tree.create(destination + "/SNAPSHOT.json", encode(manifest))
    return {**manifest, "destination": folder + "/" + identifier, "applied": apply}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate snapshot manifest key')
        result[key] = value
    return result


def snapshot_manifest(tree, manifest_path):
    """Validate an untrusted v1 manifest without enumerating its archive root."""
    parts = relative_parts(manifest_path)
    prefixes = {('Writer-Workspace', 'Context-Snapshot'): {'Canonical'},
                ('Editor-Workspace', 'Manuscript-Snapshot'): {'Canonical', 'Drafts'}}
    if (len(parts) != 4 or parts[:2] not in prefixes or parts[-1] != 'SNAPSHOT.json'
            or not re.fullmatch(r'[a-f0-9]{32}', parts[2])):
        raise ValueError('Invalid snapshot identity or scope')
    raw = tree.read(Path(*parts).as_posix())
    manifest = json.loads(raw, object_pairs_hook=_unique_object)
    if (not isinstance(manifest, dict) or manifest.get('format') != FORMAT
            or manifest.get('snapshot_id') != parts[2] or not isinstance(manifest.get('files'), dict)):
        raise ValueError('Snapshot manifest identity mismatch')
    if len(manifest['files']) >= SNAPSHOT_FILE_LIMIT:
        raise ValueError('Snapshot file limit exceeded')
    seen = set()
    for name, sha in manifest['files'].items():
        source = relative_parts(name)
        if (len(source) < 2 or source[0] not in prefixes[parts[:2]]
                or Path(*source).as_posix() != name
                or Path(name).suffix.lower() not in {'.md', '.txt', '.json'}
                or (source[0] == 'Drafts' and len(source) != 2)
                or not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha)
                or name.casefold() in seen):
            raise ValueError('Invalid or duplicate snapshot source')
        seen.add(name.casefold())
    return manifest, digest(raw)


def snapshot_payloads(tree, manifest_path, manifest):
    total, result = 0, {}
    for name, expected in manifest['files'].items():
        payload = tree.read((Path(manifest_path).parent / name).as_posix())
        total += len(payload)
        if digest(payload) != expected or total > 64 * 1024 * 1024:
            raise ValueError('Snapshot content changed or byte limit exceeded')
        result[name] = payload
    return result


def review_snapshot(project: Path, draft: str, context: str, apply=False):
    if relative_parts(draft)[:2] != ("Writer-Workspace", "Drafts"):
        raise ValueError("Review source must be a writer draft")
    if relative_parts(context)[:2] != ("Writer-Workspace", "Context-Snapshot") or Path(context).name != "SNAPSHOT.json":
        raise ValueError("Explicit context snapshot manifest required")
    with SafeTree(project) as tree:
        validate(tree)
        with tree.lock() if apply else _no_lock():
            manifest, unused_revision = snapshot_manifest(tree, context)
            data = {"Drafts/" + Path(draft).name: tree.read(draft)}
            data.update(snapshot_payloads(tree, context, manifest))
            result = _snapshot(tree, "Editor-Workspace/Manuscript-Snapshot", data, apply)
            if apply:
                checkpoint = "Backups/Checkpoints/" + result["snapshot_id"]
                tree.mkdir(checkpoint)
                tree.create(checkpoint + "/draft.md", data["Drafts/" + Path(draft).name])
                tree.create(checkpoint + "/receipt.json", encode(result))
            return result


class _no_lock:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False


def canonical_target(target):
    parts = relative_parts(target)
    if len(parts) < 3 or parts[0] != "Canonical" or parts[1] not in CANON or Path(target).suffix.lower() not in {".md", ".txt", ".json"}:
        raise ValueError("Target must be an explicit Canonical text file")


def promotion_plan(tree, source: str, target: str):
    canonical_target(target)
    parts = relative_parts(source)
    required = "Drafts" if relative_parts(target)[1] == "Final-Manuscript" else "Proposed-Changes"
    if len(parts) < 3 or parts[:2] != ("Writer-Workspace", required):
        raise ValueError("Source is outside the approved proposal area")
    return _plan(tree, "promote", source, target, tree.read(source))


def _plan(tree, operation, source, target, payload, parent=None):
    canonical_target(target)
    old = tree.maybe_read(target)
    diff_lines = difflib.unified_diff((old or b"").decode("utf-8").splitlines(keepends=True), payload.decode("utf-8").splitlines(keepends=True), fromfile=target + " (current)", tofile=source + " (proposed)")
    diff = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff_lines)
    plan = {"format": FORMAT, "project": str(tree.root), "operation": operation, "source": source, "target": target, "before_sha256": digest(old) if old is not None else None, "after_sha256": digest(payload), "diff": diff, "parent_transaction": parent}
    plan["approval_digest"] = digest(encode(plan))
    return plan, payload, old


def _commit(tree, bundle, approval, transaction_id=None):
    plan, payload, old = bundle
    if not approval or approval != plan["approval_digest"]:
        raise ValueError("Explicit author approval must match this exact diff and both revisions")
    if old == payload:
        raise ValueError("No change to approve")
    identifier = uuid.uuid4().hex if transaction_id is None else transaction_id
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('Invalid transaction identity')
    backup = "Backups/Transactions/" + identifier
    try:
        with tree.directory(relative_parts(backup)):
            raise ValueError('Transaction identity already exists')
    except FileNotFoundError:
        pass
    tree.mkdir(backup)
    if old is not None:
        tree.create(backup + "/before.bin", old)
    tree.create(backup + "/after.bin", payload)
    tree.create(backup + "/PREPARED.json", encode(plan))
    # Backups and intent are durable before the one-file atomic publication.
    tree.mkdir(str(Path(plan["target"]).parent))
    tree.publish(plan["target"], payload, plan["before_sha256"])
    if digest(tree.read(plan["target"])) != plan["after_sha256"]:
        raise ValueError("Post-publication verification failed; inspect PREPARED and backup")
    receipt = {**plan, "transaction_id": identifier, "status": "COMMITTED"}
    tree.create(backup + "/COMMITTED.json", encode(receipt))
    return receipt


def promote(project: Path, source: str, target: str, apply=False, approval=None):
    with SafeTree(project) as tree:
        validate(tree)
        with tree.lock() if apply else _no_lock():
            bundle = promotion_plan(tree, source, target)
            return _commit(tree, bundle, approval) if apply else bundle[0]


def restore(project: Path, transaction: str, apply=False, approval=None):
    if not re.fullmatch(r"[a-f0-9]{32}", transaction):
        raise ValueError("Exact transaction ID required")
    with SafeTree(project) as tree:
        validate(tree)
        with tree.lock() if apply else _no_lock():
            folder = "Backups/Transactions/" + transaction
            receipt = tree.maybe_read(folder + "/COMMITTED.json")
            original = json.loads(receipt if receipt is not None else tree.read(folder + "/PREPARED.json"))
            if original.get("format") != FORMAT or (receipt is not None and original.get("status") != "COMMITTED"):
                raise ValueError("Invalid transaction")
            target = original["target"]
            canonical_target(target)
            if original["before_sha256"] is None:
                raise ValueError("No previous file; automatic deletion is not supported")
            payload = tree.read(folder + "/before.bin")
            current = digest(tree.read(target))
            if receipt is None and current == original["before_sha256"]:
                raise ValueError("Prepared operation did not change Canonical; no rollback needed")
            if digest(payload) != original["before_sha256"] or current != original["after_sha256"]:
                raise ValueError("Backup corrupt or target has a newer revision")
            bundle = _plan(tree, "restore", folder + "/before.bin", target, payload, transaction)
            return _commit(tree, bundle, approval) if apply else bundle[0]


@contextmanager
def launch_context(project: Path, role: str, cli: Path, conversation=None):
    if role not in {"writer", "editor"} or not cli.is_absolute() or ".." in cli.parts or cli.name.lower() not in {"agy", "agy.exe"} or not cli.is_file():
        raise ValueError("Choose writer/editor and the explicit official agy executable")
    if conversation is not None and not re.fullmatch(r"[a-fA-F0-9-]{36}", conversation):
        raise ValueError("Exact host conversation UUID required")
    with SafeTree(project) as tree:
        validate(tree)
        metadata = json.loads(tree.read(".novel-studio/project.json"))
        expected = policies(project, Path(metadata["plugin"]), metadata["private_roots"])[role]
        relative = f".novel-studio/Sessions/{role}.json"
        if json.loads(tree.read(relative)) != expected:
            raise ValueError("Session profile differs from generated policy; inspect before launch")
        workspace = "Writer-Workspace" if role == "writer" else "Editor-Workspace"
        active = (workspace,) if role == "writer" else (workspace, "Review-Reports")
        with tree.directory(active):
            folders = ("Context-Snapshot", "Drafts", "Proposed-Changes", "Locked") if role == "writer" else ("Manuscript-Snapshot", "Review-Reports")
            with SafeTree(project/workspace) as work:
                for folder in folders:
                    with work.directory((folder,)):
                        pass
                args = [str(cli), "--agent", "novel-" + role, "--mode", "accept-edits", "--settings", str(project/relative)]
                if conversation:
                    args += ["--conversation", conversation]
                # Fixed argv, no shell, no bypass flags, no background service.
                yield args, project/Path(*active)


def launch(project: Path, role: str, cli: Path, conversation=None):
    with launch_context(project, role, cli, conversation) as (args, cwd):
        return subprocess.run(args, cwd=cwd).returncode


def import_material(project: Path, source: Path, target: str, apply=False, approval=None):
    """Explicit author-selected text import; same approval/backup pipeline as promotion."""
    canonical_target(target)
    if not source.is_absolute() or ".." in source.parts:
        raise ValueError("Choose an absolute source file")
    with SafeTree(source.parent) as origin:
        payload = origin.read(source.name)
    with SafeTree(project) as tree:
        validate(tree)
        with tree.lock() if apply else _no_lock():
            bundle = _plan(tree, "import", str(source), target, payload)
            return _commit(tree, bundle, approval) if apply else bundle[0]


def ui_state(project: Path, changes=None):
    """Small author-owned UI metadata; never contains keys or manuscript transcripts."""
    relative = ".novel-studio/GUI-State.json"
    with SafeTree(project) as tree:
        validate(tree)
        with tree.lock() if changes is not None else _no_lock():
            old = tree.maybe_read(relative)
            state = json.loads(old) if old is not None else {}
            if changes is None:
                return state
            if set(changes) - {"context", "review", "sessions", "feedback", "last_draft"}:
                raise ValueError("Unknown UI state field")
            changes = dict(changes)
            if "sessions" in changes:
                changes["sessions"] = {**state.get("sessions", {}), **changes["sessions"]}
            state.update(changes)
            for key, prefix in [("context", ("Writer-Workspace", "Context-Snapshot")), ("review", ("Editor-Workspace", "Manuscript-Snapshot")), ("feedback", ("Writer-Workspace", "Context-Snapshot")), ("last_draft", ("Writer-Workspace", "Drafts"))]:
                if state.get(key) and relative_parts(state[key])[:2] != prefix:
                    raise ValueError("UI selection escaped its permitted source area")
            sessions = state.get("sessions", {})
            if set(sessions) - {"writer", "editor"} or any(not re.fullmatch(r"[a-fA-F0-9-]{36}", value) for value in sessions.values()):
                raise ValueError("Invalid role conversation ID")
            tree.publish(relative, encode(state), digest(old) if old is not None else None)
            return state


def author_feedback(project: Path, report: str, selected_text: str):
    if relative_parts(report)[:2] != ("Editor-Workspace", "Review-Reports") or not selected_text.strip():
        raise ValueError("Choose a report and the author's selected advice")
    with SafeTree(project) as tree:
        validate(tree)
        with tree.lock():
            original = tree.read(report)
            folder = "Writer-Workspace/Context-Snapshot/Author-Feedback/" + uuid.uuid4().hex
            tree.mkdir(folder)
            name = folder + "/作者选定意见.md"
            text = "# 作者选定意见\n\n仅处理以下作者选择，不自动合并其他意见，不授权改 Canonical。\n\n" + selected_text + "\n\n来源报告：" + report + "\n来源 SHA-256：" + digest(original) + "\n"
            tree.create(name, text.encode("utf-8"))
            return name


@contextmanager
def role_turn(project: Path, role: str):
    """Coordinate visible GUI requests, including separate desktop shortcut processes."""
    if role not in {"writer", "editor"}:
        raise ValueError("Unknown role")
    with SafeTree(project) as tree:
        validate(tree)
        name = f".novel-studio/.ns-{role}-turn.lock"
        token = (str(os.getpid())+":"+uuid.uuid4().hex).encode("ascii")
        # Register briefly under the author lock so a full-project backup can
        # hold a consistent idle interval. Release it before running role tools.
        with tree.lock():
            tree.create(name, token)
        try:
            yield
        finally:
            tree._discard_internal(name, digest(token))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("launch", help="Start one official interactive CLI with verified session profile")
    start.add_argument("--project", type=Path, required=True)
    start.add_argument("--role", choices=("writer", "editor"), required=True)
    start.add_argument("--cli", type=Path, required=True)
    start.add_argument("--conversation")
    for name in ("init", "context", "review-snapshot", "promote", "restore"):
        p = sub.add_parser(name)
        p.add_argument("--project", type=Path, required=True)
        mode = p.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")
        if name == "init":
            p.add_argument("--plugin", type=Path, required=True)
            p.add_argument("--deny-private", action="append", type=Path, default=[])
        if name in {"review-snapshot", "promote"}:
            p.add_argument("--source", required=True)
        if name == "review-snapshot":
            p.add_argument("--context", required=True)
        if name == "promote":
            p.add_argument("--target", required=True)
        if name == "restore":
            p.add_argument("--transaction", required=True)
        if name in {"promote", "restore"}:
            p.add_argument("--approve", help="Exact digest from the reviewed dry-run; author-operated only")
            p.add_argument("--json", action="store_true", help="Structured plan instead of human-readable diff")
    args = parser.parse_args()
    try:
        if args.command == "launch":
            return launch(args.project, args.role, args.cli, args.conversation)
        if args.command == "init":
            result = initialize(args.project, args.plugin, args.deny_private, args.apply)
        elif args.command == "context":
            result = context_snapshot(args.project, args.apply)
        elif args.command == "review-snapshot":
            result = review_snapshot(args.project, args.source, args.context, args.apply)
        elif args.command == "promote":
            result = promote(args.project, args.source, args.target, args.apply, args.approve)
        else:
            result = restore(args.project, args.transaction, args.apply, args.approve)
        if "diff" in result and not getattr(args, "json", False):
            print(result["diff"] or "(no textual difference)", end="\n")
            result = {key: value for key, value in result.items() if key != "diff"}
        print(encode(result).decode("utf-8"), end="")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
