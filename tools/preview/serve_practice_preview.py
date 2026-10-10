"""浏览器验收用临时服务：固定练习样例、无外部模型、无真实资料写入。

uv run --no-sync python -m tools.preview.serve_practice_preview --port 8876
"""
import argparse
import tempfile
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8876)
    args = parser.parse_args()
    from src.config import config
    from src.services import env_store, llm_providers

    with tempfile.TemporaryDirectory(prefix='exam_practice_preview_', ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        config.storage.sqlite_path = str(root / 'meta.db')
        config.storage.chroma_path = str(root / 'chroma')
        config.storage.knowledge_dir = str(root / 'knowledge')
        config.storage.parsed_assets_dir = str(root / 'assets')
        config.storage.log_path = str(root / 'app.log')
        config.llm.api_key = ''
        env_store.PROJECT_ROOT = root
        env_store.ENV_PATH = root / '.env'
        llm_providers.STORE_PATH = root / 'providers.json'

        from src.dependencies import get_doc_store, get_question_bank_store, get_catalog_store
        from src.main import app
        from src.services.evidence_metadata import EvidenceMetadata
        import uvicorn

        ds, store, catalog = get_doc_store(), get_question_bank_store(), get_catalog_store()
        source = root / 'signals.md'
        shutil.copyfile(Path(__file__).resolve().parents[2] / 'tests/fixtures/qa_baseline/signals.md', source)
        doc_id = ds.create('signals.md', str(source))
        ds.update_status(doc_id, 'done', 1)
        ds.update_evidence_metadata(doc_id, {**EvidenceMetadata().to_dict(), 'applicability_scope': '考试', 'metadata_source': 'manual'})
        citation = {'doc_id': str(doc_id), 'source_file': 'signals.md', 'page': 1,
            'snippet': '频谱在 |ω| > ω_m 为零，ω_s > 2ω_m', 'score': .9,
            'applicability_scope': '考试', 'authority_label': '教学材料',
            'selection_reason': '适用场景=考试；版本=固定样例'}
        base = {'course_id': 'course-default', 'chapter': '采样定理', 'difficulty': 'medium',
            'status': 'reviewed', 'citations': [citation], 'analysis': '先确认带限条件，再核对采样角频率。'}
        store.create_question({**base, 'stem': '带限信号的采样角频率应满足什么条件？', 'question_type': 'choice',
            'options': ['严格大于两倍最高角频率', '可以是任意频率', '等于最高角频率'], 'answer': '严格大于两倍最高角频率'})
        store.create_question({**base, 'stem': '为什么讲义提醒采样时留保护带？', 'question_type': 'short_answer',
            'answer': '等于奈奎斯特率属于临界情况，实践中需留保护带。'})
        pdf_source = root / 'tcp-source.pdf'
        shutil.copyfile(Path(__file__).resolve().parents[2] / 'tests/fixtures/qa_pdf/tcp-source.pdf', pdf_source)
        pdf_id = ds.create('tcp-source.pdf', str(pdf_source))
        ds.update_status(pdf_id, 'done', 1)
        store.create_question({**base, 'stem': '【PDF 定位验收样例】TCP 握手第二次发送什么？',
            'chapter': 'TCP 三次握手',
            'question_type': 'choice', 'options': ['SYN+ACK', 'FIN'], 'answer': 'SYN+ACK',
            'analysis': '核对原始 PDF 第 1 页的可读片段。',
            'citations': [{'doc_id': str(pdf_id), 'source_file': 'tcp-source.pdf', 'page': 1,
                'snippet': 'SYN / SYN+ACK / ACK / ESTABLISHED', 'score': .9}]})
        try:
            uvicorn.run(app, host='127.0.0.1', port=args.port, lifespan='off', log_level='warning')
        finally:
            ds.close(); store.close(); catalog.close()


if __name__ == '__main__':
    main()
