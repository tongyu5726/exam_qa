"""OpenAI 兼容 VLM 客户端：供文档入库与独立 API 共用。"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from openai import APITimeoutError, OpenAI

from src.config import config
from src.exceptions import (
    BadRequestException,
    LLMAPIException,
    LLMTimeoutException,
    ServiceUnavailableException,
)
from src.services.http_client import create_openai_http_client

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/bmp",
}

DEFAULT_VISUAL_PROMPT = """请用中文理解这张教学资料图片，并输出可供 RAG 检索的事实摘要。
如果包含多幅图，请逐一按图号描述，不要只分析第一幅。区分图片直接证据和正文解释，不把页眉、水印和装饰当作知识。
如果是图表：必须识别标题、横纵坐标名称与单位、图例、曲线趋势、极值/拐点以及清晰可读的关键数值；对于指定横坐标，给出对应纵坐标的近似值和区间判断。
如果是公式：转写为 LaTeX，并说明变量含义及公式与邻近正文的关系。
如果是普通插图或扫描页：概括其中可确认的文字与结构。
只陈述图片中能确认的内容；无法辨认时明确说明，不要猜测。"""


def image_mime(path: str | Path, content_type: str = "") -> str:
    """从上传类型或扩展名推断 VLM 可接受的图片 MIME。"""
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized in SUPPORTED_IMAGE_MIMES:
        return normalized
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }.get(Path(path).suffix.lower(), "")


def _response_text(response) -> str:
    content = response.choices[0].message.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
            else:
                text = getattr(item, "text", None)
            if text:
                parts.append(str(text).strip())
        return "\n".join(part for part in parts if part).strip()
    return ""


class VisionClient:
    """单一 VLM 出口，避免解析器和 HTTP API 各自拼装请求。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: int,
    ):
        self.api_key = api_key.strip()
        self.base_url = base_url.strip() or "https://api.openai.com/v1"
        self.model = model.strip()
        self.timeout = timeout
        self._client: OpenAI | None = None

    @property
    def configured(self) -> bool:
        return bool(self.model and self.api_key)

    def public_status(self) -> dict:
        return {
            "configured": self.configured,
            "model": self.model,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "protocol": "openai-compatible",
            "supported_mime_types": sorted(SUPPORTED_IMAGE_MIMES),
        }

    def _openai(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=1,
                http_client=create_openai_http_client(self.timeout),
            )
        return self._client

    def analyze_bytes(
        self,
        image: bytes,
        *,
        mime: str,
        prompt: str = "",
        caption: str = "",
        page: int | None = None,
    ) -> str:
        if not self.configured:
            raise ServiceUnavailableException(
                "VLM 未配置，请先设置 VISUAL_MODEL 和 VISUAL_API_KEY"
            )
        if not image:
            raise BadRequestException("图片内容为空")
        if mime not in SUPPORTED_IMAGE_MIMES:
            raise BadRequestException(f"不支持的图片格式: {mime or 'unknown'}")

        instruction = (prompt or DEFAULT_VISUAL_PROMPT).strip()
        context: list[str] = []
        if page:
            context.append(f"PDF 物理页：第 {page} 页")
        if caption:
            context.append(f"图片说明（可能含图号）：{caption.strip()}")
        if context:
            instruction += "\n\n已知上下文：\n" + "\n".join(context)

        data_url = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
        try:
            response = self._openai().chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instruction},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                temperature=0.1,
                max_tokens=config.parsing.visual_max_tokens,
            )
        except APITimeoutError as exc:
            raise LLMTimeoutException("VLM 服务响应超时", detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("VLM 调用失败: %s", exc)
            raise LLMAPIException("VLM 服务暂时不可用", detail=str(exc)) from exc

        if not response.choices:
            raise LLMAPIException("VLM 未返回结果")
        if getattr(response.choices[0], "finish_reason", None) == "length":
            raise LLMAPIException("VLM 摘要被截断，请增大 VISUAL_MAX_TOKENS 或拆分图片")
        text = _response_text(response)
        if not text:
            raise LLMAPIException("VLM 未返回有效内容")
        return text

    def analyze_file(
        self,
        path: str | Path,
        *,
        prompt: str = "",
        caption: str = "",
        page: int | None = None,
    ) -> str:
        source = Path(path)
        mime = image_mime(source)
        if not mime:
            raise BadRequestException(f"不支持的图片格式: {source.suffix or 'unknown'}")
        try:
            payload = source.read_bytes()
        except OSError as exc:
            raise BadRequestException("图片不存在或无法读取", detail=str(exc)) from exc
        return self.analyze_bytes(
            payload,
            mime=mime,
            prompt=prompt,
            caption=caption,
            page=page,
        )


def build_vision_client() -> VisionClient:
    parsing = config.parsing
    return VisionClient(
        api_key=parsing.visual_api_key or config.llm.api_key,
        base_url=parsing.visual_base_url or config.llm.base_url,
        model=parsing.visual_model,
        timeout=parsing.visual_timeout,
    )
