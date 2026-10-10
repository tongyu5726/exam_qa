"""Durable graph boundaries, recovery, publication, isolation and private state."""
import json
from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import AppException, LLMAPIException, NotFoundException
from src.services.query import ask, ask_stream, resume_execution
from src.services.storage.conversation_store import ConversationStore
from src.services.storage.agent_run_store import AgentRunStore, SQLiteGraphSaver
from src.services.conversation_history import read_conversation


def model(*decisions):
    llm = MagicMock(configured=True,model='test-model')
    llm.chat_with_tools.side_effect = decisions
    llm.chat.return_value = '{"allowed":true}'
    return llm


def reply():
    return {'content':'','tool_calls':[{'id':'reply','name':'respond_to_user',
        'arguments':json.dumps({'kind':'conversation','topic':'identity','answer':'我是溯知。'})}]}


def setup(tmp_path):
    store = ConversationStore(str(tmp_path/'runs.db'))
    conv = store.create_conversation('course')['id']
    return store,conv


@pytest.mark.parametrize('tool_reply', [False, True])
def test_internal_marker_never_published_or_saved_after_durable_execution(tmp_path, tool_reply):
    store,conv = setup(tmp_path)
    bad = {'content':'<NO_EVIDENCE>', 'tool_calls':[]}
    decisions = [bad]
    if tool_reply:
        invalid = reply()
        invalid['tool_calls'][0]['arguments'] = json.dumps({
            'kind':'conversation', 'topic':'capabilities', 'answer':'<NO_EVIDENCE>',
        })
        decisions.insert(0, invalid)
    llm = model(*decisions)
    events = list(ask_stream('你都能干什么','auto',MagicMock(),llm,'course',store,conv))
    out = events[-1]['data']
    assert out['execution']['status'] == 'completed'
    assert out['response_kind'] == 'conversation' and '出题' in out['answer']
    assert '<NO_EVIDENCE>' not in ''.join(e['text'] for e in events if e['type'] == 'delta')
    messages = store.get_messages(conv)
    assert len(messages) == 2 and messages[-1]['content'] == out['answer']
    assert len(out['tool_calls']) == int(tool_reply)
    if tool_reply:
        assert not out['tool_calls'][0]['ok']
    llm.chat.assert_not_called()


def test_breakpoint_survives_reconstruction_step_and_atomic_publish(tmp_path):
    store,conv = setup(tmp_path)
    llm = model(reply())
    result = ask('介绍一下你', 'auto', MagicMock(), llm, 'course', store,conv,breakpoints=['respond_to_user'])
    rid = result.execution['id']
    assert result.execution['status'] == 'paused'
    assert result.execution['next_nodes'] == ['respond_to_user']
    assert store.count_messages(conv) == 0
    llm.chat_with_tools.assert_called_once()
    llm.chat.assert_not_called()
    # A reconstructed store + compiled graph uses native saved messages/pending calls.
    store = ConversationStore(str(tmp_path/'runs.db'))
    llm = model()
    step = resume_execution(rid,'course','step',store,MagicMock(),llm,None)
    assert step['execution']['status'] == 'paused'
    assert step['execution']['next_nodes'] == ['agent']
    llm.chat.assert_called_once()
    llm.chat_with_tools.assert_not_called()
    assert store.count_messages(conv) == 0
    result = resume_execution(rid,'course','run',store,MagicMock(),llm,None)
    assert result['execution']['status'] == 'completed'
    assert result['answer'] == '我是溯知。'
    assert store.count_messages(conv) == 2
    assert resume_execution(rid,'course','run',store,MagicMock(),llm,None) == result
    assert store.count_messages(conv) == 2
    trace = result['execution']['events']
    assert [e['node'] for e in trace if e['status'] == 'completed'] == ['agent','respond_to_user','agent']
    assert all('duration_ms' in e for e in trace if e['status'] == 'completed')
    assert 'messages' not in json.dumps(trace) and '我是溯知' not in json.dumps(trace)


def test_pause_requested_during_node_stops_before_tool(tmp_path):
    store,conv = setup(tmp_path)
    runs = AgentRunStore(store)
    def decide(*args,**kwargs):
        current = runs.list_public(conv,'course')[0]
        runs.control(current['id'],'course','pause')
        return reply()
    llm = model()
    llm.chat_with_tools.side_effect = decide
    events = list(ask_stream('介绍一下你','auto',MagicMock(),llm,'course',store,conv))
    data = events[-1]['data']
    assert data['execution']['status'] == 'paused'
    assert data['execution']['next_nodes'] == ['respond_to_user']
    llm.chat.assert_not_called()
    assert [e['data']['status'] for e in events if e['type'] == 'node'] == ['running','completed']


