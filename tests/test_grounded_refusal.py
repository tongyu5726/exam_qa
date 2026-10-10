from unittest.mock import MagicMock
import pytest

from src.services.generation import generate, stream_generate

CONTEXT = [{'text': '资料只有卷积定义，不包含考核比例。', 'score': .5, 'metadata': {'source_file': 'signals.md'}}]


@pytest.mark.parametrize('answer', ['<NO_EVIDENCE>', '**资料未包含此内容。**\n这份资料只有卷积。', '资料库中未找到相关内容'])
def test_model_abstention_is_not_claimed_as_grounded(answer):
    llm = MagicMock()
    llm.chat.return_value = answer
    assert generate(CONTEXT, '考核比例？', llm) == {
        'answer': '资料库中未找到相关内容', 'citations': [], 'grounded': False}


def test_streamed_abstention_is_normalized_without_exposing_internal_marker():
    llm = MagicMock()
    llm.chat_stream.return_value = iter(['<NO_', 'EVID', 'ENCE>'])
    events = list(stream_generate(CONTEXT, '考核比例？', llm))
    assert not any(e['type'] == 'delta' and 'NO_' in e['text'] for e in events)
    assert events[-1]['data'] == {'answer': '资料库中未找到相关内容', 'citations': [], 'grounded': False}


def test_partial_supported_answer_does_not_refuse_for_a_missing_example():
    llm = MagicMock()
    llm.chat.return_value = '定义：输入与冲激响应卷积。例题：资料未包含此内容。'
    assert generate(CONTEXT, '卷积定义和例题？', llm)['grounded'] is True
