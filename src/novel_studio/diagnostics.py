"""Bounded local diagnostics: fixed metadata, no text/payload/credentials."""
import hashlib
import logging
import json
from pathlib import Path
import re
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from .core import ROOT, VERSION, SafeTree, CoreError, admin, digest
from .host_diagnostics import HOST_ENUMS, HOST_BOOLEANS, HOST_COUNTS, HOST_FIELDS

EVENTS = frozenset('process_start process_exit ui_action ui_error job_start job_completed job_failed workflow_step model_start model_finish host_event host_error window_show window_done qt_message unhandled error_trace log_enabled'.split())
FIELDS = frozenset('action role provider model_id actual_model task phase status error_code error_class error_id job_id attempt_id run_id project_tag chapter_tag dialog_class icon result duration_ms exit_code errno winerror cli_version event_type qt_type persisted'.split())
BUDGET_FIELDS = frozenset('context_bytes raw_context_bytes limit_bytes outline_bytes always_bytes references_bytes manuscript_bytes deduplicated_bytes stable_bytes dynamic_bytes context_stage'.split())
CLI_FIELDS = frozenset({'cli_version', 'cli_compatibility'})
FIELDS = FIELDS | BUDGET_FIELDS | HOST_FIELDS | CLI_FIELDS
MAX_BYTES, SLOTS = 262144, 4
_SCOPE = ContextVar('novel_diagnostic_scope', default=None)


@contextmanager
def scope(**fields):
    approved = {k:v for k,v in fields.items() if k in {'action','role','task','job_id','attempt_id','run_id'}}
    marker = _SCOPE.set({**(_SCOPE.get() or {}), **approved})
    try:
        yield
    finally:
        _SCOPE.reset(marker)


def token(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_./:-]{1,100}', value) and not re.search(r'(sk-|AIza|Bearer|token=|key=)', value, re.I) else None


