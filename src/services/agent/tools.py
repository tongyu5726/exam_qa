"""Agent 工具：薄封装 services 现有能力，不重复实现 RAG。

工具集（对应笔记四）：search_pdf / read_page / extract_table /
analyze_chart / quote_source。每个工具返回值都携带引用来源（citations），
满足「可追溯引用」；TOOL_SCHEMAS 供 P2-C 决策环（function calling）使用。
"""

from __future__ import annotations

import logging

from src.exceptions import BadRequestException
from src.services.storage.vector_store import ChromaVectorStore

logger = logging.getLogger(__name__)

SUPPORTED_TOOLS = frozenset(
    {"search_pdf", "read_page", "extract_table", "analyze_chart", "quote_source"}
)


def retrieve_tool(
    query: str,
    vs: ChromaVectorStore,
    course_id: str,
    top_k: int,
    *,
    scenario: str | None = None,
    as_of: str | None = None,
) -> list[dict]:
    """调用检索主链路（向量 + BM25 → RRF），返回候选片段（不在此处做阈值过滤）。"""
    from src.services.retrieval import retrieve

    kwargs: dict[str, object] = {
        "query": query, "vs": vs, "course_id": course_id,
        "top_k": top_k, "score_threshold": 0.0,
    }
    if scenario:
        kwargs["scenario"] = scenario
    if as_of:
        kwargs["as_of"] = as_of
    return retrieve(**kwargs)


def _citation(hit: dict) -> dict:
    """把检索命中转成结构化引用来源（文件/页码/章节/切片路径）。"""
    meta = hit.get("metadata") or {}
    citation = {
        "source_file": meta.get("source_file") or "",
        "page": meta.get("page"),
        "chapter": meta.get("chapter") or "",
        "section_path": meta.get("section_path") or "",
        "block_type": meta.get("block_type") or "",
        "bbox": meta.get("bbox") or "",
        "pdf_page_label": meta.get("pdf_page_label") or "",
        "textbook_references": meta.get("textbook_references") or "",
        "content_role": meta.get("content_role") or "content",
        "parser_name": meta.get("parser_name") or "",
        "parse_quality": float(meta.get("parse_quality") or 0.0),
        "snippet": (hit.get("text") or "")[:200],
        "score": round(float(hit.get("score") or 0.0), 4),
    }
    # 让 Agent 能将检索命中继续交给 read_page 精读；对外 Citation 模型会忽略此内部辅助字段。
    if meta.get("doc_id"):
        citation["doc_id"] = str(meta["doc_id"])
    return citation


def _embed_query(query: str) -> list[float]:
    from src.services.embedding import get_embedding_client

    return get_embedding_client().embed([query])[0]


def search_pdf(
    query: str,
    vs: ChromaVectorStore,
    course_id: str,
    top_k: int = 5,
    *,
    scenario: str | None = None,
    as_of: str | None = None,
) -> dict:
    """search_pdf：检索课程资料中与问题相关的片段（向量 + BM25 → RRF）。"""
    kwargs: dict[str, object] = {}
    if scenario:
        kwargs["scenario"] = scenario
    if as_of:
        kwargs["as_of"] = as_of
    hits = retrieve_tool(query, vs, course_id, top_k, **kwargs)
    return {
        "query": query,
        "results": [
            {"text": h.get("text", ""), "metadata": h.get("metadata") or {}}
            for h in hits
        ],
        "citations": [_citation(h) for h in hits],
    }


def read_page(
    doc_id: str,
    page: int,
    vs: ChromaVectorStore,
    course_id: str,
    *,
    source_file: str | None = None,
    scenario: str | None = None,
    as_of: str | None = None,
) -> dict:
    """read_page：读取指定文档指定页的完整文本（含表格/图片摘要切片）。"""
    kwargs: dict[str, object] = {
        "course_id": course_id,
        "doc_id": doc_id,
        "source_file": source_file,
        "page": int(page),
    }
    if scenario:
        kwargs["scenario"] = scenario
    if as_of:
        kwargs["as_of"] = as_of
    hits = vs.get_chunks(**kwargs)
    if not hits:
        return {
            "doc_id": doc_id,
            "page": page,
            "found": False,
            "text": "",
            "citations": [],
        }
    text = "\n\n".join(h.get("text", "") for h in hits if h.get("text"))
    return {
        "doc_id": doc_id,
        "page": page,
        "found": True,
        "text": text,
        "citations": [_citation(h) for h in hits],
    }


