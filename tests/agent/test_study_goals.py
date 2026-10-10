"""A study goal is planned, tested interactively and resumed in the same conversation."""
import json
from unittest.mock import MagicMock, patch
import pytest
from src.services.query import ask, ask_stream
from src.services.storage.conversation_store import ConversationStore
from src.services.storage.question_bank_store import QuestionBankStore

HIT = {"id": "ev1", "text": "采样频率至少为最高频率的两倍。", "score": .9,
       "metadata": {"course_id": "course", "source_file": "第三章.pdf", "chapter": "第三章", "page": 1}}


def calls(*names):
    return [{"content": "", "tool_calls": [{"id": str(i), "name": n, "arguments": json.dumps({"step_id": sid}) if sid else "{}"}]} for i, (n, sid) in enumerate(names)]


@pytest.fixture
def runtime(tmp_path):
    conv = ConversationStore(str(tmp_path / "conversation.db"))
    bank = QuestionBankStore(str(tmp_path / "bank.db"))
    llm = MagicMock(configured=True)
    yield conv, bank, llm
    bank.close()


def invoke(runtime, question, stream=False, **scope):
    conv, bank, llm = runtime
    args = dict(question=question, mode="auto", vs=MagicMock(), llm=llm, course_id="course",
                conversation_id="conv", conversation_store=conv, question_bank_store=bank, **scope)
    return list(ask_stream(**args)) if stream else ask(**args)


def quiz():
    return json.dumps([{"stem": "采样频率至少为最高频率的多少倍？", "options": ["两倍", "一倍", "一半", "任意"],
        "answer": "两倍", "analysis": "原文要求两倍下限。", "evidence_ids": ["ev1"]}], ensure_ascii=False)


def test_open_goal_plans_then_waits_for_answers_without_leaking_solution(runtime):
    llm = runtime[2]
    llm.chat.side_effect = [json.dumps({"steps": [{"action": "self_test", "focus": "采样条件", "reason": "先诊断"}, {"action": "explain", "focus": "采样条件", "reason": "再补讲"}]}), quiz(), json.dumps([{"index": 1, "supported": True}])]
    llm.chat_with_tools.side_effect = calls(("build_study_plan", None), ("retrieve_evidence", None), ("execute_study_step", "s1"))
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        events = invoke(runtime, "第三章掌握得不好，帮我复习，每轮1道题", stream=True)
    result = events[-1]["data"]
    assert result["task"]["kind"] == "study_goal" and result["task"]["status"] == "waiting_answer"
    assert [s["action"] for s in result["task"]["plan"]] == ["self_test", "explain"]
    item = result["task"]["self_test"]["items"][0]
    assert "answer" not in item and "analysis" not in item and "citations" not in item
    assert not result["task"]["drafts"] and not result["artifacts"]
    assert runtime[1].list_questions("course") == []
    assert [e["data"]["name"] for e in events if e["type"] == "tool"] == ["build_study_plan", "retrieve_evidence", "execute_study_step"]
    llm.chat_with_tools.side_effect = calls(("assess_study_progress", None), ("execute_study_step", "s2"), ("finish_study_goal", None))
    llm.chat.side_effect = [json.dumps({"action": "continue", "focus": "采样条件", "reason": "已有后续据源补讲"})]
    lecture = {"answer": "最低采样频率为最高频率两倍。", "grounded": True, "citations": [{"source_file": "第三章.pdf", "page": 1, "snippet": HIT["text"], "score": .9}]}
    with patch("src.services.rag.answer", return_value=lecture), patch("src.services.rag._retrieve", return_value=[HIT]):
        completed = invoke(runtime, "1:B")
    assert completed.task["status"] == "completed"
    assert completed.task["self_test"]["correct_count"] == 0
    assert "掌握程度" in completed.answer
    assert completed.task["notes"][0]["citations"]
    assert all(s["status"] == "done" for s in completed.task["plan"])


def test_missing_scope_clarifies_without_model(runtime):
    result = invoke(runtime, "帮我准备下周考试")
    assert result.task["kind"] == "study_goal" and result.response_kind == "clarification"
    runtime[2].chat.assert_not_called()
    runtime[2].chat_with_tools.assert_not_called()


