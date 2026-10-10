"""固定课程样例的端到端问答基线；真实模型运行须显式指定 --live。

只创建临时资料库，不读取或改写现有 SQLite/Chroma。默认仅校验原文锚点。
机器检查只发现异常；事实、公式条件和引用支持性需要逐条复核。
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import tempfile
import time
from datetime import datetime, timezone

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'qa_baseline'
logger = logging.getLogger(__name__)


def load_fixture(root: Path = FIXTURE) -> tuple[list[dict], list[dict]]:
    sources = json.loads((root / 'sources.json').read_text(encoding='utf-8'))
    cases = [json.loads(line) for line in (root / 'cases.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    contents = {s['file']: (root / s['file']).read_text(encoding='utf-8') for s in sources}
    for case in cases:
        if not case.get('expect_refusal') and case['anchor'] not in contents[case['source']]:
            raise ValueError(f"原文锚点不存在：{case['id']}")
    return sources, cases


def capture_result(case: dict, data: dict, elapsed: float) -> dict:
    flags = []
    if case.get('expect_refusal'):
        if data.get('grounded') is not False or data.get('answer') != '资料库中未找到相关内容':
            flags.append('refusal_mismatch')
    else:
        if data.get('grounded') is not True:
            flags.append('unexpected_refusal')
        if not any(c.get('source_file') == case['source'] for c in data.get('citations', [])):
            flags.append('expected_source_missing')
    return {**case, 'elapsed_seconds': round(elapsed, 3), 'result': data, 'flags': flags,
            'review': {'status': 'pending', 'fact_correct': None, 'conditions_complete': None,
                       'citations_support_answer': None, 'notes': ''}}


def save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def compare_reports(before: dict, after: dict) -> dict:
    """比较同一批样例的来源覆盖与引用数量；不把来源出现当作事实正确。"""
    def index(report):
        cases = report['cases']
        records = {case['id']: case for case in cases}
        if report.get('status') != 'captured' or len(records) != len(cases):
            raise ValueError('需要完整、无重复编号的报告')
        return records

    old, new = index(before), index(after)
    if old.keys() != new.keys() or any(
        old[key].get(field) != new[key].get(field)
        for key in old for field in ('question', 'source', 'expect_refusal', 'scenario', 'as_of')
    ):
        raise ValueError('只比较相同问题、来源和范围的样例')

    def observation(records):
        positive = [case for case in records.values() if not case.get('expect_refusal')]
        citations = [c for case in records.values() for c in case.get('result', {}).get('citations', [])]
        return {'case_count': len(records), 'answerable_cases': len(positive),
            'expected_source_present': sum(any(c.get('source_file') == case['source']
                for c in case.get('result', {}).get('citations', [])) for case in positive),
            'citation_count': len(citations),
            'model_selected_count': sum(c.get('evidence_role') == 'model_selected' for c in citations),
            'candidate_count': sum(c.get('evidence_role') != 'model_selected' for c in citations),
            'flagged_cases': sum(bool(case.get('flags')) or 'error_type' in case for case in records.values())}

    return {'before': observation(old), 'after': observation(new),
        'limitations': ['引用数量减少不等于引用精准率提高', '预期来源出现不等于全部结论获支持',
            '模型选源仍需对照原文复核', '不同运行的生成具有随机性']}


def run_live(output: Path) -> dict:
    # 进程内使用现有模型配置，但资料存储路径全部替换为临时路径。
    from src.config import config
    from src.services.evidence_metadata import EvidenceMetadata
    from src.services.ingestion import ingest_file
    from src.services.query import ask
    from src.services.llm import OpenAIClient
    from src.services.storage.doc_store import SQLiteDocStore
    from src.services.storage.vector_store import ChromaVectorStore

    sources, cases = load_fixture()
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'corpus': '固定课程样例快照；含一份人为冲突草案',
        'limitations': ['未覆盖真实 PDF 视觉读图', '机器标记不等于事实正确性判分', '结果仅代表本次样例与配置'],
        'settings': {'embedding_provider': config.embedding.provider, 'score_threshold': config.retrieval.score_threshold,
                     'top_k': config.retrieval.top_k, 'rerank_enabled': config.retrieval.rerank_enabled},
        'status': 'running', 'cases': []}
    save_report(output, report)
    try:
        if not config.llm.api_key:
            raise ValueError('缺少 LLM 配置')
        if config.embedding.provider == 'local' and not Path(config.embedding.model).is_dir():
            from huggingface_hub import snapshot_download
            config.embedding.model = snapshot_download(config.embedding.model, local_files_only=True)
        llm = OpenAIClient(api_key=config.llm.api_key, base_url=config.llm.base_url, model=config.llm.model,
            temperature=config.llm.temperature, max_tokens=config.llm.max_tokens, timeout=config.llm.timeout)
        with tempfile.TemporaryDirectory(prefix='exam_qa_baseline_', ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            config.storage.parsed_assets_dir = str(root / 'assets')
            vs = ChromaVectorStore(str(root / 'chroma'), collection_name='qa_baseline')
            ds = SQLiteDocStore(str(root / 'meta.db'))
            try:
                for source in sources:
                    doc_id = ingest_file(str(FIXTURE / source['file']), vs, ds, course_id='course-eval')
                    evidence = {**EvidenceMetadata().to_dict(), **source['evidence'], 'metadata_source': 'manual', 'metadata_confidence': 1.0}
                    ds.update_evidence_metadata(int(doc_id), evidence)
                    vs.set_evidence_metadata_by_doc_id(doc_id, evidence)
                for case in cases:
                    started = time.perf_counter()
                    try:
                        result = ask(question=case['question'], mode='qa', vs=vs, llm=llm,
                            course_id='course-empty' if case.get('empty_course') else 'course-eval',
                            scenario=case.get('scenario'), as_of=case.get('as_of')).model_dump()
                        record = capture_result(case, result, time.perf_counter() - started)
                    except Exception as exc:
                        # 外部错误可能带请求细节；报告只记录类型，不输出凭据/端点。
                        record = {**case, 'error_type': type(exc).__name__, 'flags': ['execution_error'], 'review': {'status': 'pending'}}
                    report['cases'].append(record)
                    save_report(output, report)
                    logger.info('已完成 %s (%d/%d)，异常标记=%s', case['id'], len(report['cases']), len(cases), record['flags'])
            finally:
                ds.close()
                vs.close()
        report['status'] = 'captured'
    except Exception as exc:
        report['status'] = 'setup_failed'
        report['setup_error_type'] = type(exc).__name__
        logger.error('基线无法执行，错误类型=%s', type(exc).__name__)
    save_report(output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='允许当前配置的外部 LLM；只发送固定样例')
    parser.add_argument('--output', type=Path, default=Path('docs/artifacts/quality/qa-baseline-report.json'))
    parser.add_argument('--compare', type=Path, help='与既有完整报告比较；不加 --live 时仅本地读取报告')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    sources, cases = load_fixture()
    logger.info('原文锚点校验通过：%d 份资料，%d 个问题', len(sources), len(cases))
    if args.live:
        report = run_live(args.output)
        if report['status'] != 'captured':
            raise SystemExit(1)
    if args.compare:
        before = json.loads(args.compare.read_text(encoding='utf-8'))
        after = json.loads(args.output.read_text(encoding='utf-8'))
        summary = compare_reports(before, after)
        save_report(args.output.with_suffix('.comparison.json'), summary)
        logger.info('引用数量：%d → %d；预期来源覆盖：%d/%d', summary['before']['citation_count'],
            summary['after']['citation_count'], summary['after']['expected_source_present'], summary['after']['answerable_cases'])


if __name__ == '__main__':
    main()
