"""Direct official SDK adapters. No configurable endpoints, proxy or agent I/O."""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import re
import ssl
import time
from .core import CoreError, checked_text
from .cache_policy import normalize_usage, capabilities, validate_policy
from .model_limits import apply_limits, positive_limit, MAX_TOKEN_PARAMETER
from .prompting import canonical, MAX_PROMPT_BYTES, context_size_error

DESTINATIONS = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com",
                "gemini": "https://generativelanguage.googleapis.com"}
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}")
ROUTES = {"openai": {"GET": r"/v1/models(?:/[A-Za-z0-9._:-]+)?", "POST": r"/v1/responses"},
          "anthropic": {"GET": r"/v1/models(?:/[A-Za-z0-9._:-]+)?", "POST": r"/v1/messages"},
          "gemini": {"GET": r"/v1beta/models(?:/[A-Za-z0-9._-]+)?", "POST": r"/v1beta/(?:interactions|models/[A-Za-z0-9._-]+:generateContent)"}}


def model_id(value):
    if not isinstance(value, str) or not MODEL_ID.fullmatch(value):
        raise CoreError("MODEL_INVALID", "模型 ID 格式不正确，请使用供应商官方目录中的准确 ID。")
    return value


def plain(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return value


def mute_sdk_logging():
    # Do not let SDK environment debug flags route request bodies/headers to root
    # loggers. Local usage/error enums are recorded by our own boundary instead.
    for name in ("openai", "anthropic", "google.genai", "google_genai", "httpx", "httpx2", "httpcore", "httpcore2"):
        logger = logging.getLogger(name)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        logger.setLevel(logging.CRITICAL + 1)


class RequestBoundary:
    def __init__(self, provider, secret):
        if provider not in DESTINATIONS:
            raise CoreError("PROVIDER_UNSUPPORTED", "仅支持界面中列出的官方供应商。")
        self.provider, self.secret = provider, secret
        self.host = DESTINATIONS[provider].split("/")[2]
        self.remaining_posts = 0

    def request(self, request):
        url = request.url
        if url.scheme != "https" or url.host != self.host or url.port not in (None, 443) or url.username or url.password or \
                not re.fullmatch(ROUTES[self.provider].get(request.method, r"(?!)"), url.path) or "%" in str(url).split("?", 1)[0]:
            raise CoreError("NETWORK_SCOPE", "已阻止超出供应商官方模型接口范围的请求。")
        if any(k.lower() in {"key", "api_key", "api-key", "access_token"} for k in request.url.params):
            raise CoreError("NETWORK_SCOPE", "密钥不得出现在请求地址中。")
        if request.method == "POST":
            if self.remaining_posts <= 0:
                raise CoreError("API_RETRY_BLOCKED", "已阻止 SDK 在一次明确调用内补发收费请求；请检查官方结果后手动重试。")
            self.remaining_posts -= 1
        allowed = {"host", "content-type", "content-length", "accept", "accept-encoding", "connection", "user-agent", "anthropic-version", "anthropic-beta", "x-goog-api-client"}
        headers = {k: v for k, v in request.headers.items() if k.lower() in allowed}
        request.headers.clear()
        request.headers.update(headers)
        request.headers[{"openai": "authorization", "anthropic": "x-api-key", "gemini": "x-goog-api-key"}[self.provider]] = ("Bearer " if self.provider == "openai" else "") + self.secret

    @staticmethod
    def response(response):
        if 300 <= response.status_code < 400:
            raise CoreError("REDIRECT_BLOCKED", "官方接口返回了重定向；为保护密钥，本次连接已停止。")
        if response.status_code >= 400:
            # Fail at the transport boundary before SDK retry/backoff handlers
            # inspect a raw error body or schedule another billable request.
            status = response.status_code
            if status in (401, 403):
                raise CoreError("API_AUTH", "官方服务未授权本次请求，请检查密钥和账号权限。")
            if status == 429:
                raise CoreError("API_RATE_LIMIT", "官方服务报告额度或速率限制；本次已停止，没有补发请求。")
            if status == 404:
                raise CoreError("API_MODEL_UNAVAILABLE", "官方服务未找到所选模型或接口，请检查模型目录和账号资格。")
            if status in (400, 422):
                raise CoreError("API_PARAMETER", "官方服务拒绝了参数或上下文；未静默删除参数或补发。")
            raise CoreError("API_SERVICE_ERROR", "官方服务返回错误，本次已停止，没有自动重试。")


def safe_error(error):
    if isinstance(error, CoreError):
        return error
    cause = error
    for _ in range(5):
        cause = getattr(cause, "__cause__", None) or getattr(cause, "__context__", None)
        if cause is None:
            break
        if isinstance(cause, CoreError):
            return cause
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    name = type(error).__name__.lower()
    if status in (401, 403):
        return CoreError("API_AUTH", "官方服务未授权本次请求，请检查密钥和账号权限。没有自动换号或降级。")
    if status == 429:
        return CoreError("API_RATE_LIMIT", "官方服务报告额度或速率限制；没有自动重试，请在供应商账户中检查。")
    if status == 404:
        return CoreError("API_MODEL_UNAVAILABLE", "官方服务未找到所选模型或接口，请刷新模型目录并检查账号资格。")
    if status in (400, 422):
        return CoreError("API_PARAMETER", "官方服务拒绝了参数或上下文，请检查所选模型能力和请求大小。未静默删除参数。")
    if "timeout" in name:
        return CoreError("API_TIMEOUT", "请求超时。服务端可能已经处理并计费，请先检查，不会自动补发。")
    return CoreError("API_CONNECTION", "官方 API 请求未完成，请检查网络或稍后手动重试。原始异常内容不会写入日志。")


@contextmanager
def official_client(provider, secret, *, transport=None):
    """Injected transport is for tests; it is never accepted from a project/tool."""
    if provider not in DESTINATIONS:
        raise CoreError("PROVIDER_UNSUPPORTED", "未知供应商。")
    mute_sdk_logging()
    import certifi
    import httpx
    import httpx2
    boundary = RequestBoundary(provider, secret)
    module = httpx if provider == "gemini" else httpx2
    http = module.Client(timeout=module.Timeout(120, connect=15), trust_env=False, follow_redirects=False,
                         verify=ssl.create_default_context(cafile=certifi.where()), transport=transport,
                         event_hooks={"request": [boundary.request], "response": [boundary.response]})
    client = None
    try:
        if provider == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=secret, base_url=DESTINATIONS[provider], http_client=http, max_retries=0,
                            organization="", project="", admin_api_key="", webhook_secret="")
        elif provider == "anthropic":
            from anthropic import Anthropic
            client = Anthropic(api_key=secret, base_url=DESTINATIONS[provider], http_client=http, max_retries=0,
                               open_telemetry=False, webhook_key="")
        else:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=secret, enterprise=False, vertexai=False,
                debug_config=genai.client.DebugConfig(client_mode=None, replays_directory=None, replay_id=None),
                http_options=types.HttpOptions(base_url=DESTINATIONS[provider], api_version="v1beta", timeout=120000,
                    httpx_client=http, retry_options=types.HttpRetryOptions(attempts=0)))
        yield OfficialSession(client, boundary)
    except Exception as error:
        raise safe_error(error) from None
    finally:
        if client is not None:
            client.close()
        http.close()


