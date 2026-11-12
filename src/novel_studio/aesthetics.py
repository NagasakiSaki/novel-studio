"""Editor aesthetic profiles and source-backed report verification, not scoring."""
from __future__ import annotations

import json
from pathlib import Path
from .core import CoreError, checked_text

FORMAT = "novel-studio-aesthetic-profile-v1"
REVIEW_FORMAT = "novel-studio-review-v1"
FIELDS = (
    ("character", "人物塑造", "人物是否有独立处境、利益和复杂性。", "保护人物灰度与独立性，不要求道德正确"),
    ("motivation", "人物动机", "选择的动机、局限与代价。", "从处境和经历判断，不用主角立场替代人物逻辑"),
    ("structure", "剧情结构", "场景、章节与整体叙事的作用。", "允许慢热、非线性和开放结构"),
    ("causality", "因果逻辑", "因果链是否有依据，区分未知与矛盾。", "缺少材料时先提出问题，不擅自补事实"),
    ("pace", "叙事节奏", "事件、感知、潜台词与关系变化。", "安静场景也可推进，不强制钩子或爽点"),
    ("expression", "文学表达", "语义、修辞和具体阅读效果。", "结合语境诊断，不按禁词表机械替换"),
    ("dialogue", "对话自然度", "对白是否有目的、关系与未说出口的内容。", "不把所有口语差异统一成流畅说明文"),
    ("voices", "人物语言辨识度", "语言是否体现人物经历和当时关系。", "允许矛盾、迟疑、沉默和认知盲区"),
    ("emotion", "情绪感染力", "具体细节、选择和后果如何承载情绪。", "克制和外露都可成立，不强制煽情"),
    ("atmosphere", "氛围塑造", "环境、器物和感官如何服务场景。", "不因描写本身较长就判定无效"),
    ("originality", "创新性", "主题、形式和人物处理是否具有个性。", "不以偏离熟悉套路为缺陷"),
    ("world", "世界观一致性", "与已批准事实、约束及未知信息对照。", "设定冲突交给作者裁决，不擅自统一"),
    ("timeline", "时间线一致性", "事件先后、时间跨度、回忆与叙事时间。", "区分故事时间、叙述顺序和人物知情时间"),
    ("foreshadowing", "伏笔管理", "线索是否铺设、回收或有意保留。", "未回收不自动等于错误，说明承诺与范围"),
    ("readability", "读者可读性", "按作者目标读者判断信息与表达。", "允许成熟题材和理解负担，不自动低幼化"),
)
TEMPLATES = {
    "文学性优先": {"expression": 8, "character": 7},
    "商业可读性优先": {"readability": 8, "pace": 7},
    "剧情逻辑优先": {"causality": 9, "timeline": 8},
    "人物塑造优先": {"character": 9, "motivation": 8},
    "氛围和情绪优先": {"atmosphere": 9, "emotion": 8},
    "悬疑节奏优先": {"foreshadowing": 9, "pace": 8, "causality": 8},
    "自定义审美": {},
}
OPTIONS = {"strictness": "适中", "tolerance": "允许慢热、心理纵深、复杂修辞和有意留白；缺少上下文先说明。",
           "intrusiveness": "提供局部建议，不擅自整篇改写", "required_checks": "人物知情边界\n时间线与因果\n已批准事实冲突\n证据与推测分离",
           "tolerated": "作者有意的矛盾、语体差异和开放结局可保留。", "protected": "不得修改源稿、正式作品、人物正典或追踪记录。"}
