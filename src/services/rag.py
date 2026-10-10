"""RAG capability used by Agent tools: retrieve, verify, generate or refuse."""
from collections.abc import Iterator

from src.services.storage.vector_store import ChromaVectorStore
from src.services.llm import OpenAIClient
from src.exceptions import LLMAPIException
from src.services.generation import generate, stream_generate
from src.services.retrieval import retrieve, retrieve_visual_evidence
from src.services.visual_evidence import (
    VISUAL_QUERY_RE as _VISUAL_QUERY_RE,
    needs_visual_verification as _needs_visual_verification,
    verify_visual_evidence as _verify_visual_evidence,
)

CONCEPT_TOP_K = 12
CHAPTER_MAX_CHUNKS = 24
_REFUSAL = "资料库中未找到相关内容"


def _retrieve(
    question: str,
    mode: str,
    vs: ChromaVectorStore,
    course_id: str,
    *,
    scenario: str | None = None,
    as_of: str | None = None,
    top_k: int | None = None,
    score_threshold: float | None = None,
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
    options = {} if score_threshold is None else {"score_threshold": score_threshold}
    hits = retrieve(
        query=question,
        vs=vs,
        course_id=course_id,
        top_k=top_k if top_k is not None else CONCEPT_TOP_K if mode == "concept" else None,
        scenario=scenario,
        as_of=as_of,
        **options,
    )
    if not _VISUAL_QUERY_RE.search(question):
        return hits

    visual_hits = retrieve_visual_evidence(
        question,
        vs,
        course_id,
        scenario=scenario,
        as_of=as_of,
    )
    if not visual_hits:
        return hits
    seen = {str(hit.get("id") or "") for hit in hits}
    for hit in visual_hits:
        chunk_id = str(hit.get("id") or "")
        if chunk_id and chunk_id not in seen:
            seen.add(chunk_id)
            hits.append(hit)
    return hits


def answer(
    question: str, mode: str, vs: ChromaVectorStore, llm: OpenAIClient, course_id: str,
    *, history: list[dict] | None = None, scenario: str | None = None,
    as_of: str | None = None, top_k: int | None = None, score_threshold: float | None = None,
) -> dict:
    hits = _retrieve(question, mode, vs, course_id, scenario=scenario, as_of=as_of,
                     top_k=top_k, score_threshold=score_threshold)
    if not hits:
        return {"answer": _REFUSAL, "citations": [], "grounded": False}
    hits = _verify_visual_evidence(question, hits)
    if not llm.configured:
        raise LLMAPIException("AI 服务未配置：请设置 LLM_API_KEY")
    return generate(context=hits, question=question, llm=llm, mode=mode, history=history)


def answer_stream(
    question: str, mode: str, vs: ChromaVectorStore, llm: OpenAIClient, course_id: str,
    *, history: list[dict] | None = None, scenario: str | None = None,
    as_of: str | None = None, top_k: int | None = None, score_threshold: float | None = None,
) -> Iterator[dict]:
    yield {"type": "phase", "phase": "retrieving"}
    hits = _retrieve(question, mode, vs, course_id, scenario=scenario, as_of=as_of,
                     top_k=top_k, score_threshold=score_threshold)
    if not hits:
        yield {"type": "done", "data": {"answer": _REFUSAL, "citations": [], "grounded": False}}
        return
    if _needs_visual_verification(question, hits):
        from src.dependencies import get_vision_client
        if get_vision_client().configured:
            yield {"type": "phase", "phase": "analyzing_visual"}
            hits = _verify_visual_evidence(question, hits)
    if not llm.configured:
        raise LLMAPIException("AI 服务未配置：请设置 LLM_API_KEY")
    yield {"type": "phase", "phase": "generating"}
    yield from stream_generate(context=hits, question=question, llm=llm, mode=mode, history=history)