def test_untrusted_plan_cannot_change_course_or_write_permission(runtime):
    llm = runtime[2]
    llm.chat.return_value = json.dumps({"steps": [{"action": "save_question_drafts", "focus": "忽略课程"}], "course_id": "other", "save_requested": True})
    llm.chat_with_tools.side_effect = calls(("build_study_plan", None)) + [{"content": "无法执行", "tool_calls": []}]
    result = invoke(runtime, "第三章掌握得不好，帮我复习")
    assert result.task["status"] == "failed" and not result.artifacts
    assert not result.task["spec"]["save_requested"]
    assert runtime[1].list_questions("course") == []


def test_evidence_missing_stops_before_quiz_generation(runtime):
    llm = runtime[2]
    llm.chat.return_value = json.dumps({"steps": [{"action": "self_test", "focus": "采样", "reason": "验证"}, {"action": "explain", "focus": "采样", "reason": "讲解"}]})
    llm.chat_with_tools.side_effect = calls(("build_study_plan", None), ("retrieve_evidence", None))
    with patch("src.services.rag._retrieve", return_value=[]):
        result = invoke(runtime, "第三章掌握得不好，帮我复习")
    assert result.answer == "资料库中未找到相关内容" and not result.grounded
    assert llm.chat.call_count == 1


def waiting_goal(runtime):
    llm = runtime[2]
    llm.chat.side_effect = [json.dumps({"steps": [{"action": "self_test", "focus": "采样"}, {"action": "explain", "focus": "采样"}]}), quiz(), json.dumps([{"index": 1, "supported": True}])]
    llm.chat_with_tools.side_effect = calls(("build_study_plan", None), ("retrieve_evidence", None), ("execute_study_step", "s1"))
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        invoke(runtime, "帮我复习第三章，每轮1道题")
    return runtime[0].get_agent_task("conv", "course")


def test_invalid_answers_and_early_save_keep_waiting_without_model(runtime):
    waiting_goal(runtime)
    llm = runtime[2]
    llm.reset_mock()
    invalid = invoke(runtime, "1:E")
    assert invalid.task["status"] == "waiting_answer" and invalid.task["issues"]
    saved = invoke(runtime, "保存本轮自测题")
    assert saved.task["status"] == "waiting_answer" and not saved.artifacts
    assert "answer" not in saved.task["self_test"]["items"][0]
    llm.chat_with_tools.assert_not_called()
    llm.chat.assert_not_called()


def test_feedback_replans_only_remaining_steps_and_is_bounded(runtime):
    from src.services.agent import study_goals
    from src.exceptions import BadRequestException
    task = waiting_goal(runtime)
    task = study_goals.prepare("1:B", task, course_id="course", scenario=None, as_of=None)
    task = study_goals.prepare("降低难度，先讲解，重新规划", task, course_id="course", scenario=None, as_of=None)
    assert task["needs_replan"] and task["spec"]["difficulty"] == "easy"
    llm = runtime[2]
    llm.chat.side_effect = None
    llm.chat.return_value = json.dumps({"steps": [{"action": "explain", "focus": "采样条件", "reason": "回应错项"}]})
    for _ in range(2):
        task, _ = study_goals.execute("replan_study_goal", task, vs=MagicMock(), llm=llm, bank=runtime[1], args={})
    assert task["plan"][0]["id"] == "s1" and task["plan"][0]["status"] == "done"
    assert task["revisions"] == 2 and not task["needs_replan"]
    with pytest.raises(BadRequestException):
        study_goals.execute("replan_study_goal", task, vs=MagicMock(), llm=llm, bank=runtime[1], args={})


def test_cannot_skip_answer_step_or_finish_early(runtime):
    from src.services.agent import study_goals
    from src.exceptions import BadRequestException
    task = waiting_goal(runtime)
    for name, args in [("execute_study_step", {"step_id": "s2"}), ("finish_study_goal", {}), ("save_study_questions", {})]:
        with pytest.raises(BadRequestException):
            study_goals.execute(name, task, vs=MagicMock(), llm=runtime[2], bank=runtime[1], args=args)
    assert not runtime[1].list_questions("course")


