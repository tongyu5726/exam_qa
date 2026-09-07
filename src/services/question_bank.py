"""题库服务：先检索有效课程证据，再让 LLM 生成可保存的题目草稿。"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

from src.config import config
from src.exceptions import BadRequestException
from src.models import QuestionGenerateRequest
from src.services.evidence_metadata import evidence_reason, normalize_scope
from src.services.llm import OpenAIClient
from src.services.retrieval import retrieve
from src.services.storage.question_bank_store import QuestionBankStore
from src.services.storage.vector_store import ChromaVectorStore

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
logger = logging.getLogger(__name__)
# 深度推理模型会把推理 token 计入 max_tokens。题库同时需要结构化题目正文，
# 因而不能沿用普通问答的 2048 默认上限。
_QUESTION_MIN_MAX_TOKENS = 4096
_QUESTION_MAX_MAX_TOKENS = 8192

_QUESTION_SYSTEM = """你是严谨的课程命题助教。只能依据给定课程资料出题，不能使用资料外知识。
输出必须是 JSON 数组；每项只能有 stem、options、answer、analysis 字段。不要输出 Markdown 或其他文字。
题干必须可由资料直接验证；解析要说明依据的资料事实，不要捏造出处。选择题 options 必须为 4 个字符串，answer 使用正确选项的完整文本；填空题与简答题 options 必须是空数组。"""


def _citations(hits: list[dict], scenario: str | None, as_of: str | None) -> list[dict]:
    result: list[dict] = []
    seen: set[tuple] = set()
    for hit in hits:
        meta = hit.get("metadata") or {}
        citation = {
            "doc_id": str(meta.get("doc_id") or ""),
            "source_file": meta.get("source_file", "未知"),
            "page": meta.get("page"),
            "snippet": (hit.get("text") or "")[:200].replace("\n", " "),
            "score": round(float(hit.get("score") or 0), 4),
            "source_version": meta.get("source_version", ""),
            "effective_from": meta.get("effective_from", "0001-01-01"),
            "effective_to": meta.get("effective_to", "9999-12-31"),
            "authority_level": int(meta.get("authority_level") or 30),
            "authority_label": meta.get("authority_label", "教学材料"),
            "applicability_scope": meta.get("applicability_scope", "all"),
            "selection_reason": evidence_reason(meta, scenario=scenario, as_of=as_of),
        }
        key = (citation["source_file"], citation["page"], citation["snippet"])
        if key not in seen:
            seen.add(key)
            result.append(citation)
    return result


def _context(hits: list[dict]) -> str:
    parts = []
    for index, hit in enumerate(hits[:12], 1):
        meta = hit.get("metadata") or {}
        label = f"资料 {index}（{meta.get('source_file', '未知')}，第 {meta.get('page') or '?'} 页）"
        parts.append(f"[{label}]\n{(hit.get('text') or '')[:2400]}")
    return "\n\n".join(parts)


def _extract_question_list(raw: str) -> list:
    """兼容模型的 JSON 围栏、前后说明，以及 {"questions": [...]} 包装。

    只接受 JSON 解码得到的题目数组，不尝试修补题目内容，后续仍严格校验
    题型、选项数、答案与请求题数，避免宽松解析绕过受证据出题约束。
    """
    text = _JSON_FENCE.sub("", (raw or "").strip()).strip()
    decoder = json.JSONDecoder()
    starts = [0, *[
        index for index, char in enumerate(text)
        if index and char in "[{"
    ]]
    for start in starts:
        try:
            value, _end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and isinstance(value.get("questions"), list):
            return value["questions"]
    logger.warning("出题模型响应未包含可解析的题目 JSON（长度=%d）", len(text))
    raise BadRequestException("出题模型未返回有效 JSON，请重试")


def _parse_questions(raw: str, *, question_type: str, count: int) -> list[dict]:
    data = _extract_question_list(raw)
    items: list[dict] = []
    for item in data[:count]:
        if not isinstance(item, dict):
            continue
        stem, answer = str(item.get("stem") or "").strip(), str(item.get("answer") or "").strip()
        options = item.get("options") or []
        if not isinstance(options, list):
            options = []
        options = [str(value).strip()[:1000] for value in options if str(value).strip()]
        if not stem or not answer:
            continue
        if question_type == "choice":
            if len(options) != 4 or answer not in options:
                continue
        else:
            options = []
        items.append({
            "stem": stem[:4000], "options": options, "answer": answer[:4000],
            "analysis": str(item.get("analysis") or "").strip()[:6000],
        })
    if len(items) != count:
        raise BadRequestException("出题结果不符合题型或题数约束，请重试")
    return items


def _question_max_tokens(count: int) -> int:
    """按题数为出题分配推理与最终 JSON 的共享输出预算。"""
    return min(_QUESTION_MAX_MAX_TOKENS, max(_QUESTION_MIN_MAX_TOKENS, count * 700))


def generate_questions(
    *, request, vs: ChromaVectorStore, llm: OpenAIClient, store: QuestionBankStore,
) -> dict:
    """生成并保存题目草稿；无有效证据时显式拒绝且不写入题库。"""
    scenario = normalize_scope(request.scenario) if request.scenario else None
    if request.as_of:
        try:
            date.fromisoformat(request.as_of)
        except ValueError as exc:
            raise BadRequestException("as_of 必须是有效日期，格式 YYYY-MM-DD") from exc
    query = " ".join(part for part in (request.chapter, request.topic) if part).strip()
    hits = retrieve(
        query=query, vs=vs, course_id=request.course_id,
        top_k=max(5, min(request.count * 3, 20)),
        score_threshold=config.retrieval.score_threshold,
        scenario=scenario, as_of=request.as_of,
    )
    citations = _citations(hits, scenario, request.as_of)
    if not hits:
        return {"questions": [], "citations": [], "grounded": False}
    user = (
        f"出题主题：{request.topic}\n题型：{request.question_type}\n难度：{request.difficulty}\n"
        f"题数：{request.count}\n章节：{request.chapter or '未指定'}\n\n课程资料：\n{_context(hits)}"
    )
    generated = _parse_questions(
        llm.chat(
            [
                {"role": "system", "content": _QUESTION_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=_question_max_tokens(request.count),
        ),
        question_type=request.question_type, count=request.count,
    )
    questions = [
        store.create_question({
            **item, "course_id": request.course_id, "question_type": request.question_type,
            "difficulty": request.difficulty, "chapter": request.chapter, "citations": citations,
            "scenario": scenario or "", "as_of": request.as_of or "", "status": "draft", "origin": "agent",
        })
        for item in generated
    ]
    return {"questions": questions, "citations": citations, "grounded": True}


def _question_has_usable_evidence(question: dict, scenario: str | None, as_of: str | None) -> bool:
    """组卷只复用可追溯的题目；场景/时效要求存在时逐条校验其引用。"""
    citations = question.get("citations") or []
    if not citations:
        return False
    for citation in citations:
        scope = str(citation.get("applicability_scope") or "all")
        if scenario and scenario != "all" and scope not in ("all", scenario):
            continue
        if as_of:
            start = str(citation.get("effective_from") or "0001-01-01")
            end = str(citation.get("effective_to") or "9999-12-31")
            if not (start <= as_of <= end):
                continue
        return True
    return False


def assemble_paper(
    *, request, vs: ChromaVectorStore, llm: OpenAIClient, store: QuestionBankStore,
) -> dict:
    """按蓝图确定性选题，缺题时才使用受证据约束的生成服务补足。"""
    scenario = normalize_scope(request.scenario) if request.scenario else None
    if request.as_of:
        try:
            date.fromisoformat(request.as_of)
        except ValueError as exc:
            raise BadRequestException("as_of 必须是有效日期，格式 YYYY-MM-DD") from exc

    chosen: list[dict] = []
    chosen_ids: set[str] = set()
    reused_count = generated_count = 0
    for rule in request.rules:
        candidates = store.list_questions(
            request.course_id, question_type=rule.question_type,
            difficulty=rule.difficulty, chapter=rule.chapter or None,
        )
        reusable = [
            question for question in candidates
            if question["id"] not in chosen_ids and _question_has_usable_evidence(question, scenario, request.as_of)
        ]
        selected = reusable[:rule.count]
        chosen.extend({"question": question, "score": rule.score} for question in selected)
        chosen_ids.update(question["id"] for question in selected)
        reused_count += len(selected)

        missing = rule.count - len(selected)
        if not missing:
            continue
        if not request.allow_generate:
            raise BadRequestException(
                f"题库中缺少 {missing} 道 {rule.question_type}/{rule.difficulty} 题；可开启 allow_generate 补题"
            )
        generated = generate_questions(
            request=QuestionGenerateRequest(
                course_id=request.course_id, topic=request.topic, question_type=rule.question_type,
                difficulty=rule.difficulty, count=missing, chapter=rule.chapter,
                scenario=scenario, as_of=request.as_of,
            ),
            vs=vs, llm=llm, store=store,
        )
        if not generated["grounded"] or len(generated["questions"]) != missing:
            raise BadRequestException("资料不足，无法按蓝图补全试卷")
        for question in generated["questions"]:
            if (
                question.get("question_type") != rule.question_type
                or question.get("difficulty") != rule.difficulty
                or (rule.chapter and question.get("chapter") != rule.chapter)
            ):
                raise BadRequestException("新生成题目不符合组卷蓝图，已拒绝组卷")
            if not _question_has_usable_evidence(question, scenario, request.as_of):
                raise BadRequestException("新生成题目缺少有效资料证据，已拒绝组卷")
            chosen.append({"question": question, "score": rule.score})
            chosen_ids.add(question["id"])
        generated_count += missing

    expected_count = sum(rule.count for rule in request.rules)
    if len(chosen) != expected_count or len(chosen_ids) != expected_count:
        raise BadRequestException("组卷校验失败：题目数量不足或存在重复")
    if any(item["question"].get("course_id") != request.course_id for item in chosen):
        raise BadRequestException("组卷校验失败：发现跨课程题目")

    paper = store.create_paper(
        request.course_id, request.title, request.description,
        [{"question_id": item["question"]["id"], "score": item["score"]} for item in chosen],
    )
    return {
        "paper": paper, "reused_count": reused_count, "generated_count": generated_count,
        "total_score": paper["total_score"],
    }
