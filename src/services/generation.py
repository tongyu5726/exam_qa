"""生成：拼 prompt、调 LLM、组装 citations。"""

import logging

from src.config import config
from src.exceptions import LLMAPIException
from src.services.evidence_metadata import evidence_reason
from src.services.llm import OpenAIClient

logger = logging.getLogger(__name__)

QA_SYSTEM_PROMPT = """你是一位课程答疑助教。请根据以下参考资料回答问题。

回答规则：
1. 优先使用参考资料中的内容，直接引用其中的定义与公式。
2. 数学公式用 LaTeX：行内用 $...$ 或 \(...\)，独立成行用 $$...$$ 或 \[...\]；勿把公式拆成裸字母拼写。
3. 参考资料出现“公式：$$...$$”时，它就是已经识别出的公式证据；必须使用，不得称其仍是图片、被省略或未收录。
4. 若资料声明「共 N 项」并提供连续编号，必须合并重叠片段、去重后完整列出；只有编号仍确实缺失时才说资料未包含。
5. 资料相关但不完整时，可据已有信息作答，并标明哪些来自资料。
6. 勿在资料之外自行补充知识点或公式；资料确无则写「资料未包含此内容」。
7. 不要在正文中输出【……】来源标签；系统会在回答下方单独展示结构化证据和源文档入口。
8. 表述简洁、准确，便于复习。"""

CONCEPT_SYSTEM_PROMPT = """你是一位课程答疑助教。学生在检索某个知识点，请根据参考资料做结构化聚合。

回答结构（按此顺序，缺则写「资料未包含」）：
1. **定义**：该知识点的核心定义与含义。
2. **公式**：相关公式（LaTeX：$...$ / $$...$$），并简要说明符号。
3. **例题**：资料中的例题或典型应用；若无例题，写明并给一句基于资料的应用提示（勿编造题目）。

其他规则：
- 只依据参考资料，勿补充资料外知识点。
- 参考资料出现“公式：$$...$$”时必须使用该公式，不得称其仍是图片、被省略或未收录。
- 不要在正文中输出【……】来源标签；系统会在回答下方展示证据。
- 表述简洁，便于复习。"""

CHAPTER_SYSTEM_PROMPT = """你是一位课程答疑助教。学生在做某一章的复习概览，请只根据参考资料生成本章提纲。

回答结构（按此顺序，缺则写「资料未包含」）：
1. **知识清单**：本章出现的主要知识点（条目列表）。
2. **重点标注**：资料中反复出现或明确强调的内容。
3. **推荐自测**：根据资料内容给出 3～5 道复习自测题（可简答/选择）；题目必须能从资料推出，勿编造资料外考点。

其他规则：
- 只依据参考资料，勿补充资料外知识点或章节。
- 不要在正文中输出【……】来源标签；系统会在回答下方展示证据。
- 表述简洁，便于期末复习。"""


def _format_context(chunks: list[dict]) -> str:
    parts = []
    for c in chunks:
        meta = c.get("metadata", {})
        src = meta.get("source_file", "未知")
        src_short = src.replace(".md", "").replace(".pdf", "").replace(".txt", "")
        text = c.get("text", "")
        first_line = text.split("\n", 1)[0].strip().lstrip("§ ")
        if len(first_line) > 40:
            first_line = first_line[:37] + "..."
        evidence_kind = "公式证据" if meta.get("block_type") == "formula" else "正文证据"
        parts.append(f"【{src_short} · {first_line} · {evidence_kind}】\n{text}")
    return "\n\n".join(parts)


def _build_citations(chunks: list[dict]) -> list[dict]:
    citations = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        citations.append(
            {
                "doc_id": str(meta.get("doc_id") or ""),
                "source_file": meta.get("source_file", "未知"),
                "page": meta.get("page"),
                "snippet": chunk.get("text", "")[:200].replace("\n", " "),
                "score": round(chunk.get("score", 0), 4),
                "source_version": meta.get("source_version", ""),
                "effective_from": meta.get("effective_from", "0001-01-01"),
                "effective_to": meta.get("effective_to", "9999-12-31"),
                "authority_level": int(meta.get("authority_level") or 30),
                "authority_label": meta.get("authority_label", "教学材料"),
                "applicability_scope": meta.get("applicability_scope", "all"),
                "selection_reason": evidence_reason(meta, scenario=None, as_of=None),
                "chapter": meta.get("chapter", ""),
                "section_path": meta.get("section_path", ""),
                "block_type": meta.get("block_type", ""),
                "bbox": meta.get("bbox", ""),
                "pdf_page_label": meta.get("pdf_page_label", ""),
                "textbook_references": meta.get("textbook_references", ""),
                "content_role": meta.get("content_role", "content"),
                "parser_name": meta.get("parser_name", ""),
                "parse_quality": float(meta.get("parse_quality") or 0.0),
            }
        )
    return citations


def _build_messages(
    context: list[dict],
    question: str,
    mode: str = "qa",
    history: list[dict] | None = None,
) -> list[dict]:
    ctx = _format_context(context)
    if mode == "concept":
        system, user = CONCEPT_SYSTEM_PROMPT, f"参考资料：\n\n{ctx}\n\n请聚合知识点「{question}」（定义→公式→例题）。"
    elif mode == "chapter":
        system, user = (
            CHAPTER_SYSTEM_PROMPT,
            f"参考资料：\n\n{ctx}\n\n请生成章节「{question}」的概览（知识清单→重点→推荐自测）。",
        )
    else:
        system, user = QA_SYSTEM_PROMPT, f"参考资料：\n\n{ctx}\n\n学生问题：{question}"

    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user})
    return messages


def generate(
    context: list[dict],
    question: str,
    llm: OpenAIClient,
    mode: str = "qa",
    history: list[dict] | None = None,
) -> dict:
    if not context:
        return {"answer": "资料库中未找到相关内容", "citations": [], "grounded": False}
    answer = llm.chat(
        _build_messages(context, question, mode=mode, history=history),
        temperature=config.llm.temperature,
    )
    logger.info("LLM 生成完成: mode=%s len=%d", mode, len(answer))
    return {"answer": answer, "citations": _build_citations(context), "grounded": True}


def stream_generate(
    context: list[dict],
    question: str,
    llm: OpenAIClient,
    mode: str = "qa",
    history: list[dict] | None = None,
):
    parts: list[str] = []
    for chunk in llm.chat_stream(
        _build_messages(context, question, mode=mode, history=history),
        temperature=config.llm.temperature,
        emit_status=True,
    ):
        if isinstance(chunk, dict):
            if chunk.get("type") == "status" and chunk.get("status") == "reasoning":
                yield {"type": "phase", "phase": "reasoning"}
                continue
            delta = str(chunk.get("text") or "") if chunk.get("type") == "content" else ""
        else:
            # 兼容测试桩及其他实现 LLMClient 协议的客户端。
            delta = str(chunk or "")
        if not delta:
            continue
        parts.append(delta)
        yield {"type": "delta", "text": delta}
    if not parts:
        raise LLMAPIException(
            "模型没有返回可显示的答案。请重试，或检查模型输出长度配置。"
        )
    logger.info("LLM 生成完成: mode=%s len=%d stream=true", mode, len("".join(parts)))
    yield {
        "type": "done",
        "data": {
            "answer": "".join(parts),
            "citations": _build_citations(context),
            "grounded": True,
        },
    }