def test_scope_change_discards_old_quiz_and_plan(runtime):
    from src.services.agent import study_goals
    previous = waiting_goal(runtime)
    task = study_goals.prepare("继续", previous, course_id="course", scenario="exam", as_of="2026-10-01")
    assert task["id"] != previous["id"] and task["status"] == "running"
    assert not task["plan"] and not task["quizzes"] and not task["evidence"]


def test_source_change_prevents_completion(runtime):
    from src.services.agent import study_goals
    from src.exceptions import BadRequestException
    task = waiting_goal(runtime)
    task = study_goals.prepare("1:A", task, course_id="course", scenario=None, as_of=None)
    task["plan"][1]["status"] = "done"
    task["notes"] = [{"step_id": "s2", "citations": [{"source_file": "第三章.pdf"}], "answer": "讲解"}]
    task["quizzes"]["s1"]["assessment"] = {"action": "continue"}
    with patch("src.services.rag._retrieve", return_value=[]), pytest.raises(BadRequestException, match="资料已变更"):
        study_goals.execute("finish_study_goal", task, vs=MagicMock(), llm=runtime[2], bank=runtime[1], args={})
    assert task["status"] != "completed"


def test_submitted_quiz_saves_only_with_user_authorization_and_is_idempotent(runtime):
    from src.services.agent import study_goals
    task = waiting_goal(runtime)
    task = study_goals.prepare("1:A", task, course_id="course", scenario=None, as_of=None)
    task = study_goals.prepare("保存本轮自测题", task, course_id="course", scenario=None, as_of=None)
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        task, _ = study_goals.execute("save_study_questions", task, vs=MagicMock(), llm=runtime[2], bank=runtime[1], args={})
        task, _ = study_goals.execute("save_study_questions", task, vs=MagicMock(), llm=runtime[2], bank=runtime[1], args={})
    assert len(task["artifacts"]) == len(runtime[1].list_questions("course")) == 1
    assert not runtime[1].list_questions("other")


def test_wrong_answer_can_trigger_review_and_new_quiz_then_resume(runtime):
    waiting_goal(runtime)
    llm = runtime[2]
    new_quiz = json.loads(quiz())
    new_quiz[0]["stem"] = "最高频率为10Hz时，最低采样频率是哪一项？"
    new_quiz[0]["options"] = ["20Hz", "10Hz", "5Hz", "任意"]
    new_quiz[0]["answer"] = "20Hz"
    lecture = {"answer": "至少为最高频率两倍。", "grounded": True, "citations": [{"source_file": "第三章.pdf", "page": 1, "snippet": HIT["text"], "score": .9}]}
    llm.chat.side_effect = [json.dumps({"action": "retest", "focus": "采样下限", "reason": "本轮答错下限，先补讲再换题复核"}), json.dumps(new_quiz), json.dumps([{"index": 1, "supported": True}])]
    llm.chat_with_tools.side_effect = calls(("assess_study_progress", None), ("execute_study_step", "feedback_s1_review"), ("execute_study_step", "feedback_s1_test"))
    with patch("src.services.rag._retrieve", return_value=[HIT]), patch("src.services.rag.answer", return_value=lecture):
        events = invoke(runtime, "1:B", stream=True)
    task = events[-1]["data"]["task"]
    assert task["status"] == "waiting_answer" and task["retests"] == 1
    assert task["assessments"][0]["action"] == "retest"
    assert "answer" not in task["self_test"]["items"][0]
    assert task["self_test"]["items"][0]["stem"] != json.loads(quiz())[0]["stem"]
    assert [e["data"]["name"] for e in events if e["type"] == "tool"] == ["assess_study_progress", "execute_study_step", "execute_study_step"]
    assert runtime[0].get_agent_task("conv", "course")["retests"] == 1
    llm.chat.side_effect = [json.dumps({"action": "continue", "focus": "采样下限", "reason": "新题回答正确，继续原计划"})]
    llm.chat_with_tools.side_effect = calls(("assess_study_progress", None), ("execute_study_step", "s2"), ("finish_study_goal", None))
    with patch("src.services.rag._retrieve", return_value=[HIT]), patch("src.services.rag.answer", return_value=lecture):
        done = invoke(runtime, "1:A")
    assert done.task["status"] == "completed" and len(done.task["assessments"]) == 2
    assert all(s["status"] == "done" for s in done.task["plan"])
    assert not runtime[1].list_questions("course")


