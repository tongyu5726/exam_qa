"""OpenAI 兼容 LLM 客户端的流式输出归一化。"""

from types import SimpleNamespace

import pytest

from src.exceptions import LLMAPIException
from src.services.llm import OpenAIClient


def _client_with_stream(chunks):
    client = object.__new__(OpenAIClient)
    client._model = "reasoning-test"
    client._temperature = 0.3
    client._max_tokens = 4096
    completions = SimpleNamespace(create=lambda **_kwargs: iter(chunks))
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client


def _chunk(*, content=None, reasoning=None, finish_reason=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def test_stream_reports_reasoning_without_exposing_reasoning_text():
    client = _client_with_stream(
        [
            _chunk(reasoning="内部推理"),
            _chunk(content="最终答案"),
            _chunk(finish_reason="stop"),
        ]
    )

    assert list(client.chat_stream([], emit_status=True)) == [
        {"type": "status", "status": "reasoning"},
        {"type": "content", "text": "最终答案"},
    ]


def test_stream_raises_visible_error_when_only_reasoning_is_returned():
    client = _client_with_stream(
        [_chunk(reasoning="只有推理"), _chunk(finish_reason="length")]
    )

    with pytest.raises(LLMAPIException, match="没有返回可显示"):
        list(client.chat_stream([], emit_status=True))
