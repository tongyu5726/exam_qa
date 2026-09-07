"""三层漏斗意图路由：规则 → 会话上下文 → 受约束 LLM 兜底。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal

from src.services.evidence_metadata import normalize_scope
from src.services.llm import LLMClient

IntentLayer = Literal["rule", "context", "llm_fallback", "default"]
IntentTask = Literal[
    "qa", "concept", "chapter", "rule_query", "version_compare", "question_generate"
]

_DATE = re.compile(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?")
_CHAPTER = re.compile(r"第\s*[0-9一二三四五六七八九十]+\s*章")
_SCENARIO_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("考试", ("考试", "期中", "期末", "测验", "考场", "开卷", "闭卷")),
    ("实验", ("实验", "实验室", "实训")),
    ("作业", ("作业", "习题", "报告")),
    ("课堂", ("课堂", "上课", "课上")),
)
_CONTEXTUAL = re.compile(r"(?:那|这个|刚才|上面|前者|后者|同样|按刚才|照刚才)")
_COMPARE = re.compile(r"(?:新旧|两个|不同).{0,8}(?:版本|版).{0,8}(?:对比|区别|差异)|(?:版本|版).{0,8}(?:对比|区别|差异)")
_RULE_QUERY = re.compile(r"(?:是否|能不能|可不可以|可以吗|规定|要求|适用|生效|失效|现行|最新版|最新)")
_CONCEPT = re.compile(r"(?:是什么|定义|原理|公式|推导|性质|含义)")
_QUESTION_GENERATE = re.compile(
    r"(?:(?:给我|帮我|请)(?:出|生成|创建|来|命制|组|做成)|"
    r"(?:给我|帮我)(?:几|[一二三四五六七八九十\d]+)(?:个|道|份)?|"
    r"(?:出|生成|创建|来|命制|组|做成))"
    r"(?:几|[一二三四五六七八九十\d]+)?(?:个|道|份)?[^，。！？]{0,24}"
    r"(?:题目|试题|大题|小题|选择题|填空题|简答题|试卷|卷子)"
)


@dataclass(frozen=True)
class IntentDecision:
    task: IntentTask = "qa"
    mode: Literal["qa", "concept", "chapter"] = "qa"
    scenario: str | None = None
    as_of: str | None = None
    confidence: float = 0.40
    layer: IntentLayer = "default"
    rationale: str = "普通问答，使用默认检索"

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_date(text: str) -> str | None:
    match = _DATE.search(text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def _extract_scenario(text: str) -> str | None:
    lowered = text.lower()
    for scenario, aliases in _SCENARIO_ALIASES:
        if any(alias in lowered for alias in aliases):
            return scenario
    return None


def _rule_intent(question: str) -> IntentDecision | None:
    scenario = _extract_scenario(question)
    as_of = _extract_date(question)
    if _QUESTION_GENERATE.search(question):
        return IntentDecision(
            task="question_generate",
            scenario=scenario,
            as_of=as_of,
            confidence=0.98,
            layer="rule",
            rationale="命中出题或组卷表达，路由到我的题库",
        )
    if _COMPARE.search(question):
        return IntentDecision(task="version_compare", scenario=scenario, as_of=as_of, confidence=0.97, layer="rule", rationale="命中版本对比表达")
    if _CHAPTER.search(question) or "章节概览" in question or "本章概述" in question:
        return IntentDecision(task="chapter", mode="chapter", scenario=scenario, as_of=as_of, confidence=0.96, layer="rule", rationale="命中章节概览表达")
    if scenario or as_of or _RULE_QUERY.search(question):
        if ("最新" in question or "现行" in question) and as_of is None:
            as_of = date.today().isoformat()
        return IntentDecision(task="rule_query", scenario=scenario, as_of=as_of, confidence=0.91, layer="rule", rationale="命中规则、场景或时效表达")
    if _CONCEPT.search(question):
        return IntentDecision(task="concept", mode="concept", confidence=0.82, layer="rule", rationale="命中知识点表达")
    return None


def _context_intent(question: str, previous: dict | None) -> IntentDecision | None:
    if not previous or not _CONTEXTUAL.search(question):
        return None
    previous_mode = previous.get("mode") if previous.get("mode") in {"qa", "concept", "chapter"} else "qa"
    previous_task = previous.get("task") if previous.get("task") in {"qa", "concept", "chapter", "rule_query", "version_compare", "question_generate"} else "qa"
    return IntentDecision(task=previous_task, mode=previous_mode, scenario=previous.get("scenario") or None, as_of=previous.get("as_of") or None, confidence=0.76, layer="context", rationale="根据会话中最近已确认的意图继承范围")


def _llm_intent(question: str, previous: dict | None, llm: LLMClient | None) -> IntentDecision | None:
    """只处理复杂追问；模型只能产出计划，实际检索仍由确定性代码执行。"""
    if llm is None or not llm.configured or not _CONTEXTUAL.search(question):
        return None
    prompt = {
        "question": question, "previous_intent": previous or {},
        "allowed_tasks": ["qa", "concept", "chapter", "rule_query", "version_compare", "question_generate"],
        "allowed_modes": ["qa", "concept", "chapter"],
        "instruction": "仅返回 JSON：task, mode, scenario, as_of, confidence。scenario 为空或受控中文键；as_of 为 YYYY-MM-DD 或空。",
    }
    try:
        raw = llm.chat(
            [{"role": "system", "content": "你是意图路由器，不回答问题、不调用工具。"}, {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            temperature=0, max_tokens=120,
        )
        data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
    except Exception:
        return None
    task, mode = data.get("task"), data.get("mode")
    if task not in {"qa", "concept", "chapter", "rule_query", "version_compare", "question_generate"} or mode not in {"qa", "concept", "chapter"}:
        return None
    as_of = data.get("as_of") or None
    if as_of:
        try:
            date.fromisoformat(as_of)
        except (TypeError, ValueError):
            return None
    confidence = data.get("confidence", 0.60)
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        confidence = 0.60
    return IntentDecision(task=task, mode=mode, scenario=normalize_scope(data.get("scenario")) if data.get("scenario") else None, as_of=as_of, confidence=float(confidence), layer="llm_fallback", rationale="复杂指代由受约束 LLM 生成意图计划")


def resolve_intent(question: str, *, requested_mode: str = "auto", scenario: str | None = None, as_of: str | None = None, previous: dict | None = None, llm: LLMClient | None = None) -> IntentDecision:
    """按漏斗解析，显式 API 参数和 mode 始终优先于自动判断。"""
    rule = _rule_intent(question)
    # 指代性追问里的“规定”等泛化规则词没有独立范围，优先继承上一轮已确认状态；
    # 当前问题若明确给出场景、日期或章节，仍由规则层直接决定。
    defer_generic_rule_to_context = bool(
        rule
        and rule.task == "rule_query"
        and _CONTEXTUAL.search(question)
        and previous
        and not _extract_scenario(question)
        and not _extract_date(question)
    )
    decision = _context_intent(question, previous) if defer_generic_rule_to_context else rule
    decision = decision or _context_intent(question, previous)
    if decision is None:
        decision = _llm_intent(question, previous, llm) or IntentDecision()
    mode = requested_mode if requested_mode in {"qa", "concept", "chapter"} else decision.mode
    return IntentDecision(
        task=decision.task, mode=mode,
        scenario=normalize_scope(scenario) if scenario else decision.scenario,
        as_of=as_of or decision.as_of, confidence=decision.confidence,
        layer=decision.layer, rationale=decision.rationale,
    )