def test_cancel_never_starts_pending_tool_or_publishes_partial_history(tmp_path):
    store,conv = setup(tmp_path)
    llm = model(reply())
    paused = ask('介绍一下你','auto',MagicMock(),llm,'course',store,conv,breakpoints=['respond_to_user'])
    runs = AgentRunStore(store)
    rid = paused.execution['id']
    assert runs.control(rid,'course','cancel')['status'] == 'cancelled'
    with pytest.raises(AppException,match='执行正在运行'):
        resume_execution(rid,'course','run',store,MagicMock(),llm,None)
    assert store.count_messages(conv) == 0
    llm.chat.assert_not_called()
    # A cancelled execution doesn't prevent a new question.
    result = ask('你好','auto',MagicMock(),model(reply()),'course',store,conv)
    assert result.execution['status'] == 'completed'


def test_failure_retains_native_node_for_retry_not_rag_fallback(tmp_path):
    store,conv = setup(tmp_path)
    llm = model(LLMAPIException('模型离线'))
    with patch('src.services.agent.tools.rag_answer_stream') as rag:
        data = ask('第三章生成1道选择题并保存','auto',MagicMock(),llm,'course',store,conv)
    assert data.execution['status'] == 'failed'
    assert data.execution['next_nodes'] == ['agent']
    assert not data.grounded and store.count_messages(conv) == 0
    rag.assert_not_called()
    result = resume_execution(data.execution['id'],'course','run',store,MagicMock(),model({'content':'','tool_calls':[]}),None)
    assert result['execution']['status'] == 'completed'
    assert store.count_messages(conv) == 2


def test_ordinary_decision_failure_preserves_visible_scoped_fallback(tmp_path):
    store,conv = setup(tmp_path)
    llm = model(LLMAPIException('tools unsupported'))
    with patch('src.services.agent.tools.rag_answer',return_value={'answer':'资料库中未找到相关内容','grounded':False,'citations':[]}) as rag:
        data = ask('课程规定是什么','auto',MagicMock(),llm,'course',store,conv,scenario='课堂',as_of='2026-10-10',scope_override=True)
    assert not data.agentic and data.execution['status'] == 'completed'
    assert rag.call_args.kwargs['course_id'] == 'course'
    assert rag.call_args.kwargs['scenario'] == '课堂' and rag.call_args.kwargs['as_of'] == '2026-10-10'
    assert data.tool_calls[0]['name'] == 'rag_answer'
    assert [e['node'] for e in data.execution['events'] if e['status'] in ('failed','completed')] == ['agent','fallback']


def test_course_isolation_deletion_and_read_only_recovery(tmp_path):
    store,conv = setup(tmp_path)
    data = ask('介绍一下你','auto',MagicMock(),model(reply()),'course',store,conv,breakpoints=['agent'])
    rid = data.execution['id']
    runs = AgentRunStore(store)
    with pytest.raises(NotFoundException):
        runs.public(rid,'other')
    with pytest.raises(NotFoundException):
        runs.control(rid,'other','cancel')
    history = read_conversation(store,conv,'course')
    assert history['messages'] == [] and history['executions'][0]['id'] == rid
    assert 'question' not in json.dumps(history['executions'])
    saver = SQLiteGraphSaver(runs)
    cfg = {'configurable':{'thread_id':rid}}
    assert len(list(saver.list(cfg))) >= 2
    assert saver.get_tuple(cfg).checkpoint['channel_values']['question'] == '介绍一下你'
    assert '介绍一下你' not in json.dumps(runs.checkpoint_history(rid,'course'))
    assert store.delete_conversation(conv)
    assert saver.get_tuple(cfg) is None
    with pytest.raises(NotFoundException):
        runs.public(rid,'course')


