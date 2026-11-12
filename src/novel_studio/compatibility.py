"""Capability evidence, not a version allowlist. No paid probes in inspection."""
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from .core import CoreError, ROOT, SafeTree, admin, digest
from .compatibility_store import ReceiptStore
from .version_info import ADAPTER_VERSION, CONTRACT_VERSION

REQUIRED_FLAGS = ('--agent', '--settings', '--model', '--json-schema', '--input-format', '--output-format', '--print-timeout')
OPTIONAL_FLAGS = {'effort': '--effort', 'conversation': '--conversation'}
CORE_CHECKS = ('stream', 'structured', 'agents', 'deny_structured', 'deny_workspace')
# Historical observations have explicit scopes; none grants capabilities by version.
HISTORICAL_VERSIONS = {'1.5.0': 'historical host probes', '1.5.1': 'historical host workflows',
    '1.5.2': 'historical host denial and structured workflows', '1.5.3': 'alpha.16 local flags/strict only; live evidence required'}
KNOWN_ISSUES = {}  # Add only confirmed upstream defects, with a reason and evidence.
REVIEWED_MAJORS = (1,)
ALL_DENY = {'allowNonWorkspaceAccess': False, 'permissions': {'allow': [], 'ask': [],
    'deny': ['read_file(*)', 'write_file(*)', 'command(*)', 'read_url(*)', 'execute_url(*)', 'mcp(*)']}}


