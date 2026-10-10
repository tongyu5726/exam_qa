from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from src.dependencies import get_doc_store
from src.main import app


def test_real_pdf_page_anchor_and_course_scoped_source_round_trip(doc_store):
    source = Path(__file__).parent / 'fixtures/qa_pdf/tcp-source.pdf'
    with fitz.open(source) as pdf:
        assert len(pdf) == 1
        text = pdf[0].get_text()
        assert 'SYN+ACK' in text and 'ESTABLISHED' in text
        assert 'HTTP 80' in text and 'HTTPS 443' in text
    doc_id = doc_store.create('TCP握手.pdf', str(source.resolve()))
    doc_store.update_status(doc_id, 'done', 1)
    previous = app.dependency_overrides.get(get_doc_store)
    app.dependency_overrides[get_doc_store] = lambda: doc_store
    try:
        client = TestClient(app)
        response = client.get(f'/api/v1/documents/{doc_id}/source?course_id=course-default')
        assert response.status_code == 200
        assert response.headers['content-type'] == 'application/pdf'
        assert response.headers['content-disposition'].startswith('inline;')
        assert response.content == source.read_bytes()
        assert client.get(f'/api/v1/documents/{doc_id}/source?course_id=other-course').status_code == 404
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_doc_store, None)
        else:
            app.dependency_overrides[get_doc_store] = previous