def frames(tb):
    result = []
    while tb is not None:
        code = tb.tb_frame.f_code
        path = Path(code.co_filename)
        try:
            name = path.relative_to(ROOT).as_posix()
        except ValueError:
            name = '<external>/' + (path.name if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', path.name) else 'module')
        if not name.startswith(('src/novel_studio/', 'scripts/', 'gui/', 'desktop/', '<external>/')):
            name = '<external>/' + path.name
        result.append({'file': name, 'line': tb.tb_lineno, 'function': token(code.co_name) or '<function>'})
        tb = tb.tb_next
    return result[-24:]


def stack_frames():
    result = []
    frame = sys._getframe(1)
    while frame is not None and len(result) < 24:
        path = Path(frame.f_code.co_filename)
        try:
            name = path.relative_to(ROOT).as_posix()
        except ValueError:
            name = '<external>/' + path.name
        result.append({'file': name, 'line': frame.f_lineno, 'function': token(frame.f_code.co_name) or '<function>'})
        frame = frame.f_back
    return result


def sanitized(row):
    if not isinstance(row, dict) or row.get('format') != 'novel-studio-diagnostic-v1' or row.get('event') not in EVENTS or type(row.get('time')) not in {int, float}:
        return None
    result = {'format': row['format'], 'time': row['time'], 'event': row['event']}
    for key in FIELDS | {'session', 'version'}:
        value = row.get(key)
        if key == 'cli_compatibility' and (not isinstance(value, str) or value not in {'SUPPORTED', 'UNSUPPORTED', 'INVALID', 'DETECTED', 'FULL', 'PARTIAL', 'INCOMPATIBLE'}):
            continue
        if key == 'cli_version' and (not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}', value)):
            continue
        if key in HOST_ENUMS and (not isinstance(value, str) or value not in HOST_ENUMS[key]):
            continue
        if key in HOST_BOOLEANS and type(value) is not bool:
            continue
        if key in HOST_COUNTS and (type(value) is not int or value < 0):
            continue
        if key in BUDGET_FIELDS and key != 'context_stage' and (type(value) not in {int, float} or value < 0):
            continue
        if key == 'context_stage' and value not in {'workflow','compiled','cli_transport','api_request','shared_context'}:
            continue
        if type(value) in {bool, int, float} or token(value) is not None:
            result[key] = value
    clean_frames = []
    for frame in row.get('frames', []) if isinstance(row.get('frames'), list) else []:
        name = frame.get('file') if isinstance(frame, dict) else None
        if isinstance(name, str) and '..' not in name and re.fullmatch(r'(?:(?:src/novel_studio|scripts|gui|desktop)/[A-Za-z0-9_/-]+|<external>/[A-Za-z0-9_.-]+)\.pyw?', name) and type(frame.get('line')) is int:
            clean_frames.append({'file': name, 'line': frame['line'], 'function': token(frame.get('function')) or '<function>'})
    if clean_frames:
        result['frames'] = clean_frames[-24:]
    return result


class DiagnosticLog(logging.Handler):
    """Reuse logging's thread lock and SafeTree publication/lock, never root logs."""
    def __init__(self, state_root):
        super().__init__()
        self.root = Path(state_root) / 'Diagnostics'
        self.session = uuid.uuid4().hex
        self.enabled, self.available = True, True
        self.recent = []
        self.last_error_id = None
        try:
            with SafeTree(Path(state_root).parent) as parent:
                parent.mkdir(Path(state_root).name)
            with SafeTree(Path(state_root)) as tree:
                tree.mkdir('Diagnostics')
                raw = tree.maybe_read('diagnostic-preference.json')
                if raw:
                    self.enabled = json.loads(raw).get('enabled', True) is True
        except Exception:
            self.available = False

    def tag(self, value):
        return hashlib.sha256((self.session + str(value)).encode()).hexdigest()[:12]

    def event(self, event, **fields):
        if not self.enabled or event not in EVENTS:
            return None
        row = {'format': 'novel-studio-diagnostic-v1', 'time': time.time(), 'session': self.session, 'version': VERSION, 'event': event}
        for key, value in {**(_SCOPE.get() or {}), **fields}.items():
            if key not in FIELDS:
                continue
            if type(value) in {bool, int, float}:
                row[key] = value
            elif token(value) is not None:
                row[key] = value
        record = logging.LogRecord('novel_studio.local', logging.INFO, '', 0, '', (), None)
        record.safe_row = row
        self.handle(record)
        return row

    def emit(self, record):
        # Never format record.msg/args/exc_info: they may contain private text.
        row = sanitized(getattr(record, 'safe_row', None))
        if row is None:
            return
        self.recent = (self.recent + [row])[-500:]
        if not self.available:
            return
        try:
            with SafeTree(self.root) as tree:
                # Handler serializes this process. SafeTree CAS detects another
                # process; no persistent lock can be stranded by a native crash.
                meta = tree.maybe_read('journal.json')
                slot = json.loads(meta).get('slot', 0) if meta else 0
                if type(slot) is not int or not 0 <= slot < SLOTS:
                    slot = 0
                name = f'events-{slot}.jsonl'
                old = tree.maybe_read(name)
                line = json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
                if len(old or b'') + len(line) > MAX_BYTES:
                    slot = (slot + 1) % SLOTS
                    name = f'events-{slot}.jsonl'
                    old = tree.maybe_read(name)
                    data = line
                else:
                    data = (old or b'') + line
                tree.publish(name, data, digest(old) if old is not None else None)
                tree.publish('journal.json', admin.encode({'slot': slot}), digest(meta) if meta is not None else None)
        except Exception:
            self.available = False  # diagnostics cannot break a writing action

    def error(self, error, event='ui_error', **fields):
        previous = getattr(error, '_novel_diagnostic_identity', None)
        identity = previous[1] if isinstance(previous, tuple) and len(previous) == 2 and previous[0] == self.session and isinstance(previous[1], str) and re.fullmatch(r'[a-f0-9]{12}', previous[1]) else uuid.uuid4().hex[:12]
        try:
            error._novel_diagnostic_identity = (self.session, identity)
        except (AttributeError, TypeError):
            pass
        self.last_error_id = identity
        details = getattr(error, 'diagnostic_details', {})
        safe_details = {k:v for k,v in details.items() if k in BUDGET_FIELDS | HOST_FIELDS | CLI_FIELDS} if isinstance(details, dict) else {}
        row = self.event(event, error_id=identity, error_code=getattr(error, 'code', 'UNEXPECTED_EXCEPTION'), error_class=type(error).__name__,
            errno=getattr(error, 'errno', None), winerror=getattr(error, 'winerror', None), **{**safe_details, **fields})
        if row is not None:
            # Stack locations only: no exception message, source lines or locals.
            stack = frames(error.__traceback__)
            if stack:
                self.handle_safe_stack(identity, stack)
        return identity

    def handle_safe_stack(self, identity, stack):
        row = {'format': 'novel-studio-diagnostic-v1', 'time': time.time(), 'session': self.session, 'version': VERSION,
               'event': 'error_trace', 'error_id': identity, 'frames': stack, **(_SCOPE.get() or {})}
        record = logging.LogRecord('novel_studio.local', logging.ERROR, '', 0, '', (), None)
        record.safe_row = row
        self.handle(record)

    def dialog(self, dialog_class, icon=None, error_id=None):
        row = {'format':'novel-studio-diagnostic-v1','time':time.time(),'session':self.session,'version':VERSION,
               'event':'window_show','dialog_class':dialog_class,'frames':stack_frames()}
        if icon is not None: row['icon'] = icon
        if error_id is not None: row['error_id'] = error_id
        if self.enabled:
            record = logging.LogRecord('novel_studio.local',logging.INFO,'',0,'',(),None)
            record.safe_row = row
            self.handle(record)

    def report(self):
        rows = []
        if self.available:
            try:
                with SafeTree(self.root) as tree:
                    for slot in range(SLOTS):
                        raw = tree.maybe_read(f'events-{slot}.jsonl')
                        if raw:
                            for line in raw.splitlines():
                                try:
                                    row = sanitized(json.loads(line))
                                    if row is not None:
                                        rows.append(row)
                                except (ValueError, TypeError):
                                    pass  # partial line never hides other valid records
            except Exception:
                self.available = False
        rows = list({json.dumps(row, sort_keys=True): row for row in rows + self.recent}.values())
        opened = {r.get('session') for r in rows if r.get('event') == 'process_start'}
        closed = {r.get('session') for r in rows if r.get('event') == 'process_exit'}
        return {'format': 'novel-studio-diagnostic-export-v1', 'version': VERSION, 'enabled': self.enabled, 'persisted': self.available,
                'privacy': '仅本机元数据及代码位置；不含正文、提示词、密钥、CLI原始输出或完整个人路径。不会自动上传。',
                'unclosed_sessions': sorted(opened - closed - {self.session}),
                'unclosed_note': '缺少正常退出标记可能是关机、强制关闭、崩溃或日志轮转；仅作排查线索，不能独自认定崩溃。',
                'events': sorted(rows, key=lambda row: row['time'])[-3000:]}

    def set_enabled(self, enabled):
        with SafeTree(self.root.parent) as tree:
            raw = tree.maybe_read('diagnostic-preference.json')
            tree.publish('diagnostic-preference.json', admin.encode({'enabled': bool(enabled)}), digest(raw) if raw is not None else None)
        self.enabled = bool(enabled)
        self.event('log_enabled', status='ON' if enabled else 'OFF')

    def export(self, target):
        """Exclusive publication; collisions choose another name, never overwrite."""
        target = Path(target)
        payload = admin.encode(self.report())
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime())
        try:
            with SafeTree(target.parent) as tree:
                for attempt in range(32):
                    suffix = '' if attempt == 0 else '-' + stamp + ('' if attempt == 1 else '-' + str(attempt))
                    name = target.stem + suffix + target.suffix
                    try:
                        tree.create(name, payload)
                    except FileExistsError:
                        continue
                    saved = target.parent / name
                    self.event('ui_action', action='diagnostic_export', status='SAVED')
                    return saved
        except (OSError, ValueError) as cause:
            error = CoreError('DIAGNOSTIC_EXPORT_FAILED', '无法在这个位置保存日志。请改选你有写入权限的本机文件夹；原有文件保持不变。')
            for key in ('errno', 'winerror'):
                if type(getattr(cause, key, None)) is int:
                    setattr(error, key, getattr(cause, key))
            raise error from None
        raise CoreError('DIAGNOSTIC_EXPORT_CONFLICT', '这个位置的同名日志较多，请换一个文件名或文件夹。原有文件保持不变。')


def record(core, event, **fields):
    log = getattr(core, 'diagnostics', None)
    if log:
        log.event(event, project_tag=log.tag(core.project_id), **fields)
