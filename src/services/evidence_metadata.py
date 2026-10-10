"""证据元数据：从资料正文抽取版本、时效、权威与适用范围。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date


_DATE = r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?"
_VERSION_PATTERNS = (
    re.compile(r"(?:版本|版次|version|ver\.?|v)\s*[:：#]?[\s]*([0-9]+(?:\.[0-9]+){0,3})", re.I),
    re.compile(r"第\s*([0-9一二三四五六七八九十]+)\s*版"),
)
_EFFECTIVE_FROM = re.compile(
    rf"(?:生效日期|施行日期|实施日期|自|effective\s*date)\s*[:：]?\s*{_DATE}",
    re.I,
)
_EFFECTIVE_TO = re.compile(
    rf"(?:失效日期|有效期至|截至|effective\s*(?:to|until))\s*[:：]?\s*{_DATE}",
    re.I,
)
_SCOPE = re.compile(
    r"(?:适用范围|适用场景|适用对象|适用于)\s*[:：]?\s*([^\n。；;]{2,100})",
    re.I,
)

# 数值越大，代表在冲突时更应被优先采用；仍需先通过场景与生效时间过滤。
_AUTHORITY_RULES: tuple[tuple[re.Pattern, int, str], ...] = (
    (re.compile(r"(?:中华人民共和国)?(?:法律|国务院令|行政法规|国务院)"), 100, "国家法律法规"),
    (re.compile(r"(?:国家标准|GB/T?|标准号\s*GB)"), 90, "国家标准"),
    (re.compile(r"(?:教育部|国家部委|部令)"), 80, "部委规范"),
    (re.compile(r"(?:行业标准|协会标准|团体标准)"), 70, "行业规范"),
    (re.compile(r"(?:省|市|自治区).{0,12}(?:规定|办法|通知)"), 60, "地方规范"),
    (re.compile(r"(?:学校|大学|学院).{0,20}(?:规定|办法|通知|制度)"), 50, "机构文件"),
    (re.compile(r"(?:课程组|教研室|教学团队)"), 40, "课程资料"),
    (re.compile(r"(?:讲义|课件|课堂笔记|教师)"), 30, "教学材料"),
    (re.compile(r"(?:草案|征求意见|讨论稿|试行)"), 10, "草案或试行稿"),
)


@dataclass(frozen=True)
class EvidenceMetadata:
    source_version: str = ""
    effective_from: str = "0001-01-01"
    effective_to: str = "9999-12-31"
    authority_level: int = 30
    authority_label: str = "教学材料"
    applicability_scope: str = "all"
    metadata_confidence: float = 0.0
    metadata_source: str = "auto"

    def to_dict(self) -> dict:
        return asdict(self)


def _iso(match: re.Match[str] | None) -> str | None:
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def normalize_scope(value: str | None) -> str:
    """固定场景键：支持中文、字母、数字、下划线和短横线；空值为 all。"""
    text = (value or "").strip().lower()
    if not text:
        return "all"
    text = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", text).strip("_-")
    return text or "all"


def maintained_scopes(documents: list[dict]) -> list[str]:
    """仅公开已入库、活跃资料中的人工场景键。"""
    return sorted({
        normalize_scope(doc.get("applicability_scope"))
        for doc in documents
        if doc.get("metadata_source") == "manual"
        and doc.get("status") == "done" and doc.get("is_active", True)
        and normalize_scope(doc.get("applicability_scope")) != "all"
    })


def extract_evidence_metadata(text: str, filename: str = "") -> EvidenceMetadata:
    sample = (text or "")[:120_000]
    version = ""
    for pattern in _VERSION_PATTERNS:
        found = pattern.search(sample)
        if found:
            version = found.group(1).strip()
            break

    effective_from = _iso(_EFFECTIVE_FROM.search(sample)) or "0001-01-01"
    effective_to = _iso(_EFFECTIVE_TO.search(sample)) or "9999-12-31"
    authority_level, authority_label = 30, "教学材料"
    for pattern, level, label in _AUTHORITY_RULES:
        if pattern.search(sample) or pattern.search(filename):
            authority_level, authority_label = level, label
            break

    scope_hit = _SCOPE.search(sample)
    # 自动抽取到的自然语言范围不直接作为检索键，避免误过滤；由人工覆盖为受控键。
    scope = "all"
    confidence = 0.20
    if version:
        confidence += 0.20
    if effective_from != "0001-01-01":
        confidence += 0.25
    if authority_label != "教学材料":
        confidence += 0.25
    if scope_hit:
        confidence += 0.10

    return EvidenceMetadata(
        source_version=version,
        effective_from=effective_from,
        effective_to=effective_to,
        authority_level=authority_level,
        authority_label=authority_label,
        applicability_scope=scope,
        metadata_confidence=min(confidence, 0.95),
        metadata_source="auto",
    )


def evidence_reason(metadata: dict, *, scenario: str | None, as_of: str | None) -> str:
    bits = []
    if scenario:
        bits.append(f"适用场景={metadata.get('applicability_scope') or 'all'}")
    if as_of:
        bits.append(
            f"生效期={metadata.get('effective_from') or '未知'}~"
            f"{metadata.get('effective_to') or '未知'}"
        )
    bits.append(
        f"权威={metadata.get('authority_label') or '未识别'}"
        f"(L{int(metadata.get('authority_level') or 0)})"
    )
    if metadata.get("source_version"):
        bits.append(f"版本={metadata['source_version']}")
    return "；".join(bits)
