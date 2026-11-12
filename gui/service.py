"""Author GUI services: existing safe project operations and official CLI only."""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import uuid

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if SCRIPTS.is_dir():
    sys.path.insert(0, str(SCRIPTS))
import workspace_admin as admin
from safe_project import SafeTree, digest, relative_parts


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def friendly_error(error):
    text = str(error)
    cases = [
        ("approval", "预览之后文件或版本已变化，尚未保存。请重新预览差异，再确认。"),
        ("revision", "文件刚被其他操作修改，尚未覆盖。请重新预览后再试。"),
        ("newer revision", "正式文件已有更新版本，不能直接覆盖。请先检查当前内容。"),
        ("backup corrupt", "备份校验不一致，已拒绝恢复。请保留现有文件并检查备份。"),
        ("No previous file", "这是首次创建的文件，没有更早版本；程序不会自动删除正式文件。"),
        ("requires a new project", "这个文件夹已经存在。请换一个作品名称或选择“打开已有作品”。"),
        ("No change", "两个版本相同，不需要保存。"),
        ("profile differs", "本书权限配置已被改动。为保护作品，暂不启动；请检查安装和项目配置。"),
        ("Invalid role conversation", "保存的会话编号无效，请在连接详情中重置该角色会话。"),
        ("limit", "文件或资料总量超过当前安全上限，请分批处理较小的文本文件。"),
        ("linked", "所选路径包含链接或共享引用。请使用实际文件副本，程序没有改动作品。"),
        ("Reparse", "所选路径包含目录链接。请改用普通本地文件夹。"),
        ("traversal", "文件位置超出本次允许范围，已停止操作。"),
        ("outside", "文件不在这项操作允许的位置，已停止操作。"),
    ]
    for fragment, message in cases:
        if fragment.lower() in text.lower():
            return message
    if isinstance(error, FileExistsError):
        return "文件或操作锁已经存在，未覆盖。若另一个操作正在进行，请等它完成；不要直接删除锁文件。"
    if isinstance(error, FileNotFoundError):
        return "所选文件或连接程序已不存在。请重新选择或检查安装。"
    if isinstance(error, PermissionError):
        return "Windows 拒绝了文件访问，未覆盖作品。请检查文件是否正被其他程序占用。"
    return "操作未完成，作品没有被程序擅自批准。可在“执行详情”查看原因：" + text[:300]


class ConfigStartupCancelled(RuntimeError):
    """An explicit local startup decision, not an unhandled application crash."""


class LocalConfigIssue(ValueError):
    LABELS = {'syntax': 'JSON 截断或语法损坏', 'empty': '配置文件为空', 'root': '配置根节点不是对象',
              'layout': '界面布局结构异常', 'fields': '配置字段缺失或类型异常',
              'future': '当前程序不支持的配置版本', 'permission': '系统拒绝读取配置',
              'io': '磁盘读取失败', 'unsafe': '配置路径或文件未通过安全检查',
              'backup': '原配置备份未完成', 'reset': '默认设置未能安全发布'}
    CORRUPT = frozenset({'syntax', 'empty', 'root', 'layout', 'fields'})

    def __init__(self, kind, raw=None):
        self.kind, self.raw = kind, raw
        self.sha256 = digest(raw) if raw is not None else None
        self.backup = None
        self.backup_verified = False
        self.backup_failed = False
        super().__init__('本机配置文件无法正常读取：' + self.LABELS[kind] + '。小说正文和作品资料未被清空。')

    @property
    def can_reset(self):
        return self.kind in self.CORRUPT and self.backup_verified and not self.backup_failed

    def diagnostic(self):
        # Never include parser snippets, configuration values, keys or project paths.
        return {'file': 'center.json', 'category': self.kind, 'description': self.LABELS[self.kind],
                'sha256': self.sha256, 'backup': Path(self.backup).name if self.backup else None,
                'backup_verified': self.backup_verified, 'backup_failed': self.backup_failed,
                'may_use_defaults': self.can_reset}


