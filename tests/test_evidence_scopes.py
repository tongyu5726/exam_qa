from fastapi.testclient import TestClient

from src.dependencies import get_doc_store
from src.main import app
from src.services.evidence_metadata import EvidenceMetadata


def test_fixed_scope_can_clear_inherited_date_and_auto_mode_preserves_inheritance():
    from src.services.intent import resolve_intent
    previous = {'task': 'rule_query', 'mode': 'qa', 'scenario': '考试', 'as_of': '2025-01-01'}
    auto = resolve_intent('那这个呢', previous=previous)
    assert auto.as_of == '2025-01-01'
    fixed = resolve_intent('那这个呢', previous=previous, scenario='all', as_of=None, scope_override=True)
    assert fixed.scenario == 'all'
    assert fixed.as_of is None


def test_scopes_only_use_active_manually_maintained_course_documents(doc_store):
    for course, scope, source, active in [
        ('course-default', '考试', 'manual', True),
        ('course-default', '自动猜测', 'auto', True),
        ('other', '别的课程', 'manual', True),
        ('course-default', '旧场景', 'manual', False),
    ]:
        doc_id = doc_store.create('x.md', '/x.md', course_id=course, is_active=active)
        doc_store.update_status(doc_id, 'done', 1)
        doc_store.update_evidence_metadata(doc_id, {**EvidenceMetadata().to_dict(), 'applicability_scope': scope, 'metadata_source': source})
    app.dependency_overrides[get_doc_store] = lambda: doc_store
    try:
        client = TestClient(app)
        result = client.get('/api/v1/documents/scenarios', params={'course_id': 'course-default'})
        assert result.status_code == 200
        assert result.json()['data']['items'] == ['考试']
        assert client.get('/api/v1/documents/scenarios', params={'course_id': 'missing'}).status_code == 404
    finally:
        app.dependency_overrides.pop(get_doc_store, None)
