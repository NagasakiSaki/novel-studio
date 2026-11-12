"""Versioned author style settings. These are creative guidance, not permissions."""
from __future__ import annotations

import difflib
import json
import re
import uuid
from .core import CoreError, SafeTree, admin, checked_text, digest

FORMAT = "novel-studio-creative-v1"
PROFILE_FORMAT = "novel-studio-style-profile-v1"
WRITER_FIELDS = (
    ("viewpoint", "叙述视角", "如限知第三人称、第一人称；说明视角切换方式。", "限知第三人称，不越过人物知情边界"),
    ("distance", "叙述距离", "贴近感知或保持观察距离，可以随场景调整。", "贴近人物感知，保留含蓄的观察距离"),
    ("complexity", "语言复杂度", "描述词汇和句法偏好，不用难词数量衡量文学性。", "用具体、准确的词语，允许必要的复杂句"),
    ("sentence_length", "句子长短", "长短句如何交替；不是硬性字数配额。", "随感知和动作变化，不机械统一句长"),
    ("paragraph_length", "段落长度", "段落如何承载动作、对话和心理转折。", "按叙事单位分段，允许安静和长段落"),
    ("dialogue", "对话比例", "可写大致倾向；不同场景不强制相同比例。", "让人物有各自目的，避免说明书式对白"),
    ("interiority", "心理描写", "内心活动的深度、直接程度和篇幅。", "允许心理纵深，通过处境和具体感知展开"),
    ("environment", "环境描写", "环境对氛围、人物和主题的作用。", "选择与人物感知、关系和情境有关的细节"),
    ("sensory", "感官密度", "感官种类与强度，避免机械堆砌。", "按场景选取有效的声音、触感和气味"),
    ("emotion", "情绪强度", "克制、外露或变化；不要把标签当作证据。", "由动作和选择承载情绪，允许矛盾和留白"),
    ("pace", "叙事节奏", "快慢、停顿与时间组织；允许慢热。", "根据人物与信息变化推进，不强制商业断章"),
    ("rhetoric", "修辞倾向", "比喻、重复、标点等如何服务具体语境。", "修辞服从语境，保护有意的破折号、省略号与复沓"),
    ("imagery", "意象倾向", "意象的主题关联与变化，避免固定配额。", "意象从器物、环境和人物经历中生长"),
    ("voices", "角色语言差异", "语域、节奏、避讳与关系变化。", "每个人物保留独立利益、语感和认知盲区"),
    ("avoid", "避免的表达习惯", "描述具体阅读效果，不设机械禁词表。", "避免无依据的情绪标签、空洞总结和自动道德升华"),
    ("custom", "其他自定义要求", "作者可写本书特有规则；不能授予工具或文件权限。", ""),
)
TEMPLATES = {
    "细腻现实主义": {"distance": "贴近人物的日常感知，注意环境、器物和关系的细微变化"},
    "文学性叙事": {"pace": "允许非线性、意识流和多义留白；让感知与语言本身形成推进"},
    "商业悬疑": {"pace": "重视线索、因果和信息释放；悬念服务本书目标，不强制机械钩子"},
    "轻小说": {"complexity": "语句清楚、人物语气鲜明，幽默和轻快程度由作者决定"},
    "剧情驱动型": {"pace": "让选择与后果推动事件，避免为反转而牺牲因果"},
    "人物驱动型": {"interiority": "重视动机、认知盲区与关系位移，允许言行矛盾和慢热"},
    "自定义风格": {},
}


def template(name):
    if name not in TEMPLATES:
        raise CoreError("PROFILE_INVALID", "请选择已有初始模板。")
    return {"format": PROFILE_FORMAT, "kind": "writer_style", "title": name,
            "fields": {key: TEMPLATES[name].get(key, default) for key, _, _, default in WRITER_FIELDS}, "rules": ""}