def test_feedback_cannot_be_skipped_or_finish_with_unaddressed_wrong_answer(runtime):
    from src.services.agent import study_goals
    from src.exceptions import BadRequestException
    task = waiting_goal(runtime)
    task = study_goals.prepare("1:B", task, course_id="course", scenario=None, as_of=None)
    with pytest.raises(BadRequestException, match="assess_study_progress"):
        study_goals.execute("execute_study_step", task, vs=MagicMock(), llm=runtime[2], bank=runtime[1], args={"step_id": "s2"})
    task["plan"][1]["status"] = "done"
    task["notes"] = [{"citations": [{"source_file": "第三章.pdf"}]}]
    task["quizzes"]["s1"]["submitted_note_count"] = 1
    task["quizzes"]["s1"]["assessment"] = {"action": "continue"}
    with pytest.raises(BadRequestException, match="完成条件"):
        study_goals.execute("finish_study_goal", task, vs=MagicMock(), llm=runtime[2], bank=runtime[1], args={})


@pytest.mark.parametrize("decision", [
    {"action": "save_question_drafts", "focus": "采样", "reason": "保存"},
    {"action": "continue", "focus": "采样", "reason": "继续", "save_requested": True},
    {"action": "retest", "focus": "", "reason": "复核"},
])
def test_untrusted_feedback_cannot_add_capabilities_or_write(runtime, decision):
    from src.services.agent import study_goals
    from src.exceptions import BadRequestException
    task = waiting_goal(runtime)
    task = study_goals.prepare("1:B", task, course_id="course", scenario=None, as_of=None)
    runtime[2].chat.side_effect = None
    runtime[2].chat.return_value = json.dumps(decision)
    with patch("src.services.rag._retrieve", return_value=[HIT]), pytest.raises(BadRequestException):
        study_goals.execute("assess_study_progress", task, vs=MagicMock(), llm=runtime[2], bank=runtime[1], args={})
    assert not task["quizzes"]["s1"].get("assessment")
    assert not task["spec"]["save_requested"] and not runtime[1].list_questions("course")


def test_retest_budget_and_idempotent_assessment(runtime):
    from src.services.agent import study_goals
    from src.exceptions import BadRequestException
    task = waiting_goal(runtime)
    task = study_goals.prepare("1:B", task, course_id="course", scenario=None, as_of=None)
    task["retests"] = 2
    llm = runtime[2]
    llm.chat.side_effect = None
    llm.chat.return_value = json.dumps({"action": "retest", "focus": "采样", "reason": "复核"})
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        with pytest.raises(BadRequestException, match="上限"):
            study_goals.execute("assess_study_progress", task, vs=MagicMock(), llm=llm, bank=runtime[1], args={})
        llm.chat.return_value = json.dumps({"action": "review", "focus": "采样", "reason": "追加次数已达上限，完成据源错项补讲"})
        task, _ = study_goals.execute("assess_study_progress", task, vs=MagicMock(), llm=llm, bank=runtime[1], args={})
        plan = list(task["plan"])
        count = llm.chat.call_count
        task, _ = study_goals.execute("assess_study_progress", task, vs=MagicMock(), llm=llm, bank=runtime[1], args={})
    assert llm.chat.call_count == count and task["plan"] == plan and task["retests"] == 2


