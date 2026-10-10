"""课程内练习：快照、确定性选择题判分、自评和可解释的复习队列。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

from src.config import config
from src.exceptions import BadRequestException
from src.models import PracticeStartRequest, PracticeSubmitRequest
from src.services.storage.question_bank_store import QuestionBankStore
from src.services.storage.doc_store import SQLiteDocStore


def _choice_answer(question: dict) -> int:
    options = question.get('options') or []
    matches = [i for i, text in enumerate(options) if text.strip() == question['answer'].strip()]
    if len(options) < 2 or len(set(text.strip() for text in options)) != len(options) or len(matches) != 1:
        raise BadRequestException('选择题答案必须唯一对应一个选项，请先校正题目')
    return matches[0]


def start_practice(request: PracticeStartRequest, store: QuestionBankStore) -> dict:
    existing = store.find_practice_request(request.course_id, request.request_id)
    if existing:
        if existing['question_id'] != request.question_id or existing['previous_attempt_id'] != request.previous_attempt_id:
            raise BadRequestException('请求标识已用于另一场练习')
        return existing
    if request.previous_attempt_id:
        previous = store.get_practice(request.previous_attempt_id, request.course_id)
        if previous['question_id'] != request.question_id or previous['status'] != 'graded':
            raise BadRequestException('只能重练已完成的同一道题目')
        question = previous['question']
    else:
        question = store.require_question(request.question_id, request.course_id)
    method = 'objective' if question['question_type'] == 'choice' else 'self'
    if method == 'objective':
        _choice_answer(question)
    result = store.create_practice(request.course_id, question, request.request_id, request.previous_attempt_id, method)
    if result['question_id'] != request.question_id or result['previous_attempt_id'] != request.previous_attempt_id:
        raise BadRequestException('请求标识已用于另一场练习')
    return result


def submit_practice(attempt_id: str, request: PracticeSubmitRequest, store: QuestionBankStore) -> dict:
    attempt = store.get_practice(attempt_id, request.course_id)
    question = attempt['question']
    if attempt['grading_method'] == 'objective':
        index = request.selected_option
        if index is None or index >= len(question['options']) or request.user_answer:
            raise BadRequestException('请选择有效选项，不要提交文本答案')
        answer = question['options'][index]
        correct = index == _choice_answer(question)
    else:
        if request.selected_option is not None or not request.user_answer.strip():
            raise BadRequestException('请填写答案后提交')
        answer, index, correct = request.user_answer.strip(), None, None
    return store.submit_practice(attempt_id, request.course_id, answer, index, correct)


def assess_practice(attempt_id: str, course_id: str, correct: bool, store: QuestionBankStore) -> dict:
    attempt = store.get_practice(attempt_id, course_id)
    if attempt['grading_method'] != 'self' or attempt['status'] == 'started':
        raise BadRequestException('仅已提交的主观题可以自评')
    return store.assess_practice(attempt_id, course_id, correct)


def _evidence(question: dict, course_id: str, ds: SQLiteDocStore) -> tuple[str, list[dict]]:
    citations = deepcopy(question.get('citations') or [])
    today = date.today().isoformat()
    for citation in citations:
        raw_id = str(citation.get('doc_id') or '')
        doc = ds.get(int(raw_id)) if raw_id.isdecimal() else None
        valid = bool(doc and doc['course_id'] == course_id and doc['is_active']
                     and doc['status'] == 'done' and Path(doc['file_path']).is_file())
        if valid:
            for field in ('source_version', 'effective_from', 'effective_to', 'applicability_scope', 'authority_level'):
                if field in citation and citation[field] != doc.get(field):
                    valid = False
            valid = valid and doc['effective_from'] <= today <= doc['effective_to']
            # 相同 doc_id 的强制重建也可能替换正文；旧题不能据此宣称仍有有效证据。
            if question.get('created_at') and doc.get('updated_at', '') > question['created_at']:
                valid = False
        citation['source_status'] = 'current' if valid else 'stale'
        if doc and doc['course_id'] != course_id:
            citation['doc_id'] = ''
    status = 'unverified' if not citations else ('current' if all(c['source_status'] == 'current' for c in citations) else 'stale')
    return status, citations


def present_practice(attempt: dict, ds: SQLiteDocStore) -> dict:
    result = deepcopy(attempt)
    result.pop('request_id', None)
    status, citations = _evidence(result['question'], result['course_id'], ds)
    result['evidence_status'] = status
    if result['status'] == 'started':
        # 摘要也可能泄露答案，提交前只公开题干和选项。
        result['question'] = {key: result['question'].get(key) for key in ('id', 'stem', 'question_type', 'options', 'chapter', 'difficulty')}
    else:
        result['question']['citations'] = citations
    return result


def practice_review(course_id: str, store: QuestionBankStore, ds: SQLiteDocStore) -> dict:
    attempts = store.list_practice(course_id)
    completed = [a for a in attempts if a['status'] != 'started']
    latest = {}
    for attempt in completed:
        if attempt['status'] == 'graded':
            latest.setdefault(attempt['question_id'], attempt)
    wrong = [present_practice(a, ds) for a in latest.values() if a['correct'] is False]
    pending = [present_practice(a, ds) for a in attempts if a['status'] in ('started', 'submitted')]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.practice.review_days)).isoformat()
    recent = [a for a in completed if a['submitted_at'] >= cutoff]
    recommendations = []
    seen = set()
    for attempt in [*pending, *wrong]:
        if attempt['question_id'] in seen:
            continue
        seen.add(attempt['question_id'])
        recommendations.append({'question_id': attempt['question_id'], 'attempt_id': attempt['id'],
            'stem': attempt['question']['stem'], 'action': 'resume' if attempt['status'] != 'graded' else 'retry',
            'reason': '继续未完成练习' if attempt['status'] == 'started' else '提交后尚未自评' if attempt['status'] == 'submitted' else '最近一次作答错误，建议回看资料后重练'})
    attempted_ids = {a['question_id'] for a in attempts}
    for question in store.list_questions(course_id):
        if question['id'] not in attempted_ids:
            recommendations.append({'question_id': question['id'], 'attempt_id': None,
                'stem': question['stem'], 'action': 'start', 'reason': '尚未练习，先做一次自测'})
    return {'wrong_questions': wrong, 'pending': pending,
        'history': [present_practice(a, ds) for a in completed[:config.practice.history_limit]],
        'history_limit': config.practice.history_limit,
        'recommendations': recommendations[:config.practice.recommendation_limit],
        'stats': {'period_days': config.practice.review_days, 'submitted': len(recent),
            'wrong': sum(a['correct'] is False for a in recent),
            'self_assessed': sum(a['grading_method'] == 'self' and a['correct'] is not None for a in recent),
            'awaiting_assessment': sum(a['correct'] is None for a in recent),
            'retrained_correct': sum(bool(a['previous_attempt_id']) and a['correct'] is True for a in recent)}}
