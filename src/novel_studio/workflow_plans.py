"""Chapter plans and bounded draft repair; never Canonical authority."""
from .core import CoreError, checked_text
from .workflow_planning import object_schema, text_fields

PLAN_FIELDS = ("volume_title", "title", "plot_goal", "events", "character_actions", "emotional_change",
               "world_facts", "continuity", "ending_goal", "length_reason")
PLAN_SCHEMA = object_schema({**text_fields(PLAN_FIELDS), "volume": {"type": "integer"}, "chapter": {"type": "integer"},
    "word_range": object_schema({"minimum": {"type": "integer"}, "target": {"type": "integer"}, "maximum": {"type": "integer"}})})
REPAIR_FIELDS = ("diagnosis", "preserve", "changes", "acceptance_criteria", "author_question")
REPAIR_SCHEMA = object_schema({**text_fields(REPAIR_FIELDS), "requires_author_decision": {"type": "boolean"}})
PLAN_LABELS = {"volume_title": "卷名建议", "title": "章名建议", "plot_goal": "剧情目标", "events": "核心事件",
    "character_actions": "人物行动", "emotional_change": "情绪变化", "world_facts": "世界观事实", "continuity": "连续性要求",
    "ending_goal": "结尾叙事目标", "length_reason": "篇幅依据"}
REPAIR_LABELS = {"diagnosis": "问题原因", "preserve": "必须保留", "changes": "具体修改", "acceptance_criteria": "复审标准", "author_question": "需要作者决定"}


def validate_plan(value, chapter):
    if not isinstance(value, dict) or set(value) != set(PLAN_SCHEMA["properties"]):
        raise CoreError("PLAN_INVALID", "责编未交付完整章节计划；响应已保留，可查看后重试，不会跳过规划写正文。")
    if type(value["volume"]) is not int or type(value["chapter"]) is not int or (value["volume"], value["chapter"]) != (chapter["volume"], chapter["number"]):
        raise CoreError("PLAN_INVALID", "责编返回的卷章位置与当前任务不同，未采用。")
    for key in PLAN_FIELDS:
        checked_text(value[key], 6000)
        if key not in {"title", "volume_title"} and not value[key].strip():
            raise CoreError("PLAN_INVALID", "责编章节计划缺少“" + PLAN_LABELS[key] + "”，请重新生成完整计划。")
    words = value["word_range"]
    if not isinstance(words, dict) or set(words) != {"minimum", "target", "maximum"} or any(type(n) is not int for n in words.values()) or not 1 <= words["minimum"] <= words["target"] <= words["maximum"] <= 100_000:
        raise CoreError("PLAN_INVALID", "责编篇幅建议范围不正确；未要求作者手工填写章节计划。")
    return value


def validate_repair(value):
    if not isinstance(value, dict) or set(value) != set(REPAIR_SCHEMA["properties"]) or type(value["requires_author_decision"]) is not bool:
        raise CoreError("REPAIR_INVALID", "责编修订方案结构不完整，原稿与响应已保留。")
    for key in REPAIR_FIELDS:
        checked_text(value[key], 10_000)
        if key != "author_question" and not value[key].strip():
            raise CoreError("REPAIR_INVALID", "修订方案需要说明原因、保留内容、具体修改和复审标准。")
    if value["requires_author_decision"] and not value["author_question"].strip():
        raise CoreError("REPAIR_INVALID", "请责编具体说明哪项设定冲突需要作者裁决。")
    return value


def plan_text(value, repair=False):
    labels = REPAIR_LABELS if repair else PLAN_LABELS
    parts = ["# 责编修订方案" if repair else "# 责编章节执行计划"]
    if not repair:
        parts.append(f"第{value['volume']}卷 · 第{value['chapter']}章")
    parts.extend("## " + label + "\n" + value[key] for key, label in labels.items())
    if not repair:
        w = value["word_range"]
        parts.append(f"## 篇幅建议\n{w['minimum']}—{w['maximum']} 字，参考 {w['target']} 字；自然浮动，不凑字、不截断。")
    return "\n\n".join(parts)


PLAN_INSTRUCTIONS = ("你是责编。每章写作前先根据作者确认大纲制定当前章完整执行计划，第一章也必须如此。"
    "填写当前卷/章、可选卷章名、剧情目标、核心事件、人物行动/情绪、世界事实、连续性、结尾目标、篇幅范围及依据；"
    "不适用处明确说明，不以空字符串替代必要分析。标题可以留空，作者已有标题必须沿用。"
    "作者确认大纲最高优先，明确的篇幅要求优先；其次本章叙事需要，最后全局篇幅建议。"
    "宏观大纲也可直接规划本章，不预设全书都已细化。上一章方向是建议，不能修改正式大纲或正典。"
    "结尾目标可以是安静的关系位移、意象或留白，不强制商业钩子。")
REPAIR_INSTRUCTIONS = ("你是责编。分析本章有依据的中/高重要问题，提出可直接执行的修订方案，说明诊断、保留内容、"
    "逐项修改和复审标准。只在当前草稿内修复，不改正式大纲、正典或其他章节。"
    "主观审美不冒充硬伤，篇幅差异本身不要求返工。只有确实不能从已确认资料推断的重大作者选择或资料矛盾，"
    "才设 requires_author_decision=true，并提出具体问题；不要把普通可修复问题交回作者。")
