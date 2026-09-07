"""我的题库：课程隔离、证据约束出题、组卷和 API 装配。"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.dependencies import (
    get_catalog_store,
    get_llm_client,
    get_question_bank_store,
    get_vector_store,
)
from src.exceptions import BadRequestException
from src.main import app
from src.models import PaperAssembleRequest, QuestionGenerateRequest
from src.services.question_bank import _parse_questions, assemble_paper, generate_questions
from src.services.storage.question_bank_store import QuestionBankStore

client = TestClient(app)


def _payload(course_id="course-default", **overrides):
    base = {
        "course_id": course_id, "stem": "采样频率应满足什么条件？", "question_type": "short_answer",
        "options": [], "answer": "不低于最高频率两倍", "analysis": "依据采样定理", "difficulty": "medium",
        "chapter": "第4章", "status": "draft",
    }
    base.update(overrides)
    return base


def _hit():
    return {
        "text": "采样频率应不低于信号最高频率的两倍，才能无失真恢复。",
        "score": 0.9,
        "metadata": {"source_file": "signals.pdf", "page": 12, "source_version": "2.0"},
    }


def _citation():
    return {
        "source_file": "signals.pdf", "page": 12, "snippet": "采样频率应不低于两倍", "score": 0.9,
        "source_version": "2.0", "effective_from": "2026-01-01", "effective_to": "2026-12-31",
        "authority_level": 30, "authority_label": "教学材料", "applicability_scope": "考试", "selection_reason": "有效",
    }


class TestQuestionBankStore:
    def test_isolates_questions_and_builds_paper(self, temp_dir):
        store = QuestionBankStore(str(temp_dir / "bank.db"))
        question = store.create_question(_payload())
        store.create_question(_payload("course-other"))
        assert len(store.list_questions("course-default")) == 1
        assert store.get_question(question["id"], "course-other") is None

        paper = store.create_paper("course-default", "采样测试", "", [{"question_id": question["id"], "score": 10}])
        assert paper["question_count"] == 1
        assert paper["total_score"] == 10
        with pytest.raises(BadRequestException):
            store.delete_question(question["id"], "course-default")
        assert store.delete_paper(paper["id"], "course-default") is True
        assert store.delete_question(question["id"], "course-default") is True
        store.close()


class TestQuestionGeneration:
    def test_accepts_fenced_json_with_explanation(self):
        raw = "已依据资料生成：\n```json\n[{\"stem\":\"采样频率最低要求？\",\"options\":[],\"answer\":\"不低于最高频率两倍\",\"analysis\":\"依据采样定理\"}]\n```\n请查收。"
        questions = _parse_questions(raw, question_type="short_answer", count=1)
        assert questions[0]["answer"] == "不低于最高频率两倍"

    def test_accepts_questions_object_wrapper(self):
        raw = '{"questions":[{"stem":"采样频率最低要求？","options":[],"answer":"不低于最高频率两倍","analysis":"依据采样定理"}]}'
        questions = _parse_questions(raw, question_type="short_answer", count=1)
        assert len(questions) == 1

    def test_generates_only_from_retrieved_evidence(self, temp_dir):
        store = QuestionBankStore(str(temp_dir / "bank.db"))
        request = QuestionGenerateRequest(
            course_id="course-default", topic="采样定理", question_type="choice", count=1,
            scenario="考试", as_of="2026-09-01",
        )
        llm, vs = MagicMock(), MagicMock()
        llm.chat.return_value = '[{"stem":"采样频率最低要求是什么？","options":["最高频率两倍","最高频率","最高频率一半","任意频率"],"answer":"最高频率两倍","analysis":"资料给出奈奎斯特条件"}]'
        with patch("src.services.question_bank.retrieve", return_value=[_hit()]) as retrieve:
            result = generate_questions(request=request, vs=vs, llm=llm, store=store)
        assert result["grounded"] is True
        assert result["questions"][0]["origin"] == "agent"
        assert result["questions"][0]["citations"][0]["source_file"] == "signals.pdf"
        assert result["questions"][0]["scenario"] == "考试"
        assert retrieve.call_args.kwargs["course_id"] == "course-default"
        assert retrieve.call_args.kwargs["scenario"] == "考试"
        assert llm.chat.call_args.kwargs["max_tokens"] == 4096

    def test_scales_output_budget_for_many_questions(self):
        from src.services.question_bank import _question_max_tokens

        assert _question_max_tokens(1) == 4096
        assert _question_max_tokens(10) == 7000

    def test_does_not_save_when_evidence_is_empty(self, temp_dir):
        store = QuestionBankStore(str(temp_dir / "bank.db"))
        request = QuestionGenerateRequest(course_id="course-default", topic="采样定理")
        llm, vs = MagicMock(), MagicMock()
        with patch("src.services.question_bank.retrieve", return_value=[]):
            result = generate_questions(request=request, vs=vs, llm=llm, store=store)
        assert result == {"questions": [], "citations": [], "grounded": False}
        llm.chat.assert_not_called()
        assert store.list_questions("course-default") == []


class TestPaperAssembly:
    def test_reuses_evidence_backed_questions_before_generation(self, temp_dir):
        store = QuestionBankStore(str(temp_dir / "bank.db"))
        question = store.create_question(_payload(citations=[_citation()], status="reviewed", origin="agent", scenario="考试", as_of="2026-09-01"))
        request = PaperAssembleRequest(
            course_id="course-default", title="采样卷", topic="采样定理", scenario="考试", as_of="2026-09-01",
            rules=[{"question_type": "short_answer", "difficulty": "medium", "count": 1, "score": 10, "chapter": "第4章"}],
        )
        llm, vs = MagicMock(), MagicMock()
        result = assemble_paper(request=request, vs=vs, llm=llm, store=store)
        assert result["reused_count"] == 1
        assert result["generated_count"] == 0
        assert result["paper"]["items"][0]["question"]["id"] == question["id"]
        assert result["total_score"] == 10
        llm.chat.assert_not_called()

    def test_only_generates_missing_questions(self, temp_dir):
        store = QuestionBankStore(str(temp_dir / "bank.db"))
        existing = store.create_question(_payload(citations=[_citation()], origin="agent", scenario="考试", as_of="2026-09-01"))
        request = PaperAssembleRequest(
            course_id="course-default", title="采样卷", topic="采样定理", scenario="考试", as_of="2026-09-01",
            rules=[{"question_type": "short_answer", "difficulty": "medium", "count": 2, "score": 5, "chapter": "第4章"}],
        )
        def generate_missing(**_kwargs):
            generated = store.create_question(_payload(stem="采样定理另一题", citations=[_citation()], origin="agent", scenario="考试", as_of="2026-09-01"))
            return {"questions": [generated], "citations": [_citation()], "grounded": True}

        with patch("src.services.question_bank.generate_questions", side_effect=generate_missing) as generate:
            result = assemble_paper(request=request, vs=MagicMock(), llm=MagicMock(), store=store)
        assert result["reused_count"] == 1
        assert result["generated_count"] == 1
        assert result["paper"]["question_count"] == 2
        assert generate.call_args.kwargs["request"].count == 1
        assert result["paper"]["items"][0]["question"]["id"] == existing["id"]

    def test_fails_when_generation_is_disabled_and_bank_is_short(self, temp_dir):
        store = QuestionBankStore(str(temp_dir / "bank.db"))
        request = PaperAssembleRequest(
            course_id="course-default", title="采样卷", topic="采样定理", allow_generate=False,
            rules=[{"question_type": "choice", "difficulty": "hard", "count": 1, "score": 1}],
        )
        with pytest.raises(BadRequestException, match="缺少"):
            assemble_paper(request=request, vs=MagicMock(), llm=MagicMock(), store=store)


class TestQuestionBankApi:
    def test_create_and_list_are_course_scoped(self, temp_dir):
        store = QuestionBankStore(str(temp_dir / "bank.db"))
        catalog = MagicMock()
        app.dependency_overrides[get_question_bank_store] = lambda: store
        app.dependency_overrides[get_catalog_store] = lambda: catalog
        try:
            created = client.post("/api/v1/question-bank/questions", json=_payload()).json()["data"]
            assert created["origin"] == "manual"
            listed = client.get("/api/v1/question-bank/questions", params={"course_id": "course-default"})
            assert listed.status_code == 200
            assert [item["id"] for item in listed.json()["data"]] == [created["id"]]
            hidden = client.get("/api/v1/question-bank/questions", params={"course_id": "course-other"})
            assert hidden.json()["data"] == []
        finally:
            app.dependency_overrides.pop(get_question_bank_store, None)
            app.dependency_overrides.pop(get_catalog_store, None)
            store.close()

    def test_generate_forwards_course_to_service(self, temp_dir):
        store = QuestionBankStore(str(temp_dir / "bank.db"))
        catalog, llm, vs = MagicMock(), MagicMock(), MagicMock()
        app.dependency_overrides[get_question_bank_store] = lambda: store
        app.dependency_overrides[get_catalog_store] = lambda: catalog
        app.dependency_overrides[get_llm_client] = lambda: llm
        app.dependency_overrides[get_vector_store] = lambda: vs
        try:
            with patch("src.apis.v1.question_bank.generate_questions", return_value={"questions": [], "citations": [], "grounded": False}) as generate:
                response = client.post("/api/v1/question-bank/generate", json={"course_id": "course-default", "topic": "采样定理"})
        finally:
            app.dependency_overrides.pop(get_question_bank_store, None)
            app.dependency_overrides.pop(get_catalog_store, None)
            app.dependency_overrides.pop(get_llm_client, None)
            app.dependency_overrides.pop(get_vector_store, None)
            store.close()
        assert response.status_code == 200
        assert generate.call_args.kwargs["request"].course_id == "course-default"
        catalog.require_course.assert_called_once_with("course-default")

    def test_assemble_forwards_blueprint_to_service(self, temp_dir):
        store = QuestionBankStore(str(temp_dir / "bank.db"))
        catalog, llm, vs = MagicMock(), MagicMock(), MagicMock()
        app.dependency_overrides[get_question_bank_store] = lambda: store
        app.dependency_overrides[get_catalog_store] = lambda: catalog
        app.dependency_overrides[get_llm_client] = lambda: llm
        app.dependency_overrides[get_vector_store] = lambda: vs
        paper = {"id": "paper_1", "course_id": "course-default", "title": "试卷", "description": "", "question_count": 1, "total_score": 10, "created_at": "now", "updated_at": "now", "items": []}
        try:
            with patch("src.apis.v1.question_bank.assemble_paper", return_value={"paper": paper, "reused_count": 1, "generated_count": 0, "total_score": 10}) as assemble:
                response = client.post("/api/v1/question-bank/papers/assemble", json={
                    "course_id": "course-default", "title": "试卷", "topic": "采样定理",
                    "rules": [{"question_type": "short_answer", "count": 1, "score": 10}],
                })
        finally:
            app.dependency_overrides.pop(get_question_bank_store, None)
            app.dependency_overrides.pop(get_catalog_store, None)
            app.dependency_overrides.pop(get_llm_client, None)
            app.dependency_overrides.pop(get_vector_store, None)
            store.close()
        assert response.status_code == 200
        assert assemble.call_args.kwargs["request"].rules[0].count == 1