def extract_table(
    vs: ChromaVectorStore,
    course_id: str,
    *,
    query: str | None = None,
    doc_id: str | None = None,
    source_file: str | None = None,
    page: int | None = None,
    top_k: int = 10,
    scenario: str | None = None,
    as_of: str | None = None,
) -> dict:
    """extract_table：抽取资料中的表格。

    给定 query 时按语义检索表格切片；否则按 doc_id/source_file/page 过滤取表。
    """
    if query:
        kwargs: dict[str, object] = {
            "top_k": top_k, "course_id": course_id, "block_type": "table",
        }
        if scenario:
            kwargs["scenario"] = scenario
        if as_of:
            kwargs["as_of"] = as_of
        hits = vs.search(_embed_query(query), **kwargs)
    else:
        kwargs = {
            "course_id": course_id,
            "doc_id": doc_id,
            "source_file": source_file,
            "page": page,
            "block_type": "table",
        }
        if scenario:
            kwargs["scenario"] = scenario
        if as_of:
            kwargs["as_of"] = as_of
        hits = vs.get_chunks(**kwargs)
    tables = [
        {
            "text": h.get("text", ""),
            "table_headers": (h.get("metadata") or {}).get("table_headers", ""),
            "page": (h.get("metadata") or {}).get("page"),
            "chapter": (h.get("metadata") or {}).get("chapter", ""),
            "section_path": (h.get("metadata") or {}).get("section_path", ""),
        }
        for h in hits
        if h.get("text")
    ]
    return {
        "query": query or "",
        "count": len(tables),
        "tables": tables,
        "citations": [_citation(h) for h in hits],
    }


def analyze_chart(
    query: str,
    vs: ChromaVectorStore,
    course_id: str,
    llm=None,
    *,
    doc_id: str | None = None,
    page: int | None = None,
    top_k: int = 10,
    scenario: str | None = None,
    as_of: str | None = None,
) -> dict:
    """analyze_chart：分析资料中的图片/图表。

    基于入库时生成的视觉摘要（VISUAL_MODEL）；未配置视觉模型或无摘要时
    返回提示，不编造内容。
    """
    hits: list[dict] = []
    if query:
        vec = _embed_query(query)
        for btype in ("image_summary", "image"):
            kwargs: dict[str, object] = {
                "top_k": top_k, "course_id": course_id, "block_type": btype,
            }
            if scenario:
                kwargs["scenario"] = scenario
            if as_of:
                kwargs["as_of"] = as_of
            hits.extend(vs.search(vec, **kwargs))
        hits.sort(key=lambda h: float(h.get("score") or 0.0), reverse=True)
        hits = hits[:top_k]
    else:
        for btype in ("image_summary", "image"):
            kwargs = {"course_id": course_id, "doc_id": doc_id, "page": page, "block_type": btype}
            if scenario:
                kwargs["scenario"] = scenario
            if as_of:
                kwargs["as_of"] = as_of
            hits.extend(vs.get_chunks(**kwargs))

    # 两种 block_type 检索可能重叠，按 chunk id 去重
    seen: set[str] = set()
    deduped: list[dict] = []
    for h in hits:
        key = h.get("id") or id(h)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
    hits = deduped
    summaries = [h.get("text", "").strip() for h in hits if h.get("text", "").strip()]
    if not summaries:
        return {
            "found": False,
            "analysis": "",
            "note": "未找到图表摘要；请确认已配置 VISUAL_MODEL 并对该文档重新入库",
            "citations": [],
        }
    if llm is None or not getattr(llm, "configured", False):
        return {
            "found": True,
            "analysis": "",
            "summaries": summaries,
            "citations": [_citation(h) for h in hits],
        }
    prompt = (
        "以下是课程资料中图片/图表的检索摘要，请结合用户问题给出分析结论。\n\n"
        f"用户问题：{query}\n\n图表摘要：\n"
        + "\n".join(f"- {s}" for s in summaries)
    )
    answer = (llm.chat([{"role": "user", "content": prompt}], temperature=0.2) or "").strip()
    return {
        "found": True,
        "analysis": answer,
        "summaries": summaries,
        "citations": [_citation(h) for h in hits],
    }


