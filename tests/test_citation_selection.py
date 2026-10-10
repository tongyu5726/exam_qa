from unittest.mock import MagicMock

import pytest

from src.services.generation import generate, stream_generate
from src.services.query import _citations

CONTEXT = [
    {'text': '无关的考核比例', 'score': .9, 'metadata': {'doc_id': '9', 'source_file': 'policy.md'}},
    {'text': '带限信号采样条件：ω_s > 2ω_m', 'score': .8, 'metadata': {'doc_id': '2', 'source_file': 'signals.md'}},
]


def test_only_model_selected_evidence_is_shown_and_selection_survives_api():
    llm = MagicMock()
    llm.chat.return_value = '要求带限且 ω_s > 2ω_m。\n<SOURCES>2,2</SOURCES>'
    result = generate(CONTEXT, '采样条件？', llm)
    assert result['answer'] == '要求带限且 ω_s > 2ω_m。'
    assert len(result['citations']) == 1
    citation = _citations(result['citations'])[0]
    assert citation.doc_id == '2'
    assert citation.evidence_role == 'model_selected'
    assert citation.evidence_index == 2
    prompt = llm.chat.call_args.args[0]
    assert '证据编号=1' in prompt[-1]['content']
    assert '证据编号=2' in prompt[-1]['content']


@pytest.mark.parametrize('footer', ['', '<SOURCES>9</SOURCES>', '<SOURCES>0,2</SOURCES>', '<SOURCES>2,other-course</SOURCES>', '<SOURCES>2', '<SOURCES>' + '9' * 5000 + '</SOURCES>'])
def test_unattributed_or_invalid_output_is_only_retrieval_candidates(footer):
    llm = MagicMock()
    llm.chat.return_value = '要求带限。' + footer
    result = generate(CONTEXT, '采样条件？', llm)
    assert result['answer'] == '要求带限。'
    assert {c['evidence_role'] for c in result['citations']} == {'retrieval_candidate'}
    assert {c['doc_id'] for c in result['citations']} == {'9', '2'}


def test_stream_hides_selection_footer_across_every_character_boundary():
    answer = '要求带限且 x < 3。\n<SOURCES>2</SOURCES>'
    llm = MagicMock()
    llm.chat_stream.return_value = iter(answer)
    events = list(stream_generate(CONTEXT, '条件？', llm))
    visible = ''.join(e['text'] for e in events if e['type'] == 'delta')
    assert '<SOURCES>' not in visible
    assert visible.strip() == '要求带限且 x < 3。'
    assert events[-1]['data']['answer'] == visible.strip()
    assert events[-1]['data']['citations'][0]['doc_id'] == '2'


def test_refusal_with_footer_still_has_no_citations():
    llm = MagicMock()
    llm.chat.return_value = '<NO_EVIDENCE>\n<SOURCES>1</SOURCES>'
    assert generate(CONTEXT, '无法支持的问题？', llm)['citations'] == []


def test_agent_citation_preserves_source_governance_and_original_snippet():
    from src.services.agent.tools import search_pdf
    from unittest.mock import patch

    hit = {'text': '第一行\n第二行', 'score': .812345, 'metadata': {
        'doc_id': '12', 'source_file': '课程.pdf', 'page': 3,
        'source_version': 'v2', 'effective_from': '2026-01-01',
        'effective_to': '2026-12-31', 'authority_level': 90,
        'authority_label': '正式规定', 'applicability_scope': 'lab',
        'parser_name': 'mineru', 'parse_quality': .95,
    }}
    with patch('src.services.agent.tools.retrieve_tool', return_value=[hit]):
        citation = search_pdf('要求', MagicMock(), 'course-a', scenario='lab')['citations'][0]
    for field in ('doc_id', 'source_version', 'effective_from', 'effective_to',
                  'authority_level', 'authority_label', 'applicability_scope'):
        assert citation[field] == hit['metadata'][field]
    assert citation['snippet'] == '第一行\n第二行'
    assert citation['score'] == .8123


def test_question_citations_keep_parse_provenance_and_deduplicate():
    from src.services.question_bank import _citations as question_citations

    hit = {'text': '正文\n公式', 'score': .8, 'metadata': {
        'doc_id': '12', 'source_file': '课程.pdf', 'page': 3,
        'parser_name': 'mineru', 'parse_quality': .95, 'bbox': '1,2,3,4',
        'chapter': '第3章', 'block_type': 'formula',
    }}
    citations = question_citations([hit, hit], 'lab', '2026-10-04')
    assert len(citations) == 1
    assert citations[0]['parser_name'] == 'mineru'
    assert citations[0]['bbox'] == '1,2,3,4'
    assert citations[0]['snippet'] == '正文 公式'
    assert '适用场景=' in citations[0]['selection_reason']