class LocalStore:
    """Only Novel Studio's own per-user state; never Antigravity settings."""
    def __init__(self, root: Path):
        self.root = Path(root)
        self.safe_start = False
        with SafeTree(self.root.parent) as parent:
            parent.mkdir(self.root.name)

    @staticmethod
    def _decode(raw):
        if raw is None:
            return {}
        if not raw.strip():
            raise LocalConfigIssue('empty', raw)
        try:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise LocalConfigIssue('fields', raw)
                    result[key] = value
                return result
            data = json.loads(raw, object_pairs_hook=unique)
        except LocalConfigIssue:
            raise
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise LocalConfigIssue('syntax', raw) from None
        except (ValueError, RecursionError):
            raise LocalConfigIssue('unsafe', raw) from None
        if not isinstance(data, dict):
            raise LocalConfigIssue('root', raw)
        # Existing state has no required top-level version or project fields.
        # Unknown explicit versions are never treated as corrupt legacy state.
        if 'format' in data and data['format'] != 'novel-studio-center-v1':
            raise LocalConfigIssue('future', raw)
        for key in ('version', 'schema_version', 'config_version'):
            if key in data and (type(data[key]) is not int or data[key] != 1):
                raise LocalConfigIssue('future', raw)
        for key in ('project', 'cli', 'plugin', 'installed_bundle'):
            if key in data and data[key] is not None and not isinstance(data[key], str):
                raise LocalConfigIssue('fields', raw)
        for key in ('recent_projects', 'archived_projects', 'api_connection_checks'):
            if key in data and not isinstance(data[key], list):
                raise LocalConfigIssue('fields', raw)
        for key in ('recent_projects', 'archived_projects'):
            if any(not isinstance(value, str) for value in data.get(key, [])):
                raise LocalConfigIssue('fields', raw)
        for key in ('project_titles', 'api_credentials', 'api_catalogs', 'api_consents'):
            if key in data and not isinstance(data[key], dict):
                raise LocalConfigIssue('fields', raw)
        if any(not isinstance(value, str) for value in data.get('project_titles', {}).values()):
            raise LocalConfigIssue('fields', raw)
        for role in data.get('api_credentials', {}).values():
            if not isinstance(role, dict) or any(value is not None and not isinstance(value, str) for value in role.values()):
                raise LocalConfigIssue('fields', raw)
        if any(value is not None and not isinstance(value, str) for value in data.get('api_consents', {}).values()):
            raise LocalConfigIssue('fields', raw)
        for rows in data.get('api_catalogs', {}).values():
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise LocalConfigIssue('fields', raw)
        if 'layout' in data:
            layout = data['layout']
            if not isinstance(layout, dict):
                raise LocalConfigIssue('layout', raw)
            version = layout.get('ui_version')
            if version is not None and (type(version) is not int or version not in {1, 2}):
                raise LocalConfigIssue('future', raw)
            if version == 2 and not {'theme', 'role', 'page', 'width', 'height', 'sidebar_expanded',
                                    'sidebar_width', 'drawer_expanded', 'drawer_width', 'reduce_motion'} <= set(layout):
                raise LocalConfigIssue('layout', raw)
            if ('width' in layout) != ('height' in layout):
                raise LocalConfigIssue('layout', raw)
            for key in ('width', 'height', 'sidebar_width', 'drawer_width', 'tab'):
                if key in layout and type(layout[key]) is not int:
                    raise LocalConfigIssue('layout', raw)
            for key in ('sidebar_expanded', 'drawer_expanded', 'reduce_motion'):
                if key in layout and type(layout[key]) is not bool:
                    raise LocalConfigIssue('layout', raw)
            for key in ('page', 'docks', 'theme', 'role'):
                if key in layout and not isinstance(layout[key], str):
                    raise LocalConfigIssue('layout', raw)
            if ('theme' in layout and layout['theme'] not in {'light', 'dark'}
                    or 'role' in layout and layout['role'] not in {'writer', 'editor'}):
                raise LocalConfigIssue('layout', raw)
        return data

    def load(self):
        try:
            with SafeTree(self.root) as tree:
                raw = tree.maybe_read('center.json')
        except PermissionError:
            raise LocalConfigIssue('permission') from None
        except OSError:
            raise LocalConfigIssue('io') from None
        except ValueError:
            raise LocalConfigIssue('unsafe') from None
        return self._decode(raw)

    @classmethod
    def open(cls, root, choose=None):
        safe_start = False
        while True:
            try:
                store = cls(root)
            except (OSError, ValueError) as error:
                safe_start = True
                issue = LocalConfigIssue('permission' if isinstance(error, PermissionError) else 'io' if isinstance(error, OSError) else 'unsafe')
                if choose is None:
                    raise issue from None
                if choose and choose(issue) == 'retry':
                    continue
                raise ConfigStartupCancelled() from None
            store.safe_start = safe_start
            store.startup_state = store.prepare(choose)
            return store

    def prepare(self, choose=None):
        while True:
            try:
                return self.load()
            except LocalConfigIssue as issue:
                if choose is None:
                    raise
                self.safe_start = True
                if issue.kind in issue.CORRUPT:
                    try:
                        self.backup(issue)
                    except (OSError, ValueError):
                        issue.backup_failed = True
                decision = choose(issue)
                if decision == 'retry':
                    continue
                if decision != 'defaults' or not issue.can_reset:
                    raise ConfigStartupCancelled() from None
                try:
                    self.reset(issue)
                    return self.load()
                except (OSError, ValueError):
                    failed = LocalConfigIssue('reset')
                    failed.sha256, failed.backup, failed.backup_verified = issue.sha256, issue.backup, issue.backup_verified
                    if choose(failed) == 'retry':
                        continue
                    raise ConfigStartupCancelled() from None

    def backup(self, issue):
        if issue.kind not in issue.CORRUPT or issue.raw is None:
            raise ValueError('Only confirmed corrupt state can be backed up for reset')
        with SafeTree(self.root) as tree:
            tree.mkdir('.novel-studio')
            with tree.lock():
                if tree.read('center.json') != issue.raw:
                    raise ValueError('Configuration changed; reread required')
                tree.mkdir('Config-Recovery')
                files = tree.files('Config-Recovery')
                matches = [name for name in files if re.fullmatch(issue.sha256 + r'-[a-f0-9]{32}\.bin', Path(name).name)]
                relative = next((name for name in matches if tree.read(name) == issue.raw), None)
                if relative is None:
                    if len(files) >= 996:
                        raise ValueError('Config recovery file limit exceeded')
                    relative = 'Config-Recovery/' + issue.sha256 + '-' + uuid.uuid4().hex + '.bin'
                    tree.create(relative, issue.raw)
                if tree.read(relative) != issue.raw:
                    raise ValueError('Corrupt configuration backup verification failed')
                receipt = relative[:-4] + '.json'
                metadata = {'format': 'novel-studio-config-backup-v1', 'file': 'center.json',
                            'sha256': issue.sha256, 'bytes': len(issue.raw), 'backup': Path(relative).name}
                recorded = False
                for name in files:
                    if re.fullmatch(issue.sha256 + r'-[a-f0-9]{32}\.json', Path(name).name):
                        try:
                            if json.loads(tree.read(name)) == metadata:
                                recorded = True
                                break
                        except (ValueError, UnicodeDecodeError):
                            pass  # Preserve torn receipts; never replace them.
                if not recorded:
                    if len(files) >= 997:
                        raise ValueError('Config recovery file limit exceeded')
                    if tree.maybe_read(receipt) is not None:
                        receipt = 'Config-Recovery/' + issue.sha256 + '-' + uuid.uuid4().hex + '.json'
                    payload = admin.encode(metadata)
                    tree.create(receipt, payload)
                    if tree.read(receipt) != payload:
                        raise ValueError('Configuration backup receipt did not verify')
                issue.backup, issue.backup_verified = str(self.root / relative), True

    def reset(self, issue):
        if not issue.can_reset:
            raise ValueError('Verified backup is required before resetting local state')
        defaults = {'layout': {}}  # A normal, backward-compatible minimal UI state, never an empty book.
        payload = admin.encode(defaults)
        self._decode(payload)
        with SafeTree(self.root) as tree:
            with tree.lock():
                if tree.read('center.json') != issue.raw:
                    raise ValueError('Configuration changed before reset')
                relative = Path(issue.backup).relative_to(self.root).as_posix()
                if tree.read(relative) != issue.raw:
                    raise ValueError('Original backup is no longer intact')
                if len(tree.files('Config-Recovery')) > 997:
                    raise ValueError('Config recovery file limit exceeded')
                record = 'Config-Recovery/' + issue.sha256 + '-' + uuid.uuid4().hex
                metadata = {'format': 'novel-studio-config-reset-v1', 'source_sha256': issue.sha256,
                            'defaults_sha256': digest(payload), 'backup': Path(relative).name}
                tree.create(record + '-pending.json', admin.encode(metadata))
                tree.publish('center.json', payload, issue.sha256)
                if tree.read('center.json') != payload:
                    raise ValueError('Default configuration publication did not verify')
                tree.create(record + '-done.json', admin.encode(metadata))

    def recovery_records(self):
        records = []
        with SafeTree(self.root) as tree:
            try:
                for index, (name, directory) in enumerate(tree.children('Config-Recovery')):
                    if directory or index >= 1000 or len(records) >= 50:
                        break
                    if name.endswith('.json') and re.fullmatch(r'[a-f0-9]{64}-[a-f0-9]{32}(?:-pending|-done)?\.json', name):
                        try:
                            value = json.loads(tree.read('Config-Recovery/' + name))
                            source_sha = value.get('sha256', value.get('source_sha256'))
                            backup = value.get('backup')
                            if (value.get('format') not in {'novel-studio-config-backup-v1', 'novel-studio-config-reset-v1'}
                                    or not isinstance(source_sha, str) or not re.fullmatch(r'[a-f0-9]{64}', source_sha)
                                    or not isinstance(backup, str) or not re.fullmatch(source_sha + r'-[a-f0-9]{32}\.bin', backup)):
                                raise ValueError('Invalid local recovery metadata')
                            records.append({'record': name, 'sha256': value.get('sha256', value.get('source_sha256')),
                                            'backup': value.get('backup'), 'phase': 'done' if '-done.' in name else 'pending' if '-pending.' in name else 'backup'})
                        except (ValueError, AttributeError):
                            records.append({'record': name, 'phase': 'invalid'})
            except FileNotFoundError:
                pass
        return records

    def save(self, changes):
        with SafeTree(self.root) as tree:
            tree.mkdir(".novel-studio")
            with tree.lock():
                old = tree.maybe_read("center.json")
                data = self._decode(old)
                data.update(changes)
                self._decode(admin.encode(data))
                tree.publish("center.json", admin.encode(data), digest(old) if old is not None else None)
                return data

    @staticmethod
    def protection_paths(raw):
        if raw is None:
            return []
        try:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError()
                    result[key] = value
                return result
            data = json.loads(raw, object_pairs_hook=unique)
            if (not isinstance(data, dict) or set(data) != {'format', 'paths'}
                    or data.get('format') != 'novel-studio-protected-folders-v1'):
                raise ValueError()
            paths = data['paths']
            if not isinstance(paths, list) or len(paths) > 64:
                raise ValueError()
            result = []
            for value in paths:
                if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
                    raise ValueError()
                path = Path(value)
                if not path.is_absolute() or '..' in path.parts:
                    raise ValueError()
                if path not in result:
                    result.append(path)
            return result
        except (ValueError, KeyError, TypeError):
            raise ValueError('本机保护文件夹配置无法核对，请保留原文件并检查备份。') from None

    def protection_config(self, bundle):
        with SafeTree(bundle) as source:
            inherited = source.maybe_read('.local/host-protection.json')
        with SafeTree(self.root) as tree:
            saved = tree.maybe_read('protected-folders.json')
        paths = list(dict.fromkeys(self.protection_paths(inherited) + self.protection_paths(saved)))
        if len(paths) > 64:
            raise ValueError('本机保护文件夹数量超过上限。')
        return {'paths': paths, 'revision': digest(admin.encode([
            digest(inherited) if inherited is not None else None,
            digest(saved) if saved is not None else None]))}

    def add_protected_folders(self, bundle, additions, expected_revision):
        paths = self.protection_paths(admin.encode({'format': 'novel-studio-protected-folders-v1', 'paths': additions}))
        for path in paths:
            if any(root == path or root.is_relative_to(path) for root in (Path(bundle), self.root)):
                raise ValueError('请选择程序和本机设置之外的文件夹。')
        with SafeTree(self.root) as tree:
            tree.mkdir('.novel-studio')
            with tree.lock():
                current = self.protection_config(bundle)
                if current['revision'] != expected_revision:
                    raise ValueError('保护文件夹配置刚刚变化，请重新打开后再添加。')
                combined = list(dict.fromkeys(current['paths'] + paths))
                payload = admin.encode({'format': 'novel-studio-protected-folders-v1', 'paths': list(map(str, combined))})
                self.protection_paths(payload)
                before = tree.maybe_read('protected-folders.json')
                if before == payload:
                    return len(combined)
                if before is not None:
                    tree.mkdir('Protected-Folders-History')
                    backup = 'Protected-Folders-History/' + digest(before) + '.json'
                    prior = tree.maybe_read(backup)
                    if prior is None:
                        tree.create(backup, before)
                    if tree.read(backup) != before:
                        raise ValueError('保护文件夹配置备份核验失败，已停止保存。')
                tree.publish('protected-folders.json', payload, digest(before) if before is not None else None)
        return len(combined)

    def capture_project_protection(self, bundle):
        current = self.protection_config(bundle)
        if current['paths']:
            self.add_protected_folders(bundle, [], current['revision'])
        return current['paths']


