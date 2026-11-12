"""Author-configured local rate estimates. No price scraping or network access."""
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from .core import CoreError, checked_text
from .cache_policy import estimate_cost

RATE_FIELDS = {"input_per_million": "普通输入", "read_per_million": "缓存读取", "output_per_million": "计费输出（含供应商定义的思考）",
    "write_30m_per_million": "OpenAI 写入 · 30 分钟", "write_12h_per_million": "OpenAI 写入 · 12 小时",
    "write_5m_per_million": "Anthropic 写入 · 5 分钟", "write_1h_per_million": "Anthropic 写入 · 1 小时", "write_per_million": "经供应商确认的统一写入费率"}
REASONS = {"PRICE_SOURCE_MISSING": "未配置有来源、日期的模型价格", "BILLING_SCOPE_UNKNOWN": "宿主未提供可核验计费口径", "USAGE_BUCKET_MISSING": "供应商用量字段不完整", "PRICE_BUCKET_MISSING": "本回合所需单价未填写", "WRITE_TTL_SCOPE_UNKNOWN": "缺少写入期限明细，不能套用某个期限单价", "INVALID_PRICE": "价格格式不正确"}


class CostService:
    def __init__(self, core):
        self.core = core

    @staticmethod
    def key(provider, model):
        if provider not in {"openai", "anthropic", "gemini"} or not isinstance(model, str) or not model.strip() or len(model) > 200 or any(c.isspace() for c in model):
            raise CoreError("PRICE_INVALID", "请选择官方 API 供应商并填写准确模型 ID。")
        return provider + ":" + model

    def prices(self, provider, model):
        return self.core.creative.state().get("model_prices", {}).get(self.key(provider, model))

    def save(self, provider, model, value):
        key = self.key(provider, model)
        if not isinstance(value, dict) or set(value) - set(RATE_FIELDS) - {"source", "as_of", "currency", "write_rate_scope"}:
            raise CoreError("PRICE_INVALID", "价格包含不支持的字段。")
        for field in ("source", "as_of", "currency"):
            checked_text(value.get(field), 600)
        if not value["source"].strip() or not re.fullmatch(r"[A-Z]{3}", value["currency"]):
            raise CoreError("PRICE_INVALID", "请填写价格来源、查询日期和三字母币种；不进行汇率换算。")
        try:
            date.fromisoformat(value["as_of"])
            validated = {key: value[key] for key in ("source", "as_of", "currency")}
            for field in RATE_FIELDS:
                if value.get(field) in {None, ""}:
                    continue
                rate = Decimal(str(value[field]))
                if not rate.is_finite() or not 0 <= rate <= 1_000_000:
                    raise ValueError()
                validated[field] = str(rate)
            scope = value.get("write_rate_scope")
            if scope not in {None, "uniform_all_ttl"}:
                raise ValueError()
            if scope:
                validated["write_rate_scope"] = scope
        except (InvalidOperation, ValueError, TypeError):
            raise CoreError("PRICE_INVALID", "日期或单价不正确。未知单价请留空，不要填零代替。") from None
        return self.core.creative._mutate(lambda tree, state: state.setdefault("model_prices", {}).update({key: validated}))

    def report(self, role):
        rows = [r for r in self.core._state().get("api_runs", []) if r.get("role") == role]
        values = []
        for row in rows:
            estimate = estimate_cost(row.get("usage", {}), self.prices(row["provider"], row["model_id"]))
            values.append({"run_id": row["run_id"], "provider": row["provider"], "model": row["model_id"], "diagnostic": bool(row.get("diagnostic")), "status": row.get("status"), **estimate})
        groups = {}
        for value in values:
            currency = value.get("currency", "UNKNOWN")
            group = groups.setdefault(currency, {"known_subtotal": Decimal(0), "known": 0, "unknown": 0})
            if value["estimated_cost"] is None:
                group["unknown"] += 1
            else:
                group["known_subtotal"] += Decimal(value["estimated_cost"])
                group["known"] += 1
        return {"rows": values, "groups": {key: {**value, "known_subtotal": str(value["known_subtotal"])} for key, value in groups.items()},
            "scope": "当前项目保存的最多 500 条 API 任务（含独立分析诊断及失败请求）；固定短句连接测试在本机模型诊断单列。未知项不按零合计，不换算币种。",
            "invoice": "按当前作者填写的固定单价回算，不是历史账单或供应商实扣；不自动匹配长上下文阶梯价、折扣、批处理/加速费率，不含缓存存储、税费、订阅和额外工具收费。"}