class OfficialSession:
    def __init__(self, native, boundary):
        self.native, self.boundary = native, boundary

    def __getattr__(self, name):
        return getattr(self.native, name)

    def start_inference(self):
        # Our own transport budget remains authoritative even if an SDK version
        # changes the interpretation of retry/continuation configuration.
        self.boundary.remaining_posts = 1


def discover(provider, client):
    """Only public model metadata. No generated sample or automatic inference."""
    rows = []
    catalog = client.models.list() if provider == "openai" else client.models.list(limit=100) if provider == "anthropic" else client.models.list(config={"page_size": 100})
    for item in catalog:
        data = plain(item)
        name = data.get("id") if provider != "gemini" else data.get("name", "").removeprefix("models/")
        if not isinstance(name, str) or not MODEL_ID.fullmatch(name):
            continue
        row = {"provider": provider, "model_id": name, "display_name": data.get("display_name") or data.get("displayName") or name,
               "metadata_source": "official models API", "max_input_tokens": None, "max_output_tokens": None,
               "effort_levels": [], "thinking": None, "cache": capabilities(provider)}
        if provider == "anthropic":
            cap = data.get("capabilities") or {}
            effort = cap.get("effort") or {}
            row.update(max_input_tokens=data.get("max_input_tokens"), max_output_tokens=data.get("max_tokens"),
                       effort_levels=[level for level in ("low", "medium", "high", "xhigh", "max") if (effort.get(level) or {}).get("supported")] if effort.get("supported") else [], thinking=cap.get("thinking"),
                       cache=capabilities(provider, verified=True))
            row["lifecycle"] = data.get("lifecycle")
        elif provider == "gemini":
            row.update(max_input_tokens=data.get("input_token_limit"), max_output_tokens=data.get("output_token_limit"),
                       thinking=data.get("thinking"), supported_actions=data.get("supported_actions"),
                       cache=capabilities(provider, verified=True, endpoint="interactions"))
        rows.append(apply_limits(provider, name, row))
        if len(rows) >= 2000:
            raise CoreError("CATALOG_LIMIT", "官方模型目录超出本地安全上限，请缩小范围后重试。")
    return sorted(rows, key=lambda r: r["model_id"])