def identity(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise CoreError('CLI_MISSING', '连接程序位置无效。')
    for item in (path, *path.parents):
        info = item.lstat()
        if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise CoreError('CLI_IDENTITY_UNSAFE', '连接程序路径包含目录链接，无法确认程序身份。')
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise CoreError('CLI_CHANGED', '连接程序在检查时更新了，请重新检查。')
    return {'path_tag': digest(str(path).encode()), 'sha256': sha}


def contract(plugin):
    names = ['host.py', 'host_diagnostics.py', 'compatibility.py', 'compatibility_probe.py', 'compatibility_store.py']
    files = {name: digest((ROOT / 'src/novel_studio' / name).read_bytes()) for name in names}
    files['workspace_admin'] = digest((ROOT / 'scripts/workspace_admin.py').read_bytes())
    # The actual installed role/rule definitions, not just the source checkout.
    with SafeTree(plugin) as tree:
        for folder in ('agents', 'rules'):
            for name in tree.files(folder):
                files['plugin/' + name] = digest(tree.read(name))
        files['plugin.json'] = digest(tree.read('plugin.json'))
    return digest(admin.encode({'adapter': ADAPTER_VERSION, 'contract': CONTRACT_VERSION,
        'required': REQUIRED_FLAGS, 'optional': OPTIONAL_FLAGS, 'settings': ALL_DENY, 'files': files}))


def evaluate(record, version, issues=None):
    flags, proof = record.get('flags', []), record.get('proof', {})
    if not isinstance(proof, dict):
        proof = {}
    reasons = []
    if version in (KNOWN_ISSUES if issues is None else issues):
        reasons.append('KNOWN_ISSUE')
    if int(version.split('.')[0]) not in REVIEWED_MAJORS:
        reasons.append('MAJOR_REVIEW_REQUIRED')
    if any(flag not in flags for flag in REQUIRED_FLAGS):
        reasons.append('REQUIRED_FLAG_MISSING')
    if record.get('runtime_failure'):
        reasons.append('RUNTIME_CONTRACT_FAILED')
    if not isinstance(proof, dict) or any(proof.get(check) is not True for check in CORE_CHECKS):
        reasons.append('LIVE_EVIDENCE_REQUIRED')
    features = {key: flag in flags and (key != 'conversation' or proof.get('session') is True) for key, flag in OPTIONAL_FLAGS.items()}
    status = 'INCOMPATIBLE' if reasons else 'FULL' if all(features.values()) else 'PARTIAL'
    return {'status': status, 'model_allowed': not reasons, 'reasons': reasons, 'features': features,
        'checks': {key: 'PASS' if proof.get(key) is True else 'FAIL' if proof.get(key) is False else 'NOT TESTED' for key in CORE_CHECKS},
        'missing_flags': [flag for flag in REQUIRED_FLAGS if flag not in flags], 'cli_version': version}


class CompatibilityManager:
    def __init__(self, cli, root=None, store=None):
        self.cli = cli
        self.root = Path(root) if root else Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'NovelStudioDesktop/Compatibility'
        self.store = store
        self.mutex = threading.RLock()
        self.last = None
        self._binary_stamp = self._binary_id = None

    def _store(self):
        if self.store is None:
            if not self.root.parent.exists():
                with SafeTree(self.root.parent.parent) as parent:
                    parent.mkdir(self.root.parent.name)
            self.store = ReceiptStore(self.root)
        return self.store

    def binding(self):
        self.cli.check()
        # Hashing the executable is a cheap identity check, not another model or
        # protocol probe. Detect replacements even with preserved timestamps.
        self._binary_id = identity(self.cli.path)
        version = self.cli.version()
        plugin = self.cli.installed_plugin()
        info = {**self._binary_id, 'version': version, 'contract': contract(plugin), 'adapter': ADAPTER_VERSION}
        return digest(admin.encode(info)), info

    def inspect(self, force=False):
        with self.mutex:
            try:
                key, info = self.binding()
                record = self._store().read()['records'].get(key)
                if record is not None and (not isinstance(record, dict) or record.get('binding') != info or not isinstance(record.get('flags'), list)):
                    raise CoreError('COMPAT_STORE_INVALID', '兼容缓存的身份或字段无法校验；保留记录，不自动放行。')
                if record is None or force:
                    help_text = self.cli.help_text()
                    flags = re.findall(r'(?m)^\s*(--[a-z][a-z-]+)(?=\s|=|$)', help_text)
                    record = {**(record or {}), 'binding': info, 'flags': sorted(set(flags)), 'checked_at': time.time()}
                    # A changed observed help contract never inherits old proof.
                    previous = self._store().read()['records'].get(key)
                    if previous and previous['flags'] != record['flags']:
                        record.pop('proof', None)
                    self._store().update(key, record)
                result = {**evaluate(record, info['version']), 'binding': key, 'adapter_version': ADAPTER_VERSION,
                    'checked_at': record['checked_at'], 'cli_sha256': info['sha256'], 'evidence': record.get('evidence'),
                    'historical_versions': HISTORICAL_VERSIONS, 'known_issues': KNOWN_ISSUES}
            except (OSError, ValueError) as error:
                result = {'status': 'INCOMPATIBLE', 'model_allowed': False, 'features': {}, 'checks': {},
                    'reasons': [getattr(error, 'code', 'CLI_START_FAILED')], 'cli_version': getattr(self.cli, 'last_version', None)}
            self.last = result
            return result

    def require(self, feature=None):
        report = self.inspect()
        if not report['model_allowed'] or feature and not report['features'].get(feature):
            error = CoreError('CLI_INCOMPATIBLE', '连接程序尚未通过所需能力验证，模型操作已暂停。请打开“设置 → 高级与安装 → 兼容性与版本”查看原因；本地编辑和导出仍可使用。本次未发送模型请求。')
            error.diagnostic_details = {'cli_version': report.get('cli_version'), 'cli_compatibility': report['status']}
            raise error
        return report

    def certify(self, binding, proof, evidence):
        """Only fixed author-approved synthetic probes call this; no JSON import."""
        with self.mutex:
            current, info = self.binding()
            if current != binding or set(proof) != set(CORE_CHECKS) | {'session'} or any(type(v) is not bool for v in proof.values()):
                raise CoreError('CLI_CHANGED', '测试依据已变化或证据不完整，没有保存通过状态。')
            value = self._store().read()['records'].get(binding)
            if value is None:
                raise CoreError('CLI_CHANGED', '本机检查依据已变化。')
            value.update(proof=proof, evidence=evidence, runtime_failure=None)
            self._store().update(binding, value)
            return self.inspect()

    def invalidate(self, code):
        if code not in {'HOST_PROTOCOL_ERROR', 'HOST_RESULT_MISSING', 'INVALID_ARTIFACT', 'MODEL_MISMATCH', 'HOST_STATE_UNKNOWN', 'CLI_PERMISSION_ANOMALY'}:
            return
        with self.mutex:
            if not self.last or not self.last.get('binding'):
                return
            key = self.last['binding']
            value = self._store().read()['records'].get(key)
            if value:
                value['runtime_failure'] = code
                self._store().update(key, value)