class Controller:
    def __init__(self, bundle: Path, state_root: Path, emit=None, config_recovery=None):
        self.bundle = Path(bundle)
        self.store = LocalStore.open(state_root, config_recovery)
        self.emit = emit or (lambda event: None)
        self.project = None
        self.active = set()
        self.cli = self.detect_cli()
        state = self.store.startup_state
        if state.get("cli"):
            self.cli = Path(state["cli"])
        if state.get("project") and not self.store.safe_start:
            try:
                self.open_project(Path(state["project"]))
            except (OSError, ValueError, KeyError):
                pass

    @staticmethod
    def detect_cli():
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home()/"AppData/Local")))
        path = local/"agy/bin/agy.exe"
        return path if path.is_file() else None

    def log(self, action, **details):
        self.emit({"time": now(), "action": action, **details})

    def command(self, arguments):
        if not self.cli or self.cli.name.lower() not in {"agy.exe", "agy"} or not self.cli.is_absolute() or not self.cli.is_file():
            raise ValueError("尚未找到官方 Antigravity 连接程序，请在安装页选择已安装的 agy.exe。")
        command = [str(self.cli), *arguments]
        self.log("官方连接命令", argv=command)
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.log("官方连接结果", exit_code=result.returncode, output=result.stdout[-10000:], error=result.stderr[-2000:])
        if result.returncode:
            raise ValueError("Antigravity 操作未成功：" + (result.stderr or result.stdout)[-1500:])
        return result.stdout

    def inspect_connection(self):
        version = self.command(["--version"]).strip()
        plugins = json.loads(self.command(["plugin", "list", "--json"]))
        installed = next((p for p in plugins if p["name"] == "novel-studio" and p.get("enabled")), None)
        return version, installed

    def export_details(self, events):
        # Explicit GUI action only. Never automatically persist manuscript/tool text.
        relative = "Exports/"+uuid.uuid4().hex+".json"
        with SafeTree(self.store.root) as tree:
            tree.mkdir("Exports")
            tree.create(relative, admin.encode({"format": "novel-studio-gui-audit-v1", "events": events}))
        return self.store.root/relative

    def install_component(self):
        payload = self.bundle/"payload/novel-studio"
        self.command(["plugin", "validate", "--strict", str(payload), "--json"])
        self.command(["plugin", "install", str(payload)])
        version, installed = self.inspect_connection()
        if not installed or set(installed.get("agents", [])) != {"novel-writer", "novel-editor"}:
            raise ValueError("连接组件未正确发现两个角色，尚不能开始创作。")
        self.store.save({"cli": str(self.cli), "plugin": installed["path"]})
        return version, installed

    def protected_roots(self):
        roots = [self.store.root, self.store.root.parent/"Apps", self.bundle]
        return roots + self.store.capture_project_protection(self.bundle)

    def new_project(self, parent: Path, title: str):
        self.require_idle()
        if len(relative_parts(title)) != 1:
            raise ValueError("作品名只能是一层文件夹名称")
        settings = self.store.load()
        if not settings.get("plugin"):
            raise ValueError("请先在“安装与连接”完成写作组件安装。")
        root = Path(parent)/title
        admin.initialize(root, Path(settings["plugin"]), self.protected_roots(), True)
        self.open_project(root)
        self.log("创建作品", project=str(root))
        return root

    def open_project(self, root: Path):
        self.require_idle()
        with SafeTree(root) as tree:
            admin.validate(tree)
            metadata = json.loads(tree.read(".novel-studio/project.json"))
            protected = {Path(value) for value in metadata["private_roots"]}
            if not {self.store.root, self.store.root.parent/"Apps"}.issubset(protected):
                raise ValueError("这个旧工程尚未包含图形控制中心的保护范围。请新建隔离工程；程序不会静默改动旧作品或全局设置。")
            expected = admin.policies(root, Path(metadata["plugin"]), metadata["private_roots"])
            for role in ("writer", "editor"):
                if json.loads(tree.read(f".novel-studio/Sessions/{role}.json")) != expected[role]:
                    raise ValueError("Session profile differs from generated policy")
        self.project = root
        self.store.save({"project": str(root)})
        return root

    def require_project(self):
        if self.project is None:
            raise ValueError("请先创建或打开一部作品。")
        return self.project

    def require_idle(self):
        if self.active:
            raise ValueError("创作或审稿仍在进行，请等待这一回合完成后再操作正式作品或快照。")
        if self.project:
            with SafeTree(self.project) as tree:
                if any(tree.maybe_read(f".novel-studio/.ns-{role}-turn.lock") is not None for role in ("writer", "editor")):
                    raise ValueError("另一个图形窗口仍在创作或审稿，请先等待它完成。")

    def state(self):
        return admin.ui_state(self.require_project())

    def context(self):
        self.require_idle()
        result = admin.context_snapshot(self.require_project(), True)
        admin.ui_state(self.project, {"context": result["destination"]+"/SNAPSHOT.json"})
        self.log("准备主笔资料", snapshot=result["destination"], count=len(result["files"]))
        return result

    def files(self, area):
        allowed = {"drafts": "Writer-Workspace/Drafts", "reports": "Editor-Workspace/Review-Reports"}
        with SafeTree(self.require_project()) as tree:
            return [name for name in tree.files(allowed[area]) if Path(name).suffix.lower() in {".md", ".txt"}]

    def read(self, relative):
        with SafeTree(self.require_project()) as tree:
            return tree.read(relative).decode("utf-8")

    def handoff(self, draft):
        self.require_idle()
        state = self.state()
        if not state.get("context"):
            self.context()
            state = self.state()
        result = admin.review_snapshot(self.project, draft, state["context"], True)
        admin.ui_state(self.project, {"review": result["destination"]+"/SNAPSHOT.json", "last_draft": draft})
        self.log("交接独立审稿副本", draft=draft, snapshot=result["destination"])
        return result

    def feedback(self, report, text):
        self.require_idle()
        name = admin.author_feedback(self.require_project(), report, text)
        admin.ui_state(self.project, {"feedback": name})
        self.log("作者选择审稿意见", report=report, feedback=name)
        return name

    def preview(self, draft, filename):
        self.require_idle()
        if len(relative_parts(filename)) != 1:
            raise ValueError("请选择一个正式章节文件名")
        return admin.promote(self.require_project(), draft, "Canonical/Final-Manuscript/"+filename)

    def approve(self, plan):
        self.require_idle()
        result = admin.promote(self.require_project(), plan["source"], plan["target"], True, plan["approval_digest"])
        self.log("作者批准正式定稿", target=plan["target"], transaction=result["transaction_id"])
        return result

    def import_preview(self, source, category):
        self.require_idle()
        if category not in admin.CANON or source.suffix.lower() not in {".md", ".txt", ".json"}:
            raise ValueError("当前支持 Markdown、纯文本或 JSON 设定资料。Word/PDF 请先在原软件导出文本。")
        return admin.import_material(self.require_project(), source, "Canonical/"+category+"/"+source.name)

    def import_approve(self, source, plan):
        self.require_idle()
        result = admin.import_material(self.require_project(), source, plan["target"], True, plan["approval_digest"])
        self.log("作者导入设定", target=plan["target"], transaction=result["transaction_id"])
        return result

    def history(self):
        result = []
        with SafeTree(self.require_project()) as tree:
            for name in tree.files("Backups/Transactions"):
                if name.endswith("/COMMITTED.json"):
                    data = json.loads(tree.read(name))
                    result.append((data["transaction_id"], data["target"], data.get("before_sha256") is not None))
        return list(reversed(result))

    def restore_preview(self, transaction):
        self.require_idle()
        return admin.restore(self.require_project(), transaction)

    def restore_approve(self, transaction, plan):
        self.require_idle()
        result = admin.restore(self.require_project(), transaction, True, plan["approval_digest"])
        self.log("作者恢复历史版本", target=result["target"], transaction=result["transaction_id"])
        return result


