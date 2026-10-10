"""统一检索命中的引用属性；证据选择及去重由调用方负责。"""

from src.services.evidence_metadata import evidence_reason


def build_citation(
    hit: dict, *, scenario: str | None = None, as_of: str | None = None,
    evidence_role: str = "retrieval_candidate", single_line: bool = True,
) -> dict:
    meta = hit.get("metadata") or {}
    snippet = (hit.get("text") or "")[:200]
    return {
        "doc_id": str(meta.get("doc_id") or ""),
        "source_file": meta.get("source_file", "未知"),
        "page": meta.get("page"),
        "snippet": snippet.replace("\n", " ") if single_line else snippet,
        "score": round(float(hit.get("score") or 0), 4),
        "evidence_role": evidence_role,
        "source_version": meta.get("source_version", ""),
        "effective_from": meta.get("effective_from", "0001-01-01"),
        "effective_to": meta.get("effective_to", "9999-12-31"),
        "authority_level": int(meta.get("authority_level") or 30),
        "authority_label": meta.get("authority_label", "教学材料"),
        "applicability_scope": meta.get("applicability_scope", "all"),
        "selection_reason": evidence_reason(meta, scenario=scenario, as_of=as_of),
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