def quote_source(
    query: str,
    vs: ChromaVectorStore,
    course_id: str,
    top_k: int = 5,
    *,
    scenario: str | None = None,
    as_of: str | None = None,
) -> dict:
    """quote_source：返回检索结果的引用来源（文件/页码/章节/切片路径）。"""
    kwargs: dict[str, object] = {}
    if scenario:
        kwargs["scenario"] = scenario
    if as_of:
        kwargs["as_of"] = as_of
    hits = retrieve_tool(query, vs, course_id, top_k, **kwargs)
    return {
        "query": query,
        "count": len(hits),
        "citations": [_citation(h) for h in hits],
    }


# OpenAI 兼容 function calling schema（P2-C 决策环使用；course_id 由系统强制注入，不暴露给模型）
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_pdf",
            "description": "检索课程资料中与问题相关的片段（向量 + BM25 混合检索），返回片段文本与引用来源。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索查询句"},
                    "top_k": {"type": "integer", "description": "返回片段数，默认 5"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": "读取指定文档指定页的完整文本（含表格与图片摘要切片）。doc_id 与 source_file 至少给一个。",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "文档 ID（如检索结果 citation 中的 doc_id）"},
                    "page": {"type": "integer", "description": "页码，从 1 开始"},
                    "source_file": {"type": "string", "description": "源文件名（可选，替代 doc_id）"},
                },
                "required": ["page"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_table",
            "description": "抽取资料中的表格。给定 query 按语义检索表格；否则按文档/页码过滤取表，返回表格文本、列头与引用来源。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "表格相关的检索问题（可选）"},
                    "doc_id": {"type": "string", "description": "文档 ID（可选）"},
                    "source_file": {"type": "string", "description": "源文件名（可选）"},
                    "page": {"type": "integer", "description": "页码（可选）"},
                    "top_k": {"type": "integer", "description": "最多返回表格数，默认 10"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_chart",
            "description": "分析资料中的图片/图表（基于入库时生成的视觉摘要）。未配置视觉模型或无摘要时返回提示，不编造内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户问题（可选；不传则返回全部图表摘要）"},
                    "doc_id": {"type": "string", "description": "文档 ID（可选）"},
                    "page": {"type": "integer", "description": "页码（可选）"},
                    "top_k": {"type": "integer", "description": "最多取多少图表摘要，默认 10"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quote_source",
            "description": "返回检索结果的引用来源列表（文件、页码、章节、切片路径），用于追溯引用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索查询句"},
                    "top_k": {"type": "integer", "description": "最多返回引用数，默认 5"},
                },
                "required": ["query"],
            },
        },
    },
]


def execute_tool(
    name: str,
    args: dict,
    *,
    vs: ChromaVectorStore,
    course_id: str,
    llm=None,
    top_k: int = 5,
    scenario: str | None = None,
    as_of: str | None = None,
) -> dict:
    """工具白名单分发（P2-C 决策环执行节点）。未知工具拒绝，不静默忽略。"""
    if name not in SUPPORTED_TOOLS:
        raise BadRequestException(
            f"未知工具: {name}，可用工具: {', '.join(sorted(SUPPORTED_TOOLS))}"
        )
    if name == "search_pdf":
        return search_pdf(args.get("query", ""), vs, course_id, int(args.get("top_k") or top_k), scenario=scenario, as_of=as_of)
    if name == "read_page":
        return read_page(
            args.get("doc_id") or "",
            int(args["page"]),
            vs,
            course_id,
            source_file=args.get("source_file"),
            scenario=scenario,
            as_of=as_of,
        )
    if name == "extract_table":
        return extract_table(
            vs,
            course_id,
            query=args.get("query"),
            doc_id=args.get("doc_id"),
            source_file=args.get("source_file"),
            page=int(args["page"]) if args.get("page") is not None else None,
            top_k=int(args.get("top_k") or top_k),
            scenario=scenario,
            as_of=as_of,
        )
    if name == "analyze_chart":
        return analyze_chart(
            args.get("query", ""),
            vs,
            course_id,
            llm,
            doc_id=args.get("doc_id"),
            page=int(args["page"]) if args.get("page") is not None else None,
            top_k=int(args.get("top_k") or top_k),
            scenario=scenario,
            as_of=as_of,
        )
    if name == "quote_source":
        return quote_source(args.get("query", ""), vs, course_id, int(args.get("top_k") or top_k), scenario=scenario, as_of=as_of)
    raise BadRequestException(f"工具未实现: {name}")  # pragma: no cover
