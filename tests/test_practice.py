from fastapi.testclient import TestClient
import pytest

from src.dependencies import get_question_bank_store, get_doc_store
from src.main import app
from src.services.storage.question_bank_store import QuestionBankStore


@pytest.fixture
def practice_client(temp_dir, doc_store):
    store = QuestionBankStore(str(temp_dir / 'practice.db'))
    app.dependency_overrides[get_question_bank_store] = lambda: store
    app.dependency_overrides[get_doc_store] = lambda: doc_store
    yield TestClient(app), store, doc_store
    app.dependency_overrides.pop(get_question_bank_store, None)
    app.dependency_overrides.pop(get_doc_store, None)
    store.close()


def question(store, **changes):
    return store.create_question({'course_id': 'course-default', 'stem': '采样条件？',
        'question_type': 'choice', 'options': ['大于两倍', '任意'], 'answer': '大于两倍',
        'analysis': '带限条件', **changes})


def start(client, q, key='start-1', **changes):
    response = client.post('/api/v1/question-bank/practice', json={
        'course_id': 'course-default', 'question_id': q['id'], 'request_id': key, **changes})
    assert response.status_code == 200, response.text
    return response.json()['data']


def submit(client, attempt_id, **changes):
    return client.post(f'/api/v1/question-bank/practice/{attempt_id}/submit', json={
        'course_id': 'course-default', **changes})


def test_choice_snapshot_hidden_and_submission_is_idempotent(practice_client):
    client, store, _ = practice_client
    q = question(store)
    attempt = start(client, q)
    assert 'answer' not in attempt['question']
    assert 'analysis' not in attempt['question']
    assert 'citations' not in attempt['question']
    assert start(client, q)['id'] == attempt['id']
    store.update_question(q['id'], 'course-default', {'answer': '任意', 'stem': '新题干'})
    assert client.get(f"/api/v1/question-bank/practice/{attempt['id']}", params={'course_id': 'other'}).status_code == 404
    assert submit(client, attempt['id'], selected_option=0).json()['data']['correct'] is True
    saved = submit(client, attempt['id'], selected_option=0).json()['data']
    assert saved['question']['stem'] == '采样条件？'
    assert saved['question']['answer'] == '大于两倍'
    assert submit(client, attempt['id'], selected_option=1).status_code == 400
    assert len(store.list_practice('course-default')) == 1


def test_self_assessment_only_after_submission_and_history_survives_deletion(practice_client):
    client, store, _ = practice_client
    q = question(store, question_type='short_answer', options=[], answer='需要带限信号')
    attempt = start(client, q)
    url = f"/api/v1/question-bank/practice/{attempt['id']}/assessment"
    assert client.post(url, json={'course_id': 'course-default', 'correct': False}).status_code == 400
    assert submit(client, attempt['id'], user_answer='我的答案').json()['data']['correct'] is None
    assert client.post(url, json={'course_id': 'course-default', 'correct': False}).json()['data']['grading_method'] == 'self'
    store.delete_question(q['id'], 'course-default')
    retry = start(client, q, key='retry-1', previous_attempt_id=attempt['id'])
    assert retry['question']['stem'] == q['stem']
    assert 'answer' not in retry['question']
    assert len(store.list_practice('course-default')) == 2


def test_wrong_question_retrain_resolves_backlog_without_overwriting_original(practice_client):
    client, store, _ = practice_client
    q = question(store)
    a = start(client, q)
    assert submit(client, a['id'], selected_option=5).status_code == 400
    assert submit(client, a['id'], selected_option=1).json()['data']['correct'] is False
    dashboard = client.get('/api/v1/question-bank/practice-review', params={'course_id': 'course-default'}).json()['data']
    assert len(dashboard['wrong_questions']) == 1
    retry = start(client, q, key='retry', previous_attempt_id=a['id'])
    assert submit(client, retry['id'], selected_option=0).json()['data']['correct'] is True
    dashboard = client.get('/api/v1/question-bank/practice-review', params={'course_id': 'course-default'}).json()['data']
    assert dashboard['wrong_questions'] == []
    assert dashboard['stats']['wrong'] == 1
    assert dashboard['stats']['retrained_correct'] == 1
    assert store.get_practice(a['id'], 'course-default')['correct'] is False


def test_source_replaced_marked_and_cross_course_submission_rejected(practice_client):
    from src.services.evidence_metadata import EvidenceMetadata

    client, store, ds = practice_client
    doc_id = ds.create('讲义.md', '/missing.md')
    ds.update_status(doc_id, 'done', 1)
    q = question(store, citations=[{'doc_id': str(doc_id), 'source_file': '讲义.md', 'snippet': '带限', 'score': 1}])
    a = start(client, q)
    assert submit(client, a['id'], course_id='missing', selected_option=0).status_code == 404
    ds.update_status(doc_id, 'superseded')
    result = submit(client, a['id'], selected_option=0).json()['data']
    assert result['evidence_status'] == 'stale'
    assert result['question']['citations'][0]['source_status'] == 'stale'
    assert client.post(f"/api/v1/question-bank/practice/{a['id']}/assessment", json={'course_id': 'course-default', 'correct': False}).status_code == 400


def test_reopen_store_recovers_started_and_graded_attempts(temp_dir):
    store = QuestionBankStore(str(temp_dir / 'bank.db'))
    q = question(store)
    from src.services.practice import start_practice, submit_practice
    from src.models import PracticeStartRequest, PracticeSubmitRequest

    attempt = start_practice(PracticeStartRequest(course_id='course-default', question_id=q['id'], request_id='persist'), store)
    store.close()
    store = QuestionBankStore(str(temp_dir / 'bank.db'))
    assert store.get_practice(attempt['id'], 'course-default')['status'] == 'started'
    submit_practice(attempt['id'], PracticeSubmitRequest(course_id='course-default', selected_option=0), store)
    store.close()
    store = QuestionBankStore(str(temp_dir / 'bank.db'))
    assert store.get_practice(attempt['id'], 'course-default')['correct'] is True
    store.close()


def test_pending_self_assessment_does_not_resolve_previous_wrong(practice_client):
    client, store, _ = practice_client
    q = question(store, question_type='short_answer', options=[])
    a = start(client, q)
    submit(client, a['id'], user_answer='第一次')
    client.post(f"/api/v1/question-bank/practice/{a['id']}/assessment", json={'course_id': 'course-default', 'correct': False})
    retry = start(client, q, key='self-retry', previous_attempt_id=a['id'])
    submit(client, retry['id'], user_answer='第二次')
    review = client.get('/api/v1/question-bank/practice-review', params={'course_id': 'course-default'}).json()['data']
    assert len(review['wrong_questions']) == 1
    assert review['recommendations'][0]['reason'] == '提交后尚未自评'
    client.post(f"/api/v1/question-bank/practice/{retry['id']}/assessment", json={'course_id': 'course-default', 'correct': True})
    review = client.get('/api/v1/question-bank/practice-review', params={'course_id': 'course-default'}).json()['data']
    assert review['wrong_questions'] == []
