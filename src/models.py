"""Pydantic 请求/响应模型。"""

from typing import Optional, Literal
from pydantic import BaseModel, Field


class Citation(BaseModel):
    doc_id: str = ""
    source_file: str
    page: Optional[int] = None
    snippet: str
    score: float
    source_version: str = ""
    effective_from: str = "0001-01-01"
    effective_to: str = "9999-12-31"
    authority_level: int = 30
    authority_label: str = "教学材料"
    applicability_scope: str = "all"
    selection_reason: str = ""
    chapter: str = ""
    section_path: str = ""
    block_type: str = ""
    bbox: str = ""
    pdf_page_label: str = ""
    textbook_references: str = ""
    content_role: str = "content"
    parser_name: str = ""
    parse_quality: float = 0.0


class IntentData(BaseModel):
    task: str = "qa"
    mode: Literal["qa", "concept", "chapter"] = "qa"
    scenario: str | None = None
    as_of: str | None = None
    confidence: float = 0.0
    layer: Literal["rule", "context", "llm_fallback", "default"] = "default"
    rationale: str = ""


class AnswerData(BaseModel):
    answer: str
    citations: list[Citation] = []
    grounded: bool = True
    intent: IntentData | None = None


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, description="问题内容不能为空")
    course_id: str = Field(..., min_length=1, description="课程 ID，检索隔离必填")
    mode: Literal["auto", "qa", "concept", "chapter"] = "auto"
    conversation_id: str | None = Field(default=None, description="conversation ID for multi-turn context")
    scenario: str | None = Field(default=None, max_length=80, description="固定适用场景键；未传则不过滤")
    as_of: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="证据生效日 YYYY-MM-DD")
    stream: bool = False


class AgentRunRequest(BaseModel):
    question: str = Field(..., min_length=1, description="问题内容不能为空")
    course_id: str = Field(..., min_length=1, description="课程 ID，检索隔离必填")
    mode: Literal["qa", "concept"] = "qa"
    max_steps: int = Field(default=3, ge=1, le=10, description="最大检索/改写轮数")
    top_k: int | None = Field(default=None, ge=1, description="单轮检索候选数，缺省用全局配置")
    score_threshold: float | None = Field(default=None, ge=0, le=1, description="判定检索足够的阈值，缺省用全局配置")
    scenario: str | None = Field(default=None, max_length=80, description="固定适用场景键")
    as_of: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="证据生效日 YYYY-MM-DD")
    agentic: bool = Field(default=False, description="启用 P2-C 受控工具调用循环；失败自动降级 P2-B")


class AgentRunData(BaseModel):
    answer: str
    citations: list[Citation] = []
    grounded: bool = True
    steps: list[str] = []
    tool_calls: list[dict] = []
    agentic: bool = False
    agent_used: bool = True


QuestionType = Literal["choice", "fill_blank", "short_answer"]
QuestionDifficulty = Literal["easy", "medium", "hard"]
QuestionStatus = Literal["draft", "reviewed"]


class QuestionCreateRequest(BaseModel):
    course_id: str = Field(..., min_length=1, description="课程 ID，题库隔离必填")
    stem: str = Field(..., min_length=1, max_length=4000)
    question_type: QuestionType = "short_answer"
    options: list[str] = Field(default_factory=list, max_length=8)
    answer: str = Field(..., min_length=1, max_length=4000)
    analysis: str = Field(default="", max_length=6000)
    difficulty: QuestionDifficulty = "medium"
    chapter: str = Field(default="", max_length=160)
    status: QuestionStatus = "draft"


class QuestionPatch(BaseModel):
    stem: str | None = Field(default=None, min_length=1, max_length=4000)
    question_type: QuestionType | None = None
    options: list[str] | None = Field(default=None, max_length=8)
    answer: str | None = Field(default=None, min_length=1, max_length=4000)
    analysis: str | None = Field(default=None, max_length=6000)
    difficulty: QuestionDifficulty | None = None
    chapter: str | None = Field(default=None, max_length=160)
    status: QuestionStatus | None = None


class QuestionGenerateRequest(BaseModel):
    course_id: str = Field(..., min_length=1, description="课程 ID，题库隔离必填")
    topic: str = Field(..., min_length=1, max_length=300, description="出题主题或知识点")
    question_type: QuestionType = "short_answer"
    difficulty: QuestionDifficulty = "medium"
    count: int = Field(default=3, ge=1, le=10)
    chapter: str = Field(default="", max_length=160)
    scenario: str | None = Field(default=None, max_length=80)
    as_of: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class QuestionData(BaseModel):
    id: str
    course_id: str
    stem: str
    question_type: QuestionType
    options: list[str] = []
    answer: str
    analysis: str = ""
    difficulty: QuestionDifficulty
    chapter: str = ""
    citations: list[Citation] = []
    scenario: str = ""
    as_of: str = ""
    status: QuestionStatus = "draft"
    origin: Literal["manual", "agent"] = "manual"
    created_at: str
    updated_at: str


