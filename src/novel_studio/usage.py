"""Raw official terminal-result accounting, with unknown semantics kept unknown."""
from __future__ import annotations


FIELDS = ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens")


def snapshot(usage):
    return {key: value if type(value := usage.get(key)) is int and value >= 0 else None for key in FIELDS}


def summarize(runs, role):
    selected = [r for r in runs if role == "observer" or r.get("role") == role]
    # One persisted terminal record per client run UUID. Replayed final/checkpoint
    # events cannot multiply usage. No subtraction across CIDs/models/epochs.
    unique = {r["run_id"]: r for r in selected}
    values = list(unique.values())
    previous = {}
    enriched = []
    for row in values:
        raw = snapshot(row.get("usage", {}))
        cid = (row.get("role"), row.get("cid"))
        old = previous.get(cid)
        current = {key: None for key in FIELDS}
        scope = "BASELINE_MISSING"
        if old is not None:
            before = snapshot(old.get("usage", {}))
            comparable = all(raw[k] is None or before[k] is None or raw[k] >= before[k] for k in FIELDS)
            turns_forward = type(row.get("num_turns")) is int and type(old.get("num_turns")) is int and row["num_turns"] > old["num_turns"]
            if comparable and turns_forward:
                current = {k: raw[k] - before[k] if raw[k] is not None and before[k] is not None else None for k in FIELDS}
                scope = "SESSION_COUNTER_DELTA"
            else:
                scope = "COUNTER_RESET_OR_REPLAY; new_baseline"
        elif row.get("num_turns") == 1:
            current, scope = raw, "FIRST_SESSION_TURN"
        previous[cid] = row
        enriched.append({**row, "current_turn": current, "scope": scope})
    creative = [r for r in enriched if not r.get("diagnostic")]
    values = creative or enriched
    latest = values[-1] if values else {}
    return {"source": "official_cli_result", "scope": latest.get("scope", "NOT_AVAILABLE"),
            "latest": latest.get("current_turn", snapshot({})), "raw_session_counter": snapshot(latest.get("usage", {})),
            "reported_run_count": len(values), "diagnostic_run_count": sum(bool(r.get("diagnostic")) for r in enriched),
            "cumulative": snapshot(latest.get("usage", {})) if latest else None,
            "uncached_input_tokens": None, "hit_rate": None,
            "availability": "会话累计来自官方结果；本回合按同一 CID 的已知基线求差。计数重置或缺失时不推算。缓存分母未确认，命中率保持空缺。",
            "latency_seconds": latest.get("latency_seconds"),
            "trend": [{"run_id": r["run_id"], "model": r.get("requested_model"),
                       "cache_read_tokens": r["current_turn"]["cache_read_tokens"], "scope": r["scope"]} for r in values[-30:]]}
