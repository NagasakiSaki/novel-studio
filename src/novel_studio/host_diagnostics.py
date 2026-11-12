"""Closed-vocabulary host outcomes; never persist raw errors or model content."""
import re
from .core import CoreError

HOST_ENUMS = {
    'host_result_status': frozenset('SUCCESS ERROR CANCELED INTERRUPTED INVALID WAITING RUNNING MISSING MALFORMED UNKNOWN'.split()),
    'host_failure': frozenset('NONE QUOTA AUTH MODEL PERMISSION TIMEOUT NETWORK CONTEXT OUTPUT_SCHEMA OUTPUT_LIMIT CANCELED INTERRUPTED WAITING INCOMPLETE REPORTED_ERROR MISSING_RESULT PROTOCOL UNKNOWN'.split()),
    'host_failure_source': frozenset('NONE STRUCTURED_CODE MESSAGE_PATTERN STDERR_PATTERN RESULT_STATE PROTOCOL'.split()),
    'host_error_code': frozenset('RESOURCE_EXHAUSTED UNAUTHENTICATED PERMISSION_DENIED INVALID_ARGUMENT UNAVAILABLE DEADLINE_EXCEEDED CANCELLED CANCELED INTERNAL UNKNOWN OUT_OF_RANGE UNIMPLEMENTED FAILED_PRECONDITION NOT_FOUND ABORTED DATA_LOSS 400 401 403 404 408 429 500 502 503 504 OTHER NONE'.split()),
    'last_step_type': frozenset('user_input agent_response tool thinking checkpoint system OTHER NONE'.split()),
}
HOST_BOOLEANS = frozenset('host_result_present structured_output_present response_present'.split())
HOST_ENUMS['host_error_hint'] = HOST_ENUMS['host_failure']
HOST_COUNTS = frozenset('result_event_count step_error_count parse_error_count host_error_chars'.split())
HOST_FIELDS = frozenset(HOST_ENUMS) | HOST_BOOLEANS | HOST_COUNTS
CODE_KINDS = {'RESOURCE_EXHAUSTED':'QUOTA','429':'QUOTA','UNAUTHENTICATED':'AUTH','401':'AUTH',
    'PERMISSION_DENIED':'PERMISSION','403':'PERMISSION','UNAVAILABLE':'NETWORK','502':'NETWORK','503':'NETWORK',
    'DEADLINE_EXCEEDED':'TIMEOUT','408':'TIMEOUT','504':'TIMEOUT','CANCELLED':'CANCELED','CANCELED':'CANCELED','NOT_FOUND':'MODEL','404':'MODEL'}
PATTERNS = (
    ('QUOTA', r'resource_exhausted|quota exceeded|rate limit|credits exhausted'),
    ('AUTH', r'unauthenticated|authentication required|not authenticated'),
    ('MODEL', r'model not found|unknown model|unsupported model'),
    ('PERMISSION', r'permission denied|access denied|tool.*(?:denied|not allowed)'),
    ('TIMEOUT', r'deadline exceeded|timed out|timeout'),
    ('CONTEXT', r'context (?:length|window).*(?:exceed|limit|large)|input token limit'),
    ('OUTPUT_LIMIT', r'max_output_tokens|max output tokens|maximum output tokens|MAX_TOKENS'),
    ('OUTPUT_SCHEMA', r'json schema|structured.output|schema validation|invalid json'),
    ('NETWORK', r'\beof\b|stream (?:closed|dropped)|connection (?:reset|closed|failed)|unavailable|dial tcp'),
)


def classify_hint(error):
    """Patterns are diagnostic clues, never proof of server-side root cause."""
    code = error.get('code') if isinstance(error, dict) else None
    if type(code) is int: code = str(code)
    if isinstance(code, str): code = code.upper()
    if not isinstance(code, str) or code not in HOST_ENUMS['host_error_code']:
        code = 'OTHER' if code is not None else 'NONE'
    text = error if isinstance(error, str) else error.get('message', '') if isinstance(error, dict) else ''
    if not isinstance(text, str): text = ''
    length = len(text)
    if code in CODE_KINDS:
        return CODE_KINDS[code], 'STRUCTURED_CODE', code, length
    # Bound transient pattern work too; no text is retained in the summary.
    for kind, pattern in PATTERNS:
        if re.search(pattern, text[:8000], re.I):
            return kind, 'MESSAGE_PATTERN', code, length
    return 'UNKNOWN', 'NONE', code, length