def test_lease_rejects_concurrent_resume_and_stale_owner_recovers(tmp_path):
    store,conv = setup(tmp_path)
    data = ask('介绍一下你','auto',MagicMock(),model(reply()),'course',store,conv,breakpoints=['agent'])
    rid = data.execution['id']
    runs = AgentRunStore(store)
    owner = runs.claim(rid,'course','run')
    with pytest.raises(AppException) as err:
        runs.claim(rid,'course','run')
    assert err.value.status_code == 409
    with runs.conn() as db:
        db.execute('UPDATE agent_runs SET lease=0 WHERE id=?',(rid,))
    assert runs.public(rid,'course')['status'] == 'interrupted'
    assert runs.claim(rid,'course','run') != owner
    stale = SQLiteGraphSaver(runs,owner=lambda:owner)
    with pytest.raises(AppException,match='租约已失效'):
        stale.put_writes({'configurable':{'thread_id':rid,'checkpoint_id':'old'}},[('answer','stale')],'task')
    with pytest.raises(AppException):
        runs.finish(rid,owner,'completed')


def test_multi_tool_batch_has_independent_nodes_and_one_round(tmp_path):
    store,conv = setup(tmp_path)
    decision = {'content':'','tool_calls':[{'id':str(i),'name':'search_pdf','arguments':'{"query":"课程"}'} for i in range(2)]}
    llm = model(decision,{'content':'','tool_calls':[]})
    with patch('src.services.agent.tools.execute_tool',return_value={'citations':[],'grounded':False}):
        data = ask('课程内容','auto',MagicMock(),llm,'course',store,conv,breakpoints=['search_pdf'])
        rid = data.execution['id']
        step = resume_execution(rid,'course','step',store,MagicMock(),llm,None)
        assert step['execution']['next_nodes'] == ['search_pdf']
        assert len(step['tool_calls']) == 1
        done = resume_execution(rid,'course','run',store,MagicMock(),llm,None)
    assert len(done['tool_calls']) == 2
    snapshot = SQLiteGraphSaver(AgentRunStore(store)).get_tuple({'configurable':{'thread_id':rid}})
    assert snapshot.checkpoint['channel_values']['tool_rounds'] == 1


def test_active_run_blocks_task_replacement_before_side_effects(tmp_path):
    store,conv = setup(tmp_path)
    ask('介绍一下你','auto',MagicMock(),model(reply()),'course',store,conv,breakpoints=['agent'])
    with pytest.raises(AppException) as err:
        ask('生成5道关于卷积的选择题并保存','auto',MagicMock(),model(),'course',store,conv)
    assert err.value.status_code == 409 and store.get_agent_task(conv,'course') is None


def test_pause_before_save_rechecks_evidence_and_never_repeats_generation(tmp_path):
    from src.services.storage.question_bank_store import QuestionBankStore
    store,conv = setup(tmp_path)
    bank = QuestionBankStore(str(tmp_path/'bank.db'))
    names = ['retrieve_evidence','generate_question_drafts','validate_question_drafts','save_question_drafts']
    llm = model(*[{'content':'','tool_calls':[{'id':n,'name':n,'arguments':'{}'}]} for n in names])
    llm.chat.side_effect = [json.dumps([{'stem':'采样条件？','options':['两倍','一倍','一半','任意'],'answer':'两倍','analysis':'至少两倍','evidence_ids':['ev']}]),
                            '[{"index":1,"supported":true}]']
    hit = {'id':'ev','text':'采样频率至少为最高频率的两倍。','score':.9,'metadata':{'course_id':'course','source_file':'课件.md','page':1}}
    try:
        with patch('src.services.rag._retrieve',return_value=[hit]):
            paused = ask('第三章生成1道选择题并保存','auto',MagicMock(),llm,'course',store,conv,question_bank_store=bank,breakpoints=['save_question_drafts'])
        assert paused.execution['next_nodes'] == ['save_question_drafts']
        assert bank.list_questions('course') == []
        assert llm.chat.call_count == 2
        # Resume invokes the already-decided save, with current evidence revalidation.
        with patch('src.services.rag._retrieve',return_value=[hit]):
            done = resume_execution(paused.execution['id'],'course','run',store,MagicMock(),llm,bank)
        assert len(bank.list_questions('course')) == 1
        assert len(done['artifacts']) == 1 and llm.chat.call_count == 2
        resume_execution(paused.execution['id'],'course','run',store,MagicMock(),llm,bank)
        assert len(bank.list_questions('course')) == 1
    finally:
        bank.close()