def validate_profile(value):
    if isinstance(value, dict) and value.get("kind") == "editor_aesthetic":
        from .aesthetics import validate_profile as validate_aesthetic
        return validate_aesthetic(value)
    required = {"format", "kind", "title", "fields", "rules"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {"origin"} or value.get("format") != PROFILE_FORMAT or value.get("kind") != "writer_style":
        raise CoreError("PROFILE_INVALID", "这不是受支持的主笔风格档案。不会导入执行代码或权限配置。")
    checked_text(value["title"], 180)
    checked_text(value["rules"], 36000)
    if not value["title"].strip() or not isinstance(value["fields"], dict) or set(value["fields"]) != {r[0] for r in WRITER_FIELDS}:
        raise CoreError("PROFILE_INVALID", "档案名称或结构化维度不完整。")
    for text in value["fields"].values():
        checked_text(text, 2400)
    if "origin" in value:
        origin = value["origin"]
        if not isinstance(origin, dict) or set(origin) != {"analysis_id", "strength", "sources"} or not isinstance(origin["analysis_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", origin["analysis_id"]) or not isinstance(origin["strength"], str) or origin["strength"] not in {"light", "medium", "strong"} or not isinstance(origin["sources"], list) or not 1 <= len(origin["sources"]) <= 2:
            raise CoreError("PROFILE_INVALID", "风格来源记录不正确。")
        for source in origin["sources"]:
            if not isinstance(source, dict) or set(source) != {"source_id", "source_revision", "original_sha256", "version", "start_line", "end_line"} or not isinstance(source["source_id"], str) or not re.fullmatch(r"[md]_[a-f0-9]{32}", source["source_id"]):
                raise CoreError("PROFILE_INVALID", "来源编号不正确，不接受任意路径。")
            if any(not isinstance(source[key], str) or not re.fullmatch(r"[a-f0-9]{64}", source[key]) for key in ("source_revision", "original_sha256")) or any(type(source[key]) is not int or source[key] < 1 for key in ("start_line", "end_line")) or source["end_line"] < source["start_line"]:
                raise CoreError("PROFILE_INVALID", "来源版本或范围不正确。")
            if source["version"] is not None and (type(source["version"]) is not int or source["version"] < 1):
                raise CoreError("PROFILE_INVALID", "资料版本不正确。")
    return json.loads(admin.encode(value))


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise CoreError("PROFILE_INVALID", "档案编号不正确。")
    return value


class CreativeService:
    def __init__(self, core):
        self.core = core

    def state(self):
        with SafeTree(self.core.project) as tree:
            raw = tree.maybe_read(".novel-studio/Creative/settings.json")
            return json.loads(raw) if raw else {"format": FORMAT, "profiles": {}, "bindings": {}, "chapter_overrides": {}}

    def _mutate(self, operation):
        with self.core.mutex:
            self.core.require_idle()
            with SafeTree(self.core.project) as tree:
                with tree.lock():
                    name = ".novel-studio/Creative/settings.json"
                    old = tree.maybe_read(name)
                    state = json.loads(old) if old else {"format": FORMAT, "profiles": {}, "bindings": {}, "chapter_overrides": {}}
                    tree.mkdir(".novel-studio/Creative")
                    result = operation(tree, state)
                    if old is not None and admin.encode(state) != old:
                        tree.mkdir(".novel-studio/Creative/Config-History")
                        backup = ".novel-studio/Creative/Config-History/" + digest(old) + ".json"
                        if tree.maybe_read(backup) is None:
                            tree.create(backup, old)
                    tree.publish(name, admin.encode(state), digest(old) if old else None)
                    return result

    def profiles(self, include_archived=False):
        return [{"profile_id": key, **row} for key, row in self.state()["profiles"].items() if include_archived or not row.get("archived")]

    def profile(self, profile_id, version=None):
        identifier(profile_id)
        row = self.state()["profiles"].get(profile_id)
        if row is None:
            raise CoreError("PROFILE_MISSING", "档案不存在或不属于此作品。")
        version = row["version"] if version is None else version
        if type(version) is not int or not 1 <= version <= row["version"]:
            raise CoreError("PROFILE_INVALID", "档案版本不存在。")
        with SafeTree(self.core.project) as tree:
            raw = tree.read(f".novel-studio/Creative/Profiles/{profile_id}/v{version}.json")
        if digest(raw) != row["versions"][str(version)]:
            raise CoreError("PROFILE_CHANGED", "档案历史与记录不一致，已停止加载。")
        return {"profile_id": profile_id, "version": version, "revision": digest(raw), "value": validate_profile(json.loads(raw))}

    def check_profile(self, value):
        value = validate_profile(value)
        if value.get("origin") and value["origin"]["analysis_id"] in self.state().get("style_analyses", {}):
            from .style_analysis import StyleAnalysisService, copied_fragments
            record = StyleAnalysisService(self.core).get(value["origin"]["analysis_id"])
            if copied_fragments(value, record["selections"]):
                raise CoreError("STYLE_COPY_WARNING", "档案仍含与参考原文连续相同的长片段，请改为抽象风格规则后保存。")
        return value

    def save(self, value, profile_id=None, expected_revision=None):
        value = self.check_profile(value)
        profile_id = identifier(profile_id) if profile_id is not None else uuid.uuid4().hex
        payload = admin.encode(value)
        def operation(tree, state):
            old = state["profiles"].get(profile_id)
            if old and old["kind"] != value["kind"]:
                raise CoreError("PROFILE_INVALID", "主笔风格和责编审美不能互相覆盖，请创建独立档案。")
            if (old["versions"][str(old["version"])] if old else None) != expected_revision:
                raise CoreError("REVISION_CONFLICT", "此档案已在另一个窗口更新，请重新载入后比较差异。")
            if old and old.get("archived"):
                raise CoreError("PROFILE_ARCHIVED", "此档案已归档，请复制成新档案后编辑。")
            if old and expected_revision == digest(payload):
                return {"profile_id": profile_id, "version": old["version"], "revision": expected_revision}
            version = old["version"] + 1 if old else 1
            directory = ".novel-studio/Creative/Profiles/" + profile_id
            tree.mkdir(directory)
            tree.create(directory + f"/v{version}.json", payload)
            state["profiles"][profile_id] = {"title": value["title"], "kind": value["kind"], "version": version, "archived": False,
                "versions": {**(old["versions"] if old else {}), str(version): digest(payload)}}
            return {"profile_id": profile_id, "version": version, "revision": digest(payload)}
        return self._mutate(operation)

    def bind(self, profile_id, version=None):
        profile = self.profile(profile_id, version)
        role = "writer" if profile["value"]["kind"] == "writer_style" else "editor"
        return self._mutate(lambda tree, state: state["bindings"].update({role: {k: profile[k] for k in ("profile_id", "version", "revision")}}))

    def unbind(self, role="writer"):
        if role not in {"writer", "editor"}:
            raise CoreError("ROLE_DENIED", "未知角色。")
        return self._mutate(lambda tree, state: state["bindings"].pop(role, None))

    def archive(self, profile_id):
        identifier(profile_id)
        def operation(tree, state):
            row = state["profiles"].get(profile_id)
            if row is None:
                raise CoreError("PROFILE_MISSING", "档案不存在。")
            row["archived"] = True
            for role in ("writer", "editor"):
                if state["bindings"].get(role, {}).get("profile_id") == profile_id:
                    state["bindings"].pop(role)
        return self._mutate(operation)

    def override(self, document_id, fields, rules=""):
        row = self.core.document(document_id, "writer")
        if row["area"] != "drafts" or not isinstance(fields, dict) or not set(fields) <= {r[0] for r in WRITER_FIELDS}:
            raise CoreError("PROFILE_INVALID", "章节覆盖只能关联本作品草稿及已有风格维度。")
        for text in fields.values():
            checked_text(text, 2400)
        checked_text(rules, 18000)
        return self._mutate(lambda tree, state: state["chapter_overrides"].update({document_id: {"fields": fields, "rules": rules}}))

    def clear_override(self, document_id):
        return self._mutate(lambda tree, state: state["chapter_overrides"].pop(document_id, None))

    def context(self, role, document_id=None):
        if role not in {"writer", "editor"}:
            raise CoreError("ROLE_DENIED", "未知角色。")
        state = self.state()
        binding = state["bindings"].get(role)
        stable = {}
        if binding:
            profile = self.profile(binding["profile_id"], binding["version"])
            if profile["revision"] != binding["revision"]:
                raise CoreError("PROFILE_CHANGED", "绑定的风格版本已经变化。")
            value = profile["value"]
            stable = {"kind": value["kind"], "profile_id": profile["profile_id"], "version": profile["version"],
                      "fields": {k: v for k, v in value["fields"].items() if v}, "rules": value["rules"],
                      "priority": "本轮作者 > 章节覆盖 > 本档案 > 项目默认；不覆盖安全边界与已批准事实"}
            if role == "editor":
                stable.update(weights=value["weights"], options=value["options"], priority="本轮作者审稿要求优先；权重仅为审阅重点，不是文学分数，不自动改稿")
        return stable, state["chapter_overrides"].get(document_id, {}) if role == "writer" and document_id else {}

    @staticmethod
    def diff(before, after):
        def lines(value):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).splitlines(keepends=True)
        return "".join(difflib.unified_diff(lines(before), lines(after), fromfile="原风格", tofile="拟议风格"))
