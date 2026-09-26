"""LLM 对话客户端（OpenAI 兼容 API）。向量化见 services/embedding.py。"""

import logging
from typing import Protocol, runtime_checkable

from src.exceptions import LLMAPIException, LLMTimeoutException

logger = logging.getLogger(__name__)


@runtime_checkable
class LLMClient(Protocol):
    """对话补全抽象接口。"""

    def chat(self, messages: list[dict], **kwargs) -> str:
        ...

    def chat_stream(self, messages: list[dict], **kwargs):
        ...

    def chat_with_tools(self, messages: list[dict], tools: list[dict], **kwargs) -> dict:
        ...

    def health_check(self) -> bool:
        ...

    @property
    def configured(self) -> bool:
        ...


class OpenAIClient:
    """OpenAI 兼容对话 API。支持 DeepSeek、Qwen 等。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        temperature: float = 0.3,
        max_tokens: int = 2048,
        timeout: int = 60,
    ):
        from openai import OpenAI

        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._client = None
        if api_key:
            from src.services.http_client import create_openai_http_client

            self._client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=1,
                http_client=create_openai_http_client(timeout),
            )

    @property
    def configured(self) -> bool:
        return self._client is not None

    def chat(self, messages: list[dict], **kwargs) -> str:
        if self._client is None:
            raise LLMAPIException("AI 服务未配置：请设置 LLM_API_KEY")
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=kwargs.get("temperature", self._temperature),
                max_tokens=kwargs.get("max_tokens", self._max_tokens),
            )
            message = resp.choices[0].message
            content = message.content or ""
            if not content:
                reasoning_chars = len(getattr(message, "reasoning_content", "") or "")
                logger.warning(
                    "LLM 返回空正文: model=%s finish_reason=%s reasoning_chars=%d",
                    self._model,
                    resp.choices[0].finish_reason,
                    reasoning_chars,
                )
                raise LLMAPIException(
                    "模型完成了推理，但没有返回可显示的答案。"
                    f"当前 LLM_MAX_TOKENS={self._max_tokens}，请提高后重试。"
                )
            return content
        except Exception as e:
            self._raise_mapped(e)

    def chat_stream(self, messages: list[dict], **kwargs):
        """流式对话。

        默认保持兼容，只 yield 正文字符串。传入 ``emit_status=True`` 后，返回
        ``content`` / ``status`` 事件；推理内容本身不向前端暴露，只报告状态。
        """
        if self._client is None:
            raise LLMAPIException("AI 服务未配置：请设置 LLM_API_KEY")
        try:
            emit_status = bool(kwargs.get("emit_status", False))
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=kwargs.get("temperature", self._temperature),
                max_tokens=kwargs.get("max_tokens", self._max_tokens),
                stream=True,
            )
            content_chars = 0
            reasoning_chars = 0
            reasoning_status_sent = False
            finish_reason = None
            for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish_reason = choice.finish_reason or finish_reason
                delta = choice.delta
                reasoning = getattr(delta, "reasoning_content", "") or ""
                if reasoning:
                    reasoning_chars += len(reasoning)
                    if emit_status and not reasoning_status_sent:
                        reasoning_status_sent = True
                        yield {"type": "status", "status": "reasoning"}
                content = delta.content or ""
                if content:
                    content_chars += len(content)
                    if emit_status:
                        yield {"type": "content", "text": content}
                    else:
                        yield content
            if content_chars == 0:
                logger.warning(
                    "LLM 流式返回空正文: model=%s finish_reason=%s reasoning_chars=%d",
                    self._model,
                    finish_reason,
                    reasoning_chars,
                )
                raise LLMAPIException(
                    "模型完成了推理，但没有返回可显示的答案。"
                    f"当前 LLM_MAX_TOKENS={self._max_tokens}，请提高后重试。"
                )
            logger.info(
                "LLM 流式生成完成: model=%s finish_reason=%s content_chars=%d reasoning_chars=%d",
                self._model,
                finish_reason,
                content_chars,
                reasoning_chars,
            )
        except Exception as e:
            self._raise_mapped(e)

    def chat_with_tools(self, messages: list[dict], tools: list[dict], **kwargs) -> dict:
        """调用 OpenAI 兼容 function calling，并归一化为服务层可消费的字典。"""
        if self._client is None:
            raise LLMAPIException("AI 服务未配置：请设置 LLM_API_KEY")
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=tools,
                tool_choice=kwargs.get("tool_choice", "auto"),
                temperature=kwargs.get("temperature", 0),
                max_tokens=kwargs.get("max_tokens", self._max_tokens),
            )
            message = resp.choices[0].message
            calls = []
            for call in message.tool_calls or []:
                function = call.function
                calls.append(
                    {
                        "id": call.id,
                        "name": function.name,
                        "arguments": function.arguments or "{}",
                    }
                )
            return {"content": message.content or "", "tool_calls": calls}
        except Exception as e:
            self._raise_mapped(e)

    def _raise_mapped(self, exc: Exception) -> None:
        name = type(exc).__name__
        if "Timeout" in name:
            raise LLMTimeoutException(detail=str(exc)) from exc
        if name in ("APIConnectionError", "APIStatusError", "AuthenticationError", "RateLimitError"):
            raise LLMAPIException(detail=str(exc)) from exc
        if isinstance(exc, LLMAPIException):
            raise
        logger.error("LLM 调用失败: %s", exc)
        raise LLMAPIException(detail=str(exc)) from exc

    def health_check(self) -> bool:
        if self._client is None:
            logger.warning("LLM API key 未配置，跳过健康检查")
            return False
        try:
            self._client.models.list()
            logger.info("LLM API 连通性检查通过，模型: %s", self._model)
            return True
        except Exception as e:
            logger.error("LLM API 连接失败: %s", e)
            return False