CONTRACT = {"format": REVIEW_FORMAT, "instructions": "报告 text 使用本结构的 JSON，不能使用 Markdown 代码围栏。引用当前 review_sources 的 ID/版本和原文行号。引用必须逐字可追溯；缺证据明确承认。",
    "allowed_categories": ["A/objective", "B/craft", "C/preference"], "allowed_severities": ["high", "medium", "low"],
    "schema": {"format": REVIEW_FORMAT, "summary": "审稿范围、缺失资料及总体判断", "findings": [{"category": "A/objective", "title": "问题标题", "severity": "medium",
        "source_document_id": "从本轮来源目录复制", "source_revision": "复制 SHA", "start_line": 1, "end_line": 1, "quote": "该行范围内的原文短引文",
        "basis": "事实依据或审美依据，区分推测", "recommendation": "具体且可选的修改建议", "requires_change": False}]},
    "classification": "A 为可核对的事实/逻辑问题，B 为创作质量判断，C 为审美偏好。不能将 C 伪装成 A；C 不可标必改。权重不等于文学分数。所有建议由作者裁决。"}


def template(name):
    if name not in TEMPLATES:
        raise CoreError("PROFILE_INVALID", "未知审美模板。")
    return {"format": FORMAT, "kind": "editor_aesthetic", "title": name, "fields": {k: default for k, _, _, default in FIELDS},
            "weights": {k: TEMPLATES[name].get(k, 5) for k, _, _, _ in FIELDS}, "options": dict(OPTIONS), "rules": ""}


def validate_profile(value):
    keys = {"format", "kind", "title", "fields", "rules", "weights", "options"}
    if not isinstance(value, dict) or set(value) != keys or value["format"] != FORMAT or value["kind"] != "editor_aesthetic":
        raise CoreError("PROFILE_INVALID", "审美档案结构不正确。")
    checked_text(value["title"], 180)
    checked_text(value["rules"], 18000)
    expected = {r[0] for r in FIELDS}
    if not value["title"].strip() or not isinstance(value["fields"], dict) or set(value["fields"]) != expected or not isinstance(value["weights"], dict) or set(value["weights"]) != expected:
        raise CoreError("PROFILE_INVALID", "审美维度或权重不完整。")
    for text in value["fields"].values():
        checked_text(text, 2400)
    if any(type(n) is not int or not 0 <= n <= 10 for n in value["weights"].values()):
        raise CoreError("PROFILE_INVALID", "审美权重应为 0 到 10 的整数，不是文学评分。")
    if not isinstance(value["options"], dict) or set(value["options"]) != set(OPTIONS) or not isinstance(value["options"]["strictness"], str) or value["options"]["strictness"] not in {"严格", "适中", "宽松"}:
        raise CoreError("PROFILE_INVALID", "审稿选项不完整或严格程度不正确。")
    for text in value["options"].values():
        checked_text(text, 6000)
    return json.loads(json.dumps(value, ensure_ascii=False))


def sources(core, *, _view=None):
    if _view is not None:
        view = core._use_context_view('editor', _view)
        base = Path(view[3]).parent
        return [core._document_row((base / name).as_posix(), 'editor_context', payload)
                for name, payload in view[5].items()
                if '/Materials/' not in name and not name.endswith('/SNAPSHOT.json')]
    _, manifest = core.validated_context("editor")
    prefix = Path(manifest).parent.as_posix() + "/"
    return [r for r in core.documents("editor") if r["area"] == "editor_context" and r["relative"].startswith(prefix) and "/Materials/" not in r["relative"]]