class QuestionGenerateData(BaseModel):
    questions: list[QuestionData] = []
    citations: list[Citation] = []
    grounded: bool = True


class PaperItemRequest(BaseModel):
    question_id: str = Field(..., min_length=1)
    score: float = Field(default=1, gt=0, le=100)


class PaperCreateRequest(BaseModel):
    course_id: str = Field(..., min_length=1, description="课程 ID，题库隔离必填")
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    items: list[PaperItemRequest] = Field(..., min_length=1, max_length=100)


class PaperBlueprintRule(BaseModel):
    question_type: QuestionType
    difficulty: QuestionDifficulty = "medium"
    count: int = Field(..., ge=1, le=50)
    score: float = Field(default=1, gt=0, le=100)
    chapter: str = Field(default="", max_length=160)


class PaperAssembleRequest(BaseModel):
    course_id: str = Field(..., min_length=1, description="课程 ID，题库隔离必填")
    title: str = Field(..., min_length=1, max_length=200)
    topic: str = Field(..., min_length=1, max_length=300, description="缺题时基于此主题受控补题")
    description: str = Field(default="", max_length=1000)
    rules: list[PaperBlueprintRule] = Field(..., min_length=1, max_length=20)
    scenario: str | None = Field(default=None, max_length=80)
    as_of: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    allow_generate: bool = Field(default=True, description="题库缺题时允许基于资料补生成草稿")


class PaperData(BaseModel):
    id: str
    course_id: str
    title: str
    description: str = ""
    question_count: int = 0
    total_score: float = 0
    created_at: str
    updated_at: str
    items: list[dict] = []


class PaperAssembleData(BaseModel):
    paper: PaperData
    reused_count: int = 0
    generated_count: int = 0
    total_score: float = 0


class LLMPatch(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    max_tokens: int | None = Field(default=None, ge=256, le=131072)
    timeout: int | None = Field(default=None, ge=1)


class EmbeddingPatch(BaseModel):
    provider: Literal["local", "openai"] | None = None
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    timeout: int | None = Field(default=None, ge=1)


class RetrievalPatch(BaseModel):
    top_k: int | None = Field(default=None, ge=1)
    score_threshold: float | None = Field(default=None, ge=0, le=1)
    rerank_enabled: bool | None = None
    rerank_model: str | None = None
    rerank_candidates: int | None = Field(default=None, ge=1)
    rerank_top_n: int | None = Field(default=None, ge=0)


class ChunkPatch(BaseModel):
    chunk_size: int | None = Field(default=None, ge=1)
    chunk_overlap: int | None = Field(default=None, ge=0)


class ParsingPatch(BaseModel):
    pdf_use_ocr: bool | None = None
    pdf_force_ocr: bool | None = None
    pdf_ocr_language: str | None = None
    pdf_parser: Literal["auto", "pymupdf", "mineru", "markpdfdown"] | None = None
    mineru_cmd: str | None = None
    mineru_timeout: int | None = Field(default=None, ge=0)
    mineru_backend: str | None = None
    mineru_effort: Literal["medium", "high"] | None = None
    mineru_lang: str | None = None
    mineru_formula: bool | None = None
    mineru_table: bool | None = None
    mineru_image_analysis: bool | None = None
    mineru_retry_high: bool | None = None
    pdf_quality_threshold: float | None = Field(default=None, ge=0, le=1)
    formula_recognition_enabled: bool | None = None
    formula_recognition_device: str | None = None
    formula_recognition_model: str | None = None
    markpdfdown_enabled: bool | None = None
    markpdfdown_cmd: str | None = None
    markpdfdown_args: str | None = None
    markpdfdown_timeout: int | None = Field(default=None, ge=0)
    visual_model: str | None = None
    visual_base_url: str | None = None
    visual_api_key: str | None = None
    visual_timeout: int | None = Field(default=None, ge=0)


class EvidenceMetadataPatch(BaseModel):
    source_version: str | None = Field(default=None, max_length=80)
    effective_from: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    effective_to: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    authority_level: int | None = Field(default=None, ge=0, le=100)
    authority_label: str | None = Field(default=None, max_length=80)
    applicability_scope: str | None = Field(default=None, max_length=80)


class AppPatch(BaseModel):
    max_upload_mb: int | None = Field(default=None, ge=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] | None = None


class ServerPatch(BaseModel):
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)


class ProxyPatch(BaseModel):
    url: str | None = None
    no_proxy: str | None = None
    enabled: bool | None = None


class ConfigUpdateRequest(BaseModel):
    llm: LLMPatch | None = None
    embedding: EmbeddingPatch | None = None
    retrieval: RetrievalPatch | None = None
    chunk: ChunkPatch | None = None
    parsing: ParsingPatch | None = None
    app: AppPatch | None = None
    server: ServerPatch | None = None
    proxy: ProxyPatch | None = None
