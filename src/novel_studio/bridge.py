"""Explicit, bounded stdio adapter. No server, arbitrary paths or author grant."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys
import uuid
from .core import Core, CoreError, READ

FIELDS = {
    "read_document": {"document_id"}, "preview_diff": {"document_id"}, "request_approval": {"document_id"},
    "create_draft": {"text", "dry_run"}, "create_proposal": {"text", "dry_run"},
    "update_draft": {"document_id", "text", "expected_revision", "dry_run"},
    "create_review_report": {"text", "dry_run"}, "set_model": {"model_id"},
    "set_effort": {"effort"}, "test_connection": {"confirmed"},
}


def handle(core, capability, request):
    response = {"protocol_version": "1", "request_id": None, "correlation_id": None, "ok": False, "data": None, "error": None}
    try:
        if not isinstance(request, dict) or set(request) - {"protocol_version", "request_id", "correlation_id", "method", "params", "idempotency_key"}:
            raise CoreError("INVALID_REQUEST", "请求包含未知字段。")
        for field in ("request_id", "correlation_id", "method"):
            if not isinstance(request.get(field), str) or not 1 <= len(request[field]) <= 100:
                raise CoreError("INVALID_REQUEST", "请求编号或接口名称无效。")
        response.update(request_id=request["request_id"], correlation_id=request["correlation_id"])
        if request.get("protocol_version") != "1":
            raise CoreError("VERSION_UNSUPPORTED", "接口版本不兼容。")
        if "idempotency_key" in request and (not isinstance(request["idempotency_key"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request["idempotency_key"])):
            raise CoreError("INVALID_REQUEST", "操作编号无效。")
        method, p = request["method"], request.get("params", {})
        allowed = FIELDS.get(method, set())
        required = allowed - {"dry_run"}
        if not isinstance(p, dict) or set(p) - allowed or required - set(p):
            raise CoreError("INVALID_REQUEST", "接口参数缺失或包含未知字段。")
        for field, value in p.items():
            if field in {"dry_run", "confirmed"}:
                if type(value) is not bool:
                    raise CoreError("INVALID_REQUEST", "确认字段必须为布尔值。")
            elif not isinstance(value, str) and not (field == "effort" and value is None):
                raise CoreError("INVALID_REQUEST", "参数类型不正确。")
        result = core.dispatch(capability, method, p, request.get("idempotency_key"))
        response.update(ok=True, data=result)
    except CoreError as error:
        response["error"] = {"code": error.code, "message": str(error)}
    except (OSError, ValueError, KeyError, TypeError):
        response["error"] = {"code": "LOCAL_STATE_INVALID", "message": "本地资料无法安全读取，请在工作台重新检查作品。"}
    return response


def rpc(core, capability, request):
    """JSON-RPC 2.0 transport around the versioned Core envelope."""
    identifier = request.get("id") if isinstance(request, dict) else None
    if isinstance(request, dict) and request.get("jsonrpc") == "2.0" and "id" not in request:
        return None  # Notifications do not receive a response or a mutation grant.
    if (not isinstance(request, dict) or set(request) - {"jsonrpc", "id", "method", "params"}
            or request.get("jsonrpc") != "2.0" or not isinstance(identifier, (str, int)) or isinstance(identifier, bool)
            or not isinstance(request.get("method"), str) or not isinstance(request.get("params", {}), dict)):
        return {"jsonrpc": "2.0", "id": identifier if isinstance(identifier, (str, int)) else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    params = request.get("params", {})
    if set(params) - {"protocol_version", "correlation_id", "params", "idempotency_key"}:
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32602, "message": "Invalid params"}}
    envelope = {"protocol_version": params.get("protocol_version", "1"), "request_id": str(identifier),
                "correlation_id": params.get("correlation_id", str(identifier)), "method": request["method"],
                "params": params.get("params", {})}
    if "idempotency_key" in params:
        envelope["idempotency_key"] = params["idempotency_key"]
    result = handle(core, capability, envelope)
    if result["ok"]:
        return {"jsonrpc": "2.0", "id": identifier, "result": result}
    code = {"METHOD_NOT_FOUND": -32601, "INVALID_REQUEST": -32602}.get(result["error"]["code"], -32000)
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": result["error"]["message"], "data": result}}


def main():
    parser = argparse.ArgumentParser(description="User-started read-only local stdio bridge")
    parser.add_argument("--project", required=True, type=Path)
    args = parser.parse_args()
    core = Core(args.project)
    from .host import ModelService
    ModelService(core)
    capability = core.bind("observer", READ)
    while True:
        raw = sys.stdin.buffer.readline(1_000_001)
        if not raw:
            break
        if len(raw) > 1_000_000:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Request too large"}}
            print(json.dumps(response), flush=True)
            break
        try:
            response = rpc(core, capability, json.loads(raw))
        except (ValueError, UnicodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if response is None:
            continue
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