def parameter_config(provider, config, capability):
    model_id(config.get("model_id"))
    capability = apply_limits(provider, config['model_id'], capability)
    if capability.get("lifecycle") == "retired":
        raise CoreError("MODEL_RETIRED", "官方模型目录标记此模型已退役，请另选模型。")
    result = {}
    maximum = config.get("max_output_tokens")
    known_maximum = positive_limit(capability.get('max_output_tokens'))
    if maximum is None:
        maximum = known_maximum
        if maximum is None and provider == 'anthropic':
            raise CoreError('CAPABILITY_UNKNOWN', '官方目录尚未提供此模型的最大输出，请先刷新官方模型目录，或明确填写官方支持的值；程序不会再默认限制为4096。')
    if maximum is not None:
        if type(maximum) is not int or not 1 <= maximum <= MAX_TOKEN_PARAMETER or known_maximum is not None and maximum > known_maximum:
            raise CoreError("PARAMETER_INVALID", "输出上限超过当前模型已知范围。")
        result["max_output_tokens"] = maximum
    effort = config.get("effort")
    if effort:
        if effort not in capability.get("effort_levels", []):
            raise CoreError("CAPABILITY_UNKNOWN", "此模型的思考档位尚未由官方能力数据核验，不能静默发送。")
        result["effort"] = effort
    temperature = config.get("temperature")
    if temperature is not None:
        if capability.get("temperature") is not True:
            raise CoreError("CAPABILITY_UNKNOWN", "此模型的温度控制能力尚未核验。")
        if type(temperature) not in {int, float} or not 0 <= temperature <= 2:
            raise CoreError("PARAMETER_INVALID", "温度需在已支持的范围内。")
        result["temperature"] = temperature
    result["cache"] = validate_policy(provider, config.get("cache"), capability.get("cache", capabilities(provider)))
    return result


