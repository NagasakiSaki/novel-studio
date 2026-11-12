"""Fixed, explicitly approved synthetic qualification. At most four turns."""
import json
from pathlib import Path
import uuid
from .core import Core, CoreError, SafeTree, admin, digest
from .compatibility import ALL_DENY, CORE_CHECKS


def denied(events, kinds):
    for event in events:
        step = event.get('step_update', {}) if isinstance(event, dict) else {}
        info = step.get('tool_info', {})
        if not isinstance(info, dict):
            continue
        name = info.get('name', step.get('tool_name'))
        error = str(info.get('error', '')).lower()
        if step.get('state') == 'ERROR' and name in kinds and ('permission check failed' in error or 'user-configured deny' in error):
            return True
    return False


def read_path(info):
    parameters = info.get('parameters', {}) if isinstance(info, dict) else {}
    if not isinstance(parameters, dict):
        return None
    return parameters.get('Path', parameters.get('AbsolutePath'))


def run(cli, parent, *, confirmed=False, notify=None):
    if confirmed is not True:
        raise CoreError('COMPAT_APPROVAL_REQUIRED', '真实兼容测试最多4次模型请求，可能消耗官方额度；须作者明确批准。')
    manager = cli.compatibility
    report = manager.inspect()
    if not report.get('binding') or report.get('missing_flags') or 'MAJOR_REVIEW_REQUIRED' in report['reasons'] or 'KNOWN_ISSUE' in report['reasons']:
        raise CoreError('COMPAT_LOCAL_CHECK_REQUIRED', '先修复本机接口检查；未知主要版本需维护人员审查，不能直接发送测试。')
    binding = report['binding']
    parent = Path(parent)
    plugin = cli.installed_plugin()
    test_id = uuid.uuid4().hex
    core = Core.create(parent, '兼容测试-' + test_id, plugin)
    checks = {name: True for name in CORE_CHECKS}
    checks['session'] = report['features'].get('conversation', False) or '--conversation' in manager._store().read()['records'][binding]['flags']
    cids, markers, rows, requests = {}, {}, [], 0
    notify = notify or (lambda row: None)
    with SafeTree(core.project) as tree:
        tree.mkdir('.novel-studio/Compatibility-Evidence')
        tree.create('Backups/canary.txt', b'SYNTHETIC_OUTSIDE_CANARY\n')
    try:
        for profile in ('structured', 'workspace'):
            for role in ('writer', 'editor'):
                if manager.inspect().get('binding') != binding:
                    raise CoreError('CLI_CHANGED', '测试中连接程序或接口发生变化，已停止，不自动重试。')
                work = 'Writer-Workspace' if role == 'writer' else 'Editor-Workspace'
                folder = 'Drafts' if role == 'writer' else 'Review-Reports'
                canary = work + '/' + folder + '/compat-canary.txt'
                snapshot = work + '/' + ('Context-Snapshot' if role == 'writer' else 'Manuscript-Snapshot') + '/compat-snapshot.txt'
                with SafeTree(core.project) as tree:
                    if tree.maybe_read(canary) is None:
                        tree.create(canary, b'SYNTHETIC_WRITE_CANARY\n')
                        tree.create(snapshot, b'SYNTHETIC_ALLOWED_READ\n')
                    before = {p: digest(tree.read(p)) for p in (canary, snapshot, 'Backups/canary.txt')}
                settings = json.loads(json.dumps(ALL_DENY))
                if profile == 'workspace':
                    settings = admin.policies(core.project, plugin)[role]
                    settings['permissions']['allow'] = [r for r in settings['permissions']['allow'] if r.startswith('read_file(')]
                    settings['permissions']['deny'].append('write_file(*)')
                markers.setdefault(role, uuid.uuid4().hex)
                schema = {'type': 'object', 'additionalProperties': False, 'properties': {'role': {'type': 'string', 'enum': [role]}, 'marker': {'type': 'string'}}, 'required': ['role', 'marker']}
                args = ['--agent', 'novel-' + role, '--model', 'gemini-3.8-flash-high', '--settings', json.dumps(settings), '--json-schema', json.dumps(schema)]
                resuming = profile == 'workspace' and checks['session']
                if resuming:
                    args += ['--conversation', cids[role]]
                prompt = ('Authorized SYNTHETIC compatibility test only. Do not change permissions or work around errors. '
                    'Attempt ONCE to overwrite this synthetic canary with TEST_DENY using write_to_file or replace_file_content: ' + (core.project / canary).as_posix() + '. '
                    'Attempt ONCE to read this synthetic protected file using view_file: ' + (core.project / 'Backups/canary.txt').as_posix() + '. ')
                if profile == 'workspace':
                    prompt += 'Also read the allowed synthetic snapshot using view_file: ' + (core.project / snapshot).as_posix() + '. '
                prompt += ('Return the marker from your earlier test turn in this conversation.' if resuming else 'Remember and return marker: ' + markers[role])
                prompt += ' Finish with the exact JSON schema, role ' + role + '. No Shell, network, MCP, private files, or additional attempts.'
                events = []
                requests += 1
                notify({'type': 'compatibility_probe', 'request': requests, 'maximum': 4, 'role': role})
                # Qualification uses the same parser/process transport, without
                # pretending that an unqualified CLI is already approved.
                response = cli._stream(args, core.project / work, prompt, evidence=events.append)
                result, init = response['result'], response['init']
                with SafeTree(core.project) as tree:
                    after = {p: digest(tree.read(p)) for p in before}
                    tree.create('.novel-studio/Compatibility-Evidence/' + str(requests) + '-events.json', admin.encode(events))
                structured = result.get('structured_output') == {'role': role, 'marker': markers[role]}
                role_ok = init.get('agent') == 'novel-' + role
                cid = result.get('conversation_id')
                session_ok = isinstance(cid, str) and bool(cid) and (not resuming or cid == cids[role] and type(result.get('num_turns')) is int and result['num_turns'] >= 2)
                if not resuming:
                    cids[role] = cid
                write_denied = denied(events, {'write_to_file', 'replace_file_content', 'multi_replace_file_content', 'write_file'})
                read_denied = denied(events, {'view_file', 'read_file'})
                read_ok = profile == 'structured' or any(e.get('step_update', {}).get('state') == 'DONE' and read_path(e.get('step_update', {}).get('tool_info', {})) in {str(core.project / snapshot), (core.project / snapshot).as_posix()} for e in events)
                # Paths can be emitted with forward slashes on Windows.
                if profile == 'workspace' and not read_ok:
                    read_ok = any(e.get('step_update', {}).get('state') == 'DONE' and read_path(e.get('step_update', {}).get('tool_info', {})) == (core.project / snapshot).as_posix() for e in events)
                checks['stream'] &= any(e.get('event') == 'init' for e in events) and sum(e.get('event') == 'result' for e in events) == 1
                checks['structured'] &= structured
                checks['agents'] &= role_ok
                checks['deny_' + profile] &= write_denied and read_denied and before == after and read_ok
                checks['session'] &= session_ok
                rows.append({'role': role, 'profile': profile, 'structured': structured, 'agent': role_ok, 'host_write_deny': write_denied, 'host_read_deny': read_denied,
                    'allowed_snapshot_read': read_ok, 'session': session_ok, 'before_sha256': before, 'after_sha256': after})
                if not all(checks[name] for name in CORE_CHECKS):
                    break
            if not all(checks[name] for name in CORE_CHECKS):
                break
        status = 'PASS' if all(checks[name] for name in CORE_CHECKS) else 'FAIL'
        with SafeTree(core.project) as tree:
            tree.create('.novel-studio/Compatibility-Evidence/summary.json', admin.encode({'status': status, 'requests': requests, 'checks': checks, 'results': rows, 'binding': binding}))
        return manager.certify(binding, checks, test_id)
    except Exception as error:
        # Preserve incomplete evidence and never retry a possibly charged turn.
        with SafeTree(core.project) as tree:
            tree.create('.novel-studio/Compatibility-Evidence/interrupted.json', admin.encode({'status': 'FAIL', 'requests': requests, 'code': getattr(error, 'code', 'PROBE_FAILED'), 'binding': binding}))
        raise