def test_resume_does_not_grant_save_or_accept_changed_evidence(tmp_path):
    from src.services.storage.question_bank_store import QuestionBankStore
    store,conv = setup(tmp_path)
    bank = QuestionBankStore(str(tmp_path/'bank.db'))
    names = ['retrieve_evidence','generate_question_drafts','validate_question_drafts','save_question_drafts']
    llm = model(*[{'content':'','tool_calls':[{'id':n,'name':n,'arguments':'{}'}]} for n in names],{'content':'','tool_calls':[]})
    llm.chat.side_effect = [json.dumps([{'stem':'采样条件？','options':['两倍','一倍','一半','任意'],'answer':'两倍','evidence_ids':['ev']}]),'[{"index":1,"supported":true}]']
    hit = {'id':'ev','text':'至少两倍','score':.9,'metadata':{'course_id':'course','source_file':'课件.md','page':1}}
    try:
        with patch('src.services.rag._retrieve',return_value=[hit]):
            paused = ask('第三章生成1道选择题并保存','auto',MagicMock(),llm,'course',store,conv,question_bank_store=bank,breakpoints=['save_question_drafts'])
        with patch('src.services.rag._retrieve',return_value=[]):
            done = resume_execution(paused.execution['id'],'course','run',store,MagicMock(),llm,bank)
        assert not done['artifacts'] and bank.list_questions('course') == []
        assert not store.get_agent_task(conv,'course')['validated']
    finally:
        bank.close()


def test_run_without_conversation_id_is_still_durable_and_model_change_is_explicit(tmp_path):
    store = ConversationStore(str(tmp_path/'runs.db'))
    out = ask('介绍一下你','auto',MagicMock(),model(reply()),'course',store,breakpoints=['agent'])
    assert out.execution['conversation_id']
    other = model(reply())
    other.model = 'different-model'
    with pytest.raises(AppException,match='模型已变更'):
        resume_execution(out.execution['id'],'course','run',store,MagicMock(),other,None)
    other.chat_with_tools.assert_not_called()


def test_control_api_checks_course_and_persists_original_graph(tmp_path):
    from fastapi.testclient import TestClient
    from src.main import app
    from src.dependencies import get_conversation_store,get_llm_client,get_vector_store,get_catalog_store,get_question_bank_store
    store,conv = setup(tmp_path)
    llm = model(reply())
    overrides = app.dependency_overrides.copy()
    app.dependency_overrides.update({get_conversation_store:lambda:store,get_llm_client:lambda:llm,
        get_vector_store:lambda:MagicMock(),get_catalog_store:lambda:MagicMock(),get_question_bank_store:lambda:None})
    try:
        client = TestClient(app)
        out = client.post('/api/v1/ask',json={'question':'介绍一下你','course_id':'course','conversation_id':conv,'breakpoints':['respond_to_user']})
        assert out.status_code == 200
        rid = out.json()['data']['execution']['id']
        base = '/api/v1/agent/runs/'+rid
        assert client.get(base,params={'course_id':'foreign'}).status_code == 404
        assert client.post(base+'/control',json={'course_id':'foreign','action':'resume'}).status_code == 404
        assert client.post(base+'/control',json={'course_id':'course','action':'breakpoints','breakpoints':['untrusted_node']}).status_code == 400
        points = client.get(base+'/checkpoints',params={'course_id':'course'}).json()['data']
        assert points and all('channel_values' not in p for p in points)
        step = client.post(base+'/control',json={'course_id':'course','action':'step'}).json()['data']
        assert step['execution']['next_nodes'] == ['agent']
        done = client.post(base+'/control',json={'course_id':'course','action':'resume'}).json()['data']
        assert done['execution']['status'] == 'completed'
        assert llm.chat_with_tools.call_count == 1
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_cancellation_survives_worker_loss_and_never_restores_pending_save(tmp_path):
    store,conv = setup(tmp_path)
    data = ask('第三章生成1道选择题并保存','auto',MagicMock(),model(),'course',store,conv,breakpoints=['agent'])
    runs = AgentRunStore(store)
    rid = data.execution['id']
    runs.claim(rid,'course','run')
    assert runs.control(rid,'course','cancel')['status'] == 'running'
    with runs.conn() as db:
        db.execute('UPDATE agent_runs SET lease=0 WHERE id=?',(rid,))
    assert runs.public(rid,'course')['status'] == 'cancelled'
    with pytest.raises(AppException):
        runs.claim(rid,'course','run')
    # A new request settles the cancelled checkpoint before preparing a new task.
    ask('介绍一下你','auto',MagicMock(),model(reply()),'course',store,conv)
    assert store.get_agent_task(conv,'course')['status'] == 'cancelled'
