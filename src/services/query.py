"""查询编排：mode 路由 → 检索 / 章节聚合 → 生成 / 拒答。"""

import logging
from datetime import date

from src.config import config
from src.exceptions import BadRequestException, LLMAPIException
from src.models import AnswerData, Citation, IntentData
from src.services.evidence_metadata import evidence_reason
from src.services.generation import generate, stream_generate
from src.services.intent import IntentDecision, resolve_intent
from src.services.llm import OpenAIClient
from src.services.retrieval import retrieve
from src.services.storage.conversation_store import ConversationStore
from src.services.storage.vector_store import ChromaVectorStore

logger = logging.getLogger(__name__)

SUPPORTED_MODES = frozenset({"auto", "qa", "concept", "chapter"})
CONCEPT_TOP_K = 12
CHAPTER_MAX_CHUNKS = 24
_REFUSAL = "资料库中未找到相关内容"
_QUESTION_BANK_ROUTE = (
    "已识别为出题请求。请进入“我的题库”设置题型、数量、难度和章节；"
    "题库 Agent 会先检索当前课程资料，再生成并保存题目。"
)


def _validate(mode: str, course_id: str) -> None:
    if mode not in SUPPORTED_MODES:
        raise BadRequestException(f"仅支持 mode=auto|qa|concept|chapter，收到 mode={mode}")
    if not course_id or not course_id.strip():
        raise BadRequestException("course_id 不能为空")


def _validate_as_of(as_of: str | None) -> None:
    if not as_of:
        return
    try:
        date.fromisoformat(as_of)
    except ValueError as exc:
        raise BadRequestException("as_of 必须是有效日期，格式 YYYY-MM-DD") from exc


def _previous_intent(store: ConversationStore | None, conv_id: str | None) -> dict | None:
    if not store or not conv_id:
        return None
    try:
        return store.get_latest_user_intent(conv_id)
    except Exception:
        logger.warning("读取会话意图失败 conv=%s", conv_id, exc_info=True)
        return None


def _intent_data(intent: IntentDecision) -> IntentData:
    return IntentData(**intent.to_dict())


def _citations(
    raw: list[dict], *, scenario: str | None = None, as_of: str | None = None
) -> list[Citation]:
    return [
        Citation(
            doc_id=str(c.get("doc_id") or ""),
            source_file=c["source_file"],
            page=c.get("page"),
            snippet=c["snippet"],
            score=c["score"],
            source_version=c.get("source_version", ""),
            effective_from=c.get("effective_from", "0001-01-01"),
            effective_to=c.get("effective_to", "9999-12-31"),
            authority_level=int(c.get("authority_level") or 30),
            authority_label=c.get("authority_label", "教学材料"),
            applicability_scope=c.get("applicability_scope", "all"),
            selection_reason=evidence_reason(c, scenario=scenario, as_of=as_of),
            chapter=c.get("chapter", ""),
            section_path=c.get("section_path", ""),
            block_type=c.get("block_type", ""),
            bbox=c.get("bbox", ""),
            pdf_page_label=c.get("pdf_page_label", ""),
            textbook_references=c.get("textbook_references", ""),
            content_role=c.get("content_role", "content"),
            parser_name=c.get("parser_name", ""),
            parse_quality=float(c.get("parse_quality") or 0.0),
        )
        for c in raw
    ]


def _retrieve(
    question: str,
    mode: str,
    vs: ChromaVectorStore,
    course_id: str,
    *,
    scenario: str | None = None,
    as_of: str | None = None,
) -> list[dict]:
    if mode == "chapter":
        kwargs: dict[str, str] = {}
        if scenario:
            kwargs["scenario"] = scenario
        if as_of:
            kwargs["as_of"] = as_of
        hits = vs.get_by_chapter(course_id, question.strip(), **kwargs)
        capped: list[dict] = []
        for h in hits[:CHAPTER_MAX_CHUNKS]:
            item = dict(h)
            meta = dict(h.get("metadata") or {})
            item["metadata"] = meta
            item["score"] = 1.0
            capped.append(item)
        return capped
    return retrieve(
        query=question,
        vs=vs,
        course_id=course_id,
        top_k=CONCEPT_TOP_K if mode == "concept" else None,
        scenario=scenario,
        as_of=as_of,
    )