class RoleSession:
    """One visible GUI job per official CLI turn; no web server or model relay."""
    def __init__(self, controller: Controller, role: str, events: queue.Queue):
        if role not in {"writer", "editor"}:
            raise ValueError("Unknown role")
        self.controller, self.role, self.events = controller, role, events
        self.busy = False

    def send(self, prompt: str):
        if self.busy or not prompt.strip():
            raise ValueError("请等待上一回合结束，并输入要交给角色的内容。")
        c = self.controller
        # This deprecated native-write path has a different policy from the
        # qualified unified workbench. Do not inherit its permission proof.
        from novel_studio.host import OfficialCLI
        OfficialCLI(c.cli).require_compatible('legacy_native_write')
        c.require_idle()
        c.require_project()
        state = c.state()
        key = "context" if self.role == "writer" else "review"
        if not state.get(key):
            raise ValueError("请先准备主笔资料。" if self.role == "writer" else "请先在控制中心将指定草稿交给责编。")
        manifest = c.project/state[key]
        with SafeTree(c.project) as tree:
            json.loads(tree.read(state[key]))
        prefix = (f"作者指定作品：{c.project.name}。当前{('设定' if self.role=='writer' else '审稿')}快照：{manifest.as_posix()}。"
                  "请以这个精确版本为上下文，按角色边界处理；此消息不授权修改 Canonical、快照、备份、私人文件或权限设置。\n")
        if self.role == "writer" and state.get("feedback"):
            prefix += "作者筛选后的意见副本：" + (c.project/state["feedback"]).as_posix()+"。未选意见不自动合并。\n"
            if state.get("last_draft"):
                prefix += "本次交接的准确草稿路径："+(c.project/state["last_draft"]).as_posix()+"。请直接读取此文件，不猜测版本文件名；只使用角色文件工具，不使用终端列目录。\n"
        self.busy = True
        c.active.add(self.role)
        threading.Thread(target=self._run, args=(prefix+prompt, state.get("sessions", {}).get(self.role)), daemon=False).start()

    def _run(self, prompt, conversation):
        c = self.controller
        process = None
        guard = ExitStack()
        try:
            if not c.cli:
                raise ValueError("请先完成官方连接组件安装。")
            guard.enter_context(admin.role_turn(c.project, self.role))
            base_args, cwd = guard.enter_context(admin.launch_context(c.project, self.role, c.cli, conversation))
            args = base_args + ["--output-format", "stream-json", "--print-timeout", "0", "-p", prompt]
            c.log("图形会话请求", role=self.role, cwd=str(cwd), argv=base_args+["--output-format", "stream-json", "--print-timeout", "0", "-p", "<作者点击发送的内容>"])
            process = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            saw_result = False
            diagnostic = []
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    diagnostic.append(line[-500:])
                    diagnostic = diagnostic[-20:]
                    continue
                if event.get("event") == "init":
                    cid = event.get("conversation_id")
                    if cid:
                        state = c.state()
                        admin.ui_state(c.project, {"sessions": {self.role: cid}})
                    self.events.put(("init", event))
                elif event.get("event") == "step_update":
                    step = event["step_update"]
                    if step.get("step_type") == "tool":
                        self.events.put(("tool", step))
                    elif step.get("step_type") == "agent_response" and step.get("text_delta"):
                        self.events.put(("text", step["text_delta"]))
                elif event.get("event") == "result":
                    saw_result = True
                    self.events.put(("result", event["result"]))
            code = process.wait()
            if code or not saw_result:
                raise ValueError("官方连接未正常结束。请检查官方账号登录或网络状态；本程序不收集账号或密钥。\n"+"".join(diagnostic)[-1500:])
        except Exception as error:
            if process is not None and process.poll() is None:
                # Do not abandon an official host turn after a local UI-state error.
                for unused in process.stdout:
                    pass
                process.wait()
            self.events.put(("error", error))
        finally:
            try:
                guard.close()
            except Exception as error:
                self.events.put(("error", error))
            finally:
                self.busy = False
                c.active.discard(self.role)
                self.events.put(("done", None))
