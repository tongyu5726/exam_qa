"""三层漏斗意图路由测试。"""

from unittest.mock import MagicMock

from src.services.intent import resolve_intent


def test_rule_extracts_scenario_and_effective_date_without_llm():
    llm = MagicMock(configured=True)
    decision = resolve_intent(
        "截至2026年9月1日期末考试可以携带计算器吗？", llm=llm
    )
    assert decision.task == "rule_query"
    assert decision.scenario == "考试"
    assert decision.as_of == "2026-09-01"
    assert decision.layer == "rule"
    llm.chat.assert_not_called()


def test_rule_routes_chapter_in_auto_mode():
    decision = resolve_intent("请概述第3章", requested_mode="auto")
    assert decision.mode == "chapter"
    assert decision.task == "chapter"


def test_rule_routes_question_generation_before_exam_scenario():
    decision = resolve_intent("给我出几个期末的线代大题", requested_mode="auto")
    assert decision.task == "question_generate"
    assert decision.scenario == "考试"
    assert decision.layer == "rule"


def test_question_explanation_is_not_misrouted_to_generation():
    decision = resolve_intent("这道大题怎么做？", requested_mode="auto")
    assert decision.task == "qa"


def test_explicit_mode_and_filters_override_auto_intent():
    decision = resolve_intent(
        "第3章最新内容", requested_mode="qa", scenario="实验", as_of="2026-01-01"
    )
    assert decision.mode == "qa"
    assert decision.scenario == "实验"
    assert decision.as_of == "2026-01-01"


def test_context_inherits_confirmed_scope_without_llm():
    llm = MagicMock(configured=True)
    decision = resolve_intent(
        "那这个规定具体怎么执行？",
        previous={"task": "rule_query", "mode": "qa", "scenario": "考试", "as_of": "2026-09-01"},
        llm=llm,
    )
    assert decision.layer == "context"
    assert decision.scenario == "考试"
    assert decision.as_of == "2026-09-01"
    llm.chat.assert_not_called()


def test_llm_only_handles_ambiguous_context_when_no_state_exists():
    llm = MagicMock(configured=True)
    llm.chat.return_value = '{"task":"rule_query","mode":"qa","scenario":"考试","as_of":"2026-09-01","confidence":0.8}'
    decision = resolve_intent("那就按这个处理", llm=llm)
    assert decision.layer == "llm_fallback"
    assert decision.scenario == "考试"
    llm.chat.assert_called_once()