def test_retest_rejects_repeated_old_question_and_replan_keeps_promised_steps(runtime):
    from src.services.agent import study_goals
    from src.exceptions import BadRequestException
    task = waiting_goal(runtime)
    task = study_goals.prepare("1:B", task, course_id="course", scenario=None, as_of=None)
    llm = runtime[2]
    llm.chat.side_effect = [json.dumps({"action": "retest", "focus": "采样", "reason": "换题复核"})]
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        task, _ = study_goals.execute("assess_study_progress", task, vs=MagicMock(), llm=llm, bank=runtime[1], args={})
    llm.chat.side_effect = [json.dumps({"steps": [{"action": "explain", "focus": "采样"}]})]
    task, _ = study_goals.execute("replan_study_goal", task, vs=MagicMock(), llm=llm, bank=runtime[1], args={})
    assert [s["id"] for s in task["plan"][:3]] == ["s1", "feedback_s1_review", "feedback_s1_test"]
    task["plan"][1]["status"] = "done"
    llm.chat.side_effect = [quiz()]
    with pytest.raises(BadRequestException, match="不能重复"):
        study_goals.execute("execute_study_step", task, vs=MagicMock(), llm=llm, bank=runtime[1], args={"step_id": "feedback_s1_test"})
    assert "旧题干" in llm.chat.call_args.args[0][1]["content"]


def test_replanning_a_waiting_retest_keeps_followup_but_discards_old_unsubmitted_quiz(runtime):
    from src.services.agent import study_goals
    task = waiting_goal(runtime)
    task = study_goals.prepare("1:B", task, course_id="course", scenario=None, as_of=None)
    llm = runtime[2]
    llm.chat.side_effect = [json.dumps({"action": "retest", "focus": "采样", "reason": "换题复核"})]
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        task, _ = study_goals.execute("assess_study_progress", task, vs=MagicMock(), llm=llm, bank=runtime[1], args={})
    task["plan"][1]["status"] = "done"
    task["plan"][2]["status"] = "waiting_answer"
    task["quizzes"]["feedback_s1_test"] = {"id": "feedback_s1_test", "submitted": False, "items": []}
    task.update(active_quiz="feedback_s1_test", status="waiting_answer")
    task = study_goals.prepare("降低难度，先讲解，重新规划", task, course_id="course", scenario=None, as_of=None)
    llm.chat.side_effect = [json.dumps({"steps": [{"action": "explain", "focus": "采样"}]})]
    task, _ = study_goals.execute("replan_study_goal", task, vs=MagicMock(), llm=llm, bank=runtime[1], args={})
    pending = [s for s in task["plan"] if s["status"] != "done"]
    assert [s["action"] for s in pending] == ["explain", "self_test"]
    assert pending[-1]["id"] == "feedback_s1_test" and pending[-1]["status"] == "pending"
    assert "feedback_s1_test" not in task["quizzes"] and task["active_quiz"] is None
    assert task["quizzes"]["s1"]["submitted"]


def test_save_permission_is_consumed_by_the_requested_round_and_not_future_retests(runtime):
    from src.services.agent import study_goals
    task = waiting_goal(runtime)
    task = study_goals.prepare("保存本轮自测题", task, course_id="course", scenario=None, as_of=None)
    task = study_goals.prepare("1:B", task, course_id="course", scenario=None, as_of=None)
    llm = runtime[2]
    llm.chat.side_effect = [json.dumps({"action": "retest", "focus": "采样", "reason": "换题复核"})]
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        task, observation = study_goals.execute("assess_study_progress", task, vs=MagicMock(), llm=llm, bank=runtime[1], args={})
        assert observation["next"] == "save_study_questions"
        task, _ = study_goals.execute("save_study_questions", task, vs=MagicMock(), llm=llm, bank=runtime[1], args={})
    task["plan"][1]["status"] = "done"
    new_quiz = json.loads(quiz())
    new_quiz[0]["stem"] = "哪项符合采样下限要求？"
    llm.chat.side_effect = [json.dumps(new_quiz), json.dumps([{"index": 1, "supported": True}])]
    task, _ = study_goals.execute("execute_study_step", task, vs=MagicMock(), llm=llm, bank=runtime[1], args={"step_id": "feedback_s1_test"})
    public = study_goals.present(task)
    assert public["self_test"]["saved"] is False and not public["spec"]["save_requested"]
    assert len(public["artifacts"]) == len(runtime[1].list_questions("course")) == 1
    assert task["quizzes"]["s1"]["artifacts"] and not task["quizzes"]["feedback_s1_test"]["save_requested"]