def prepare_report(core, text):
    """Verify citations only. Neither software nor weights certify literary truth."""
    checked_text(text)
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or set(data) != {"format", "summary", "findings"} or data["format"] != REVIEW_FORMAT or not isinstance(data["findings"], list) or len(data["findings"]) > 100:
            raise ValueError
        checked_text(data["summary"], 18000)
    except (ValueError, TypeError, CoreError):
        return "# 独立审稿报告\n\n【引用尚未自动核验】模型未按本轮结构交付。以下保留模型原文，结论和引用均需作者复核。\n\n" + text, {"status": "UNSTRUCTURED", "verified_quotes": 0, "unverified_quotes": None}
    catalog = {r["document_id"]: r for r in sources(core)}
    capability = core.bind("editor")
    cache = {}
    groups = {"A/objective": [], "B/craft": [], "C/preference": []}
    verified = 0
    for number, finding in enumerate(data["findings"], 1):
        fields = {"category", "title", "severity", "source_document_id", "source_revision", "start_line", "end_line", "quote", "basis", "recommendation", "requires_change"}
        if not isinstance(finding, dict) or set(finding) != fields or not isinstance(finding["category"], str) or finding["category"] not in groups or type(finding["requires_change"]) is not bool:
            return "# 独立审稿报告\n\n【结构尚未核验】部分问题字段不完整，保留原始回复供作者复核。\n\n" + text, {"status": "UNSTRUCTURED", "verified_quotes": 0, "unverified_quotes": None}
        try:
            for key in ("title", "quote", "basis", "recommendation"):
                checked_text(finding[key], 18000 if key != "quote" else 1500)
            if finding["severity"] not in {"high", "medium", "low"}:
                raise ValueError
        except (CoreError, ValueError, TypeError):
            return "# 独立审稿报告\n\n【内容尚未核验】模型报告包含不合要求的字段，以下供作者复核。\n\n" + text, {"status": "UNSTRUCTURED", "verified_quotes": 0, "unverified_quotes": None}
        row = catalog.get(finding["source_document_id"]) if isinstance(finding["source_document_id"], str) else None
        evidence = "未核验：来源、版本、行号或逐字引文不匹配。"
        matched = False
        first, last = finding["start_line"], finding["end_line"]
        if row and row["revision"] == finding["source_revision"] and type(first) is int and type(last) is int and 1 <= first <= last and last - first < 500:
            if row["document_id"] not in cache:
                cache[row["document_id"]] = core.dispatch(capability, "read_document", {"document_id": row["document_id"]})
            document = cache[row["document_id"]]
            lines = document["text"].splitlines()
            quote = finding["quote"].replace("\r\n", "\n")
            if document["revision"] == finding["source_revision"] and last <= len(lines) and quote.strip() and quote in "\n".join(lines[first - 1:last]):
                matched = True
                evidence = "逐字引文匹配本轮只读副本。此核验不证明模型判断正确。"
        verified += int(matched)
        preference = finding["category"] == "C/preference"
        required = finding["requires_change"] and not preference
        body = ["### " + str(number) + ". " + finding["title"], "", "模型建议严重程度：" + {"high": "高", "medium": "中", "low": "低"}[finding["severity"]],
                "原文位置：" + (row["title"] + "，第 " + str(first) + "–" + str(last) + " 行" if row else "来源未匹配"), "引用核验：" + evidence,
                "", "原文短引：", "> " + finding["quote"].replace("\n", "\n> "), "", "判断依据：" + finding["basis"], "", "建议：" + finding["recommendation"], "",
                "处置：" + ("模型建议优先修正，最终由作者裁决。" if required else "由作者选择是否采纳。") + (" 属于审美偏好，不是必改事实错误。" if preference else ""), ""]
        if preference and finding["requires_change"]:
            body.append("系统提示：模型曾将审美偏好标为必改，已改为作者自选。\n")
        groups[finding["category"]].append("\n".join(body))
    output = ["# 独立审稿报告", "", "【作者裁决】类别与严重程度是模型判断；引用存在不代表结论已被证实。报告不会修改源稿、正典或追踪。", "", data["summary"], ""]
    for category, title in (("A/objective", "A · 模型指出的客观／连续性问题"), ("B/craft", "B · 创作质量判断"), ("C/preference", "C · 审美偏好（可选）")):
        output += ["## " + title, "", "\n".join(groups[category]) or "本轮未列出此类问题；不表示已经证明没有问题。", ""]
    return "\n".join(output), {"status": "NO_FINDINGS" if not data["findings"] else "QUOTES_MATCHED" if verified == len(data["findings"]) else "PARTIAL", "verified_quotes": verified, "unverified_quotes": len(data["findings"]) - verified,
                                "scope": "citation existence only; classifications and judgements remain model suggestions"}
