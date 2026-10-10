import pytest
from tests.eval.run_qa_eval import load_fixture, capture_result, compare_reports


def test_baseline_contains_30_traceable_cases_and_refusal_controls():
    sources, cases = load_fixture()
    assert len(cases) == 30
    assert len({c['id'] for c in cases}) == 30
    assert all(c['required_facts'] for c in cases)
    assert sum(bool(c.get('expect_refusal')) for c in cases) == 4
    assert {'公式', '条件', '表格', '范围', '时效', '冲突', '拒答', '隔离'} <= {c['category'] for c in cases}


def test_capture_flags_refusal_and_source_errors_without_claiming_correctness():
    result = capture_result({'expect_refusal': True}, {'grounded': True, 'answer': '编造', 'citations': []}, 1)
    assert result['flags'] == ['refusal_mismatch']
    assert result['review']['fact_correct'] is None
    result = capture_result({'source': '正确.md'}, {'grounded': True, 'answer': '答案', 'citations': [{'source_file': '其他.md'}]}, 1)
    assert result['flags'] == ['expected_source_missing']
    assert result['review']['citations_support_answer'] is None


def test_citation_comparison_separates_source_presence_from_fact_correctness():
    case = {'id': 'one', 'question': '定义', 'source': '正确.md', 'result': {'citations': [
        {'source_file': '正确.md'}, {'source_file': '无关.md'}]}, 'flags': []}
    before = {'status': 'captured', 'cases': [case]}
    after = {'status': 'captured', 'cases': [{**case, 'result': {'citations': [
        {'source_file': '正确.md', 'evidence_role': 'model_selected'}]}}]}
    summary = compare_reports(before, after)
    assert summary['before']['citation_count'] == 2
    assert summary['after']['citation_count'] == 1
    assert summary['after']['expected_source_present'] == 1
    assert 'accuracy' not in summary['after']
    with pytest.raises(ValueError, match='相同问题'):
        compare_reports(before, {**after, 'cases': [{**case, 'question': '不同问题'}]})
    with pytest.raises(ValueError, match='完整'):
        compare_reports(before, {'status': 'running', 'cases': [case]})