def ask(
    question: str,
    mode: str,
    vs: ChromaVectorStore,
    llm: OpenAIClient,
    course_id: str,
    conversation_store: ConversationStore | None = None,
    conversation_id: str | None = None,
    scenario: str | None = None,
    as_of: str | None = None,
) -> AnswerData:
    _validate(mode, course_id)
    _validate_as_of(as_of)
    history = _get_history(conversation_store, conversation_id)
    intent = resolve_intent(
        question, requested_mode=mode, scenario=scenario, as_of=as_of,
        previous=_previous_intent(conversation_store, conversation_id), llm=llm,
    )
    mode, scenario, as_of = intent.mode, intent.scenario, intent.as_of
    _validate_as_of(as_of)
    logger.info("查询: course=%s intent=%s layer=%s mode=%s scenario=%s as_of=%s conv=%s q='%s...'", course_id, intent.task, intent.layer, mode, scenario or "all", as_of or "latest", conversation_id, question[:60])

    if intent.task == "question_generate":
        _save_turn(
            conversation_store, conversation_id, course_id, question,
            _QUESTION_BANK_ROUTE, [], False, mode, intent=intent.to_dict(),
        )
        return AnswerData(
            answer=_QUESTION_BANK_ROUTE,
            citations=[],
            grounded=False,
            intent=_intent_data(intent),
        )

    hits = _retrieve(question, mode, vs, course_id, scenario=scenario, as_of=as_of)
    if not hits:
        logger.info("拒答 threshold=%.4f", config.retrieval.score_threshold)
        _save_turn(conversation_store, conversation_id, course_id, question, _REFUSAL, [], False, mode, intent=intent.to_dict())
        return AnswerData(answer=_REFUSAL, citations=[], grounded=False, intent=_intent_data(intent))

    if not llm.configured:
        raise LLMAPIException("AI 服务未配置：请设置 LLM_API_KEY")

    result = generate(context=hits, question=question, llm=llm, mode=mode, history=history)
    _save_turn(
        conversation_store, conversation_id, course_id,
        question, result["answer"], result["citations"], result["grounded"], mode, intent=intent.to_dict(),
    )
    return AnswerData(
        answer=result["answer"],
        citations=_citations(result["citations"], scenario=scenario, as_of=as_of),
        grounded=result["grounded"],
        intent=_intent_data(intent),
    )


def ask_stream(
    question: str,
    mode: str,
    vs: ChromaVectorStore,
    llm: OpenAIClient,
    course_id: str,
    conversation_store: ConversationStore | None = None,
    conversation_id: str | None = None,
    scenario: str | None = None,
    as_of: str | None = None,
):
    _validate(mode, course_id)
    _validate_as_of(as_of)
    history = _get_history(conversation_store, conversation_id)
    intent = resolve_intent(
        question, requested_mode=mode, scenario=scenario, as_of=as_of,
        previous=_previous_intent(conversation_store, conversation_id), llm=llm,
    )
    mode, scenario, as_of = intent.mode, intent.scenario, intent.as_of
    _validate_as_of(as_of)
    logger.info("流式查询: course=%s intent=%s layer=%s mode=%s scenario=%s as_of=%s conv=%s q='%s...'", course_id, intent.task, intent.layer, mode, scenario or "all", as_of or "latest", conversation_id, question[:60])
    if intent.task == "question_generate":
        yield {"type": "phase", "phase": "routing", "intent": intent.to_dict()}
        _save_turn(
            conversation_store, conversation_id, course_id, question,
            _QUESTION_BANK_ROUTE, [], False, mode, intent=intent.to_dict(),
        )
        yield {
            "type": "done",
            "data": AnswerData(
                answer=_QUESTION_BANK_ROUTE,
                citations=[],
                grounded=False,
                intent=_intent_data(intent),
            ).model_dump(),
        }
        return
    yield {"type": "phase", "phase": "retrieving", "intent": intent.to_dict()}

    hits = _retrieve(question, mode, vs, course_id, scenario=scenario, as_of=as_of)
    if not hits:
        _save_turn(conversation_store, conversation_id, course_id, question, _REFUSAL, [], False, mode, intent=intent.to_dict())
        yield {
            "type": "done",
            "data": AnswerData(answer=_REFUSAL, citations=[], grounded=False, intent=_intent_data(intent)).model_dump(),
        }
        return

    if not llm.configured:
        yield {"type": "error", "message": "AI 服务未配置：请设置 LLM_API_KEY"}
        return

    yield {"type": "phase", "phase": "generating"}
    for event in stream_generate(context=hits, question=question, llm=llm, mode=mode, history=history):
        if event["type"] in {"phase", "delta"}:
            yield event
        elif event["type"] == "done":
            data = event["data"]
            _save_turn(
                conversation_store, conversation_id, course_id,
                question, data["answer"], data["citations"], data["grounded"], mode, intent=intent.to_dict(),
            )
            yield {
                "type": "done",
                "data": AnswerData(
                    answer=data["answer"],
                    citations=_citations(data["citations"], scenario=scenario, as_of=as_of),
                    grounded=data["grounded"],
                    intent=_intent_data(intent),
                ).model_dump(),
            }


def _get_history(store: ConversationStore | None, conv_id: str | None) -> list[dict] | None:
    """获取对话历史消息，用于拼入 LLM 上下文。"""
    if not store or not conv_id:
        return None
    return store.get_history(conv_id) or None


def _save_turn(
    store: ConversationStore | None,
    conv_id: str | None,
    course_id: str,
    question: str,
    answer: str,
    citations: list,
    grounded: bool,
    mode: str,
    intent: dict | None = None,
) -> bool:
    """保存一轮问答到对话存储，自动创建不存在的对话。

    失败不阻断回答流程，但会记录 ERROR 日志；返回是否保存成功，便于调用方观测。
    """
    if not store or not conv_id:
        return True
    try:
        first_q = question.strip()
        title = first_q[:40] + ("..." if len(first_q) > 40 else "")
        store.ensure_conversation(conv_id, course_id, title)
        store.append_message(conv_id, "user", question, mode=mode, intent=intent)
        store.append_message(conv_id, "assistant", answer, citations=citations, grounded=grounded, mode=mode)
        return True
    except Exception:
        logger.exception(
            "保存对话消息失败 conv=%s course=%s mode=%s q='%s...'",
            conv_id,
            course_id,
            mode,
            question.strip()[:40],
        )
        return False