class NativeTurn:
    """Stateful within one local conversation, stateless at vendor storage layer.

    Exact native output/signature/tool-result sequence is retained, not reduced
    to UI explanation strings. Never transfer it between providers or models.
    """
    def __init__(self, provider, client, compiled, config, capability, messages=()):
        self.provider, self.client, self.compiled = provider, client, compiled
        self.config, self.params = config, parameter_config(provider, config, capability)
        self.messages = json.loads(json.dumps(list(messages), ensure_ascii=False))
        self.usage = []
        self.attempts = 0
        self.response_ids = []
        self.messages.append({"role": "user", "content": compiled.dynamic_text} if provider != "gemini" else
                             {"type": "user_input", "content": [{"type": "text", "text": compiled.dynamic_text}]})

    def start_request(self, kwargs):
        size = len(canonical(kwargs).encode('utf-8'))
        if size > MAX_PROMPT_BYTES:
            raise context_size_error(size, 'api_request')
        self.client.start_inference()
        self.attempts += 1

    def request(self):
        p, c, params = self.provider, self.compiled, self.params
        definitions = list(c.tools)
        maximum = params.get("max_output_tokens")
        effort = params.get("effort")
        policy = params["cache"]
        started = time.monotonic()
        if p == "openai":
            kwargs = {"model": self.config["model_id"], "store": False, "include": ["reasoning.encrypted_content"],
                      "input": [{"role": "developer", "content": [{"type": "input_text", "text": c.stable_text}]}] + self.messages,
                      "tools": [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["parameters"], "strict": True} for t in definitions]}
            if maximum is not None:
                kwargs["max_output_tokens"] = maximum
            if effort:
                kwargs["reasoning"] = {"effort": effort}
            if "temperature" in params:
                kwargs["temperature"] = params["temperature"]
            if policy["mode"] != "provider_default":
                kwargs["prompt_cache_options"] = {"mode": policy["mode"], **({"ttl": policy["ttl"]} if policy.get("ttl") else {})}
                if policy["mode"] == "explicit":
                    kwargs["input"][0]["content"][0]["prompt_cache_breakpoint"] = {"mode": "explicit"}
            self.start_request(kwargs)
            response = plain(self.client.responses.create(**kwargs))
            usage = normalize_usage(p, response.get("usage"), legacy_openai=self.config.get("legacy_cache") is True)
            self.usage.append(usage)
            if response.get("status") not in (None, "completed"):
                raise CoreError("API_INCOMPLETE", "官方回复未完整结束，未保存为正文产物。")
            steps = response.get("output", [])
            self.messages.extend(steps)
            calls = [{"id": s["call_id"], "name": s["name"], "arguments": json.loads(s["arguments"])} for s in steps if s.get("type") == "function_call"]
            text = "\n".join(b.get("text", "") for s in steps if s.get("type") == "message" for b in s.get("content", []) if b.get("type") == "output_text")
        elif p == "anthropic":
            kwargs = {"model": self.config["model_id"], "max_tokens": maximum,
                      "system": [{"type": "text", "text": c.stable_text}], "messages": self.messages,
                      "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in definitions]}
            if effort:
                kwargs["output_config"] = {"effort": effort}
            if "temperature" in params:
                kwargs["temperature"] = params["temperature"]
            if policy["mode"] == "explicit":
                kwargs["system"][0]["cache_control"] = {"type": "ephemeral", **({"ttl": policy["ttl"]} if policy.get("ttl") else {})}
            self.start_request(kwargs)
            response = plain(self.client.messages.create(**kwargs))
            usage = normalize_usage(p, response.get("usage"))
            self.usage.append(usage)
            if response.get("stop_reason") not in ("end_turn", "tool_use", "stop_sequence"):
                raise CoreError("API_INCOMPLETE", "官方回复被截断或未完成，未保存正文产物。")
            steps = response.get("content", [])
            self.messages.append({"role": "assistant", "content": steps})
            calls = [{"id": s["id"], "name": s["name"], "arguments": s["input"]} for s in steps if s.get("type") == "tool_use"]
            text = "\n".join(s["text"] for s in steps if s.get("type") == "text")
        else:
            generation = {}
            if maximum:
                generation["max_output_tokens"] = maximum
            if effort:
                generation["thinking_level"] = effort
            if "temperature" in params:
                generation["temperature"] = params["temperature"]
            kwargs = dict(model=self.config["model_id"], store=False, automatic_continuation=False,
                system_instruction=c.stable_text, input=self.messages, generation_config=generation,
                tools=[{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["parameters"]} for t in definitions])
            self.start_request(kwargs)
            response = plain(self.client.interactions.create(**kwargs))
            usage = normalize_usage(p, response.get("usage"), endpoint="interactions")
            self.usage.append(usage)
            if response.get("status") not in (None, "completed", "requires_action"):
                raise CoreError("API_INCOMPLETE", "官方交互未完整结束，没有保存产物。")
            steps = response.get("steps", response.get("outputs", []))
            self.messages.extend(steps)
            calls = [{"id": s["id"], "name": s["name"], "arguments": s.get("arguments", {})} for s in steps if s.get("type") == "function_call"]
            text = "\n".join(b.get("text", "") for s in steps if s.get("type") == "model_output" for b in s.get("content", []) if b.get("type") == "text")
        if response.get("id"):
            self.response_ids.append(response["id"])
        return {"calls": calls, "text": checked_text(text), "usage": usage, "latency_seconds": time.monotonic() - started,
                "model": response.get("model")}

    def results(self, results):
        if self.provider == "openai":
            self.messages.extend({"type": "function_call_output", "call_id": r["id"], "output": json.dumps(r["result"], ensure_ascii=False)} for r in results)
        elif self.provider == "anthropic":
            self.messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": r["id"], "content": json.dumps(r["result"], ensure_ascii=False)} for r in results]})
        else:
            self.messages.extend({"type": "function_result", "call_id": r["id"], "result": json.dumps(r["result"], ensure_ascii=False)} for r in results)
