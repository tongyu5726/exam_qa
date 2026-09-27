from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedPage:
    page: int | None  # PDF/PPT 1-based；纯文本/docx 为 None
    text: str
    # PDF 文档自身定义的页签；和物理页 page、教材中写的 P26 等引用严格分开。
    pdf_page_label: str = ""


@dataclass
class ParsedBlock:
    """MinerU 结构化 block：结构还原的最小单元。"""

    block_type: str  # text|title|table|formula|formula_inline|image|image_caption|table_caption|figure_caption|header|footer|...
    text: str  # 可检索文本（表格/公式/图片摘要已转文本）
    page: int | None  # 1-based
    level: int = 0  # 标题层级 1-6；非标题为 0
    html: str = ""  # 表格原始 HTML
    latex: str = ""  # 公式 LaTeX
    image_path: str = ""  # 图片原始文件路径（多模态摘要用）
    caption: str = ""  # 图片/表格说明
    order: int = 0  # 原始顺序（跨页排序用）
    bbox: tuple | None = None
    pdf_page_label: str = ""
    textbook_references: tuple[str, ...] = ()
    # content | annotation | reference | noise。噪声不进入向量库，批注保留为独立证据。
    content_role: str = "content"


@dataclass
class ParsedDocument:
    pages: list[ParsedPage]
    blocks: list[ParsedBlock] = field(default_factory=list)
    # 解析链路的轻量可追溯信息：不增加模型调用，随切片写入向量 metadata。
    parser_name: str = ""
    parse_quality: float = 0.0

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text.strip())

    @property
    def has_blocks(self) -> bool:
        return bool(self.blocks)


@dataclass(frozen=True)
class PDFParseQuality:
    """用于选择 PDF 解析候选结果的轻量质量信号（不依赖模型调用）。"""

    score: float
    page_coverage: float
    chars_per_page: float
    structure_ratio: float
    expected_pages: int
    parsed_pages: int