class HostTrace:
    def __init__(self):
        self.result_events = self.step_errors = self.parse_errors = 0
        self.status, self.last_step = 'MISSING', 'NONE'
        self.present = self.structured = self.response = False
        self.hint = ('UNKNOWN','NONE','NONE',0)
        self.stderr_hint = ('UNKNOWN','NONE','NONE',0)

    def observe_diagnostic(self, text):
        hint = classify_hint(text)
        if hint[0] != 'UNKNOWN':
            self.stderr_hint = hint

    def observe(self, event):
        if not isinstance(event, dict):
            self.parse_errors += 1
            return False
        if event.get('event') == 'step_update':
            step = event.get('step_update')
            if not isinstance(step, dict):
                self.parse_errors += 1
                return False
            value = step.get('step_type')
            self.last_step = value if isinstance(value, str) and value in HOST_ENUMS['last_step_type'] else 'OTHER'
            if step.get('state') == 'ERROR': self.step_errors += 1
        if event.get('event') == 'result':
            self.result_events += 1
            result = event.get('result')
            self.present = isinstance(result, dict)
            if not self.present:
                self.status = 'MALFORMED'
                return False
            value = result.get('status')
            self.status = value if isinstance(value, str) and value in HOST_ENUMS['host_result_status'] else 'UNKNOWN'
            self.structured = result.get('structured_output') is not None
            self.response = bool(result.get('response'))
            self.hint = classify_hint(result.get('error'))
        return True

    def summary(self, exit_code=None):
        kind, source, code, chars = self.hint
        if kind == 'UNKNOWN' and self.status in {'ERROR', 'MISSING'} and self.stderr_hint[0] != 'UNKNOWN':
            kind, unused, code, chars = self.stderr_hint
            source = 'STDERR_PATTERN'
        if self.result_events > 1 or self.status == 'MALFORMED': kind, source = 'PROTOCOL', 'PROTOCOL'
        elif not self.present and kind == 'UNKNOWN': kind, source = 'MISSING_RESULT', 'PROTOCOL'
        elif self.status == 'SUCCESS' and not exit_code: kind, source = 'NONE', 'NONE'
        elif kind == 'UNKNOWN':
            kind = {'CANCELED':'CANCELED','INTERRUPTED':'INTERRUPTED','WAITING':'WAITING','RUNNING':'INCOMPLETE','INVALID':'PROTOCOL','ERROR':'REPORTED_ERROR'}.get(self.status,'UNKNOWN')
            source = 'RESULT_STATE'
        return {'host_result_status':self.status,'host_failure':kind,'host_error_hint':kind if source == 'STDERR_PATTERN' else self.hint[0],'host_failure_source':source,'host_error_code':code,
            'host_error_chars':chars,'result_event_count':self.result_events,'step_error_count':self.step_errors,
            'parse_error_count':self.parse_errors,'last_step_type':self.last_step,'host_result_present':self.present,
            'structured_output_present':self.structured,'response_present':self.response}

    def failure(self, exit_code):
        detail = self.summary(exit_code)
        kind = detail['host_failure']
        if kind == 'MISSING_RESULT' and self.hint[0] not in {'UNKNOWN','NONE'}:
            kind = self.hint[0]
        code = {'QUOTA':'QUOTA_EXHAUSTED','AUTH':'AUTH_REQUIRED','MODEL':'MODEL_UNAVAILABLE','PERMISSION':'HOST_DENIED',
                'TIMEOUT':'REQUEST_TIMEOUT','NETWORK':'NETWORK_ERROR','CONTEXT':'HOST_CONTEXT_LIMIT','OUTPUT_SCHEMA':'HOST_OUTPUT_SCHEMA',
                'OUTPUT_LIMIT':'HOST_OUTPUT_LIMIT','CANCELED':'HOST_CANCELED','INTERRUPTED':'HOST_INTERRUPTED',
                'WAITING':'HOST_WAITING','MISSING_RESULT':'HOST_RESULT_MISSING','PROTOCOL':'HOST_PROTOCOL_ERROR'}.get(kind,'HOST_RESULT_ERROR')
        explanation = {'QUOTA':'官方连接报告额度或速率限制。','AUTH':'官方连接要求重新授权。','MODEL':'官方连接报告模型不可用。',
            'PERMISSION':'宿主拒绝了所需操作，权限保持不变。','TIMEOUT':'官方连接报告超时。','NETWORK':'官方连接报告网络或传输异常。',
            'CONTEXT':'官方连接报告模型上下文范围问题。','OUTPUT_SCHEMA':'官方连接报告结构化结果未完成或格式问题。',
            'OUTPUT_LIMIT':'官方连接报告输出容量问题。','CANCELED':'官方请求被取消。','INTERRUPTED':'官方请求被打断。',
            'WAITING':'宿主仍在等待交互，本次自动步骤未完成。','MISSING_RESULT':'连接结束时没有收到终态结果。',
            'PROTOCOL':'收到的宿主结果结构或数量不符合本轮协议。'}.get(kind,'宿主未给出可确认成功的结果，具体原因尚未明确。')
        error = CoreError(code, explanation + ' 已保留此前完成的版本；本次结果不按成功采用，也不会自动补发。请查看诊断编号，确认现有稿件后再决定是否重试；请求可能已计费。')
        error.diagnostic_details = detail
        return error
