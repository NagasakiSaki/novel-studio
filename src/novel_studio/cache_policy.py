"""Provider-specific cache controls and conservative, source-labelled accounting."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from .core import CoreError


def number(value):
    return value if type(value) is int and value >= 0 else None


def total(values):
    return sum(values) if all(v is not None for v in values) else None


def normalize_usage(provider, raw, *, endpoint=None, legacy_openai=False):
    """One final API response, never sum incremental streaming snapshots here."""
    data = dict(raw) if isinstance(raw, dict) else {}
    result = {"provider": provider, "endpoint": endpoint, "input_tokens": None, "output_tokens": None,
              "thinking_tokens": None, "cache_read_tokens": None, "cache_write_tokens": None,
              "uncached_input_tokens": None, "total_tokens": None, "cache_read_share": None,
              "provider_hit_rate": None, "write_applicable": None, "issues": [], "scope": "PER_RESPONSE"}
    if provider == "openai":
        details = data.get("input_tokens_details") or {}
        output = data.get("output_tokens_details") or {}
        result.update(input_tokens=number(data.get("input_tokens")), output_tokens=number(data.get("output_tokens")),
                      thinking_tokens=number(output.get("reasoning_tokens")), total_tokens=number(data.get("total_tokens")),
                      cache_read_tokens=number(details.get("cached_tokens")), cache_write_tokens=number(details.get("cache_write_tokens")),
                      write_applicable=not legacy_openai)
        result["cache_write_by_ttl"] = {k: number(v) for k, v in (details.get("cache_write_tokens_details") or {}).items() if k in {"ttl_30m_tokens", "ttl_12h_tokens"}}
        parts = [result["input_tokens"], result["cache_read_tokens"]]
        written = 0 if legacy_openai else result["cache_write_tokens"]
        if all(v is not None for v in parts) and written is not None:
            result["uncached_input_tokens"] = parts[0] - parts[1] - written
    elif provider == "anthropic":
        uncached = number(data.get("input_tokens"))
        read, write = number(data.get("cache_read_input_tokens")), number(data.get("cache_creation_input_tokens"))
        result.update(input_tokens=total([uncached, read, write]), uncached_input_tokens=uncached,
                      cache_read_tokens=read, cache_write_tokens=write, output_tokens=number(data.get("output_tokens")), write_applicable=True)
        result["thinking_tokens"] = number((data.get("output_tokens_details") or {}).get("reasoning_tokens"))
        result["cache_write_by_ttl"] = {k: number(v) for k, v in (data.get("cache_creation") or {}).items() if k in {"ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"}}
        result["total_tokens"] = total([result["input_tokens"], result["output_tokens"]])
    elif provider == "gemini":
        interaction = endpoint == "interactions"
        fields = ("total_input_tokens", "total_output_tokens", "total_thought_tokens", "total_cached_tokens", "total_tokens") if interaction else ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "cachedContentTokenCount", "totalTokenCount")
        result.update(zip(("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"), (number(data.get(k)) for k in fields)))
        result["write_applicable"] = False  # Separate explicit-resource billing is not inference usage.
        # Gemini reports response and thought tokens separately. Preserve both
        # raw fields; only the derived billing count adds them, once.
        result["billing_output_tokens"] = total([result["output_tokens"], result["thinking_tokens"]]) if endpoint in {"interactions", "generateContent"} else None
        if result["input_tokens"] is not None and result["cache_read_tokens"] is not None:
            result["uncached_input_tokens"] = result["input_tokens"] - result["cache_read_tokens"]
    elif provider == "antigravity":
        result.update({key: number(data.get(key)) for key in ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens")})
        result.update(scope="RAW_SESSION_COUNTER; normalize known CID deltas separately", write_applicable=None)
    else:
        raise CoreError("PROVIDER_UNSUPPORTED", "未知供应商，无法解释其统计口径。")
    if result["uncached_input_tokens"] is not None and result["uncached_input_tokens"] < 0:
        result["issues"].append("INCONSISTENT_INPUT_BUCKETS")
        result["uncached_input_tokens"] = None
    if provider != "antigravity" and result["input_tokens"] is not None and result["input_tokens"] > 0 and result["cache_read_tokens"] is not None:
        if result["cache_read_tokens"] <= result["input_tokens"]:
            result["cache_read_share"] = result["cache_read_tokens"] / result["input_tokens"]
        else:
            result["issues"].append("CACHE_READ_EXCEEDS_INPUT")
    return result


def merge_usage(rows):
    """Sum distinct calls in one client task; missing data never becomes zero."""
    keys = ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "cache_write_tokens", "uncached_input_tokens", "total_tokens", "billing_output_tokens")
    if not rows:
        return {key: None for key in keys}
    merged = {key: total([r.get(key) for r in rows]) for key in keys}
    merged.update(provider=rows[0]["provider"], scope="DISTINCT_API_CALLS_IN_ONE_TASK", calls=len(rows), provider_hit_rate=None,
                  write_applicable=rows[0].get("write_applicable"), cache_read_share=None)
    if merged["input_tokens"] and merged["cache_read_tokens"] is not None and all(r.get("cache_read_share") is not None for r in rows):
        merged["cache_read_share"] = merged["cache_read_tokens"] / merged["input_tokens"]
    complete = all(r.get("cache_write_tokens") == 0 or isinstance(r.get("cache_write_by_ttl"), dict) and r["cache_write_by_ttl"] and all(number(v) is not None for v in r["cache_write_by_ttl"].values()) and sum(r["cache_write_by_ttl"].values()) == r.get("cache_write_tokens") for r in rows)
    if complete:
        keys = set().union(*(r.get("cache_write_by_ttl", {}) for r in rows))
        merged["cache_write_by_ttl"] = {key: sum(r.get("cache_write_by_ttl", {}).get(key, 0) for r in rows) for key in sorted(keys)}
    return merged


def estimate_cost(usage, prices):
    """Configured rates are an estimate, never an invoice or free missing bucket."""
    if not prices or not prices.get("source") or not prices.get("as_of") or not prices.get("currency"):
        return {"estimated_cost": None, "reason": "PRICE_SOURCE_MISSING"}
    mapping = {"uncached_input_tokens": "input_per_million", "cache_read_tokens": "read_per_million", "output_tokens": "output_per_million"}
    quantities = {key: usage.get(key) for key in mapping}
    if usage.get("provider") == "gemini":
        quantities["output_tokens"] = usage.get("billing_output_tokens")
    if usage.get("write_applicable") is True:
        written, breakdown = usage.get("cache_write_tokens"), usage.get("cache_write_by_ttl", {})
        ttl_rates = {"ttl_30m_tokens": "write_30m_per_million", "ttl_12h_tokens": "write_12h_per_million", "ephemeral_5m_input_tokens": "write_5m_per_million", "ephemeral_1h_input_tokens": "write_1h_per_million"}
        if number(written) is None:
            return {"estimated_cost": None, "reason": "USAGE_BUCKET_MISSING"}
        if written:
            if isinstance(breakdown, dict) and breakdown and set(breakdown) <= set(ttl_rates) and all(number(v) is not None for v in breakdown.values()) and sum(breakdown.values()) == written:
                mapping.update({key: ttl_rates[key] for key in breakdown})
                quantities.update(breakdown)
            elif prices.get("write_rate_scope") == "uniform_all_ttl":
                mapping["cache_write_tokens"] = "write_per_million"
                quantities["cache_write_tokens"] = written
            else:
                return {"estimated_cost": None, "reason": "WRITE_TTL_SCOPE_UNKNOWN"}
    elif usage.get("write_applicable") is None:
        return {"estimated_cost": None, "reason": "BILLING_SCOPE_UNKNOWN"}
    cost = Decimal(0)
    try:
        for bucket, rate_name in mapping.items():
            quantity = quantities.get(bucket)
            if number(quantity) is None:
                return {"estimated_cost": None, "reason": "USAGE_BUCKET_MISSING"}
            if quantity == 0:
                continue
            if rate_name not in prices:
                return {"estimated_cost": None, "reason": "PRICE_BUCKET_MISSING"}
            rate = Decimal(str(prices[rate_name]))
            if not rate.is_finite() or rate < 0:
                raise InvalidOperation
            cost += Decimal(quantity) * rate / Decimal(1_000_000)
    except (InvalidOperation, ValueError, TypeError):
        return {"estimated_cost": None, "reason": "INVALID_PRICE"}
    return {"estimated_cost": str(cost), "currency": prices["currency"], "source": prices["source"], "as_of": prices["as_of"],
            "actual_invoice_cost": None, "scope": "configured per-token inference estimate; storage/taxes/other extras excluded"}


def capabilities(provider, *, verified=False, endpoint=None, modern_openai=False):
    result = {"implicit": None, "explicit": None, "breakpoints": None, "ttl_values": [], "cache_read": True,
              "cache_write": None, "provider_hit_rate": False, "actual_cost": False,
              "source": "official documentation checked 2026-11-05; model capability unverified"}
    if provider == "antigravity":
        result.update(implicit=None, explicit=False, breakpoints=False, cache_write=False, source="CLI 1.5.0 observed usage; provider-managed cache, no public TTL control")
    elif verified and provider == "anthropic":
        result.update(implicit=False, explicit=True, breakpoints=True, ttl_values=["5m", "1h"], cache_write=True, source="active official Messages model + current cache documentation")
    elif verified and provider == "openai":
        result.update(implicit=True, explicit=modern_openai, breakpoints=modern_openai, cache_write=modern_openai,
                      ttl_values=["30m", "12h"] if modern_openai else [], source="official model documentation annotation; not inferred from model display name")
    elif verified and provider == "gemini":
        result.update(implicit=True, explicit=False if endpoint == "interactions" else None, breakpoints=False,
                      cache_write=False, source="official endpoint documentation; explicit generateContent resources require separate verification")
    return result


def validate_policy(provider, selected, declared):
    mode = (selected or {}).get("mode", "provider_default")
    ttl = (selected or {}).get("ttl")
    if mode == "provider_default":
        if ttl:
            raise CoreError("CACHE_POLICY_INVALID", "官方默认模式不应附加未经选择的 TTL。")
        return {"mode": mode, "native": {}, "managed": True}
    if provider == "antigravity":
        raise CoreError("CACHE_UNSUPPORTED", "Antigravity 的缓存由官方宿主管理，不能设置断点或 TTL。")
    if mode not in {"explicit", "implicit"} or declared.get(mode) is not True:
        raise CoreError("CACHE_CAPABILITY_UNVERIFIED", "当前模型/接口未确认支持所选缓存控制，请选择官方默认或先核验能力。")
    if ttl is not None and ttl not in declared.get("ttl_values", []):
        raise CoreError("CACHE_TTL_UNSUPPORTED", "所选 TTL 不属于此供应商和模型已核验的能力。")
    return {"mode": mode, "ttl": ttl, "managed": False}
