"""Durable, boundary-controlled graph execution and sanitized node observations."""
from __future__ import annotations

import threading
import time
import logging

from langgraph.config import get_stream_writer

from src.services.agent import interaction, nodes, tasks, tools
from src.services.storage.agent_run_store import SQLiteGraphSaver, conflict
from src.exceptions import LLMAPIException, LLMTimeoutException

TOOL_NAMES = tuple(dict.fromkeys(
    schema['function']['name'] for schema in [*tools.TOOL_SCHEMAS,*tasks.TASK_SCHEMAS,*interaction.INTERACTION_SCHEMAS]
))
NODE_NAMES = ('agent','refuse','invalid_tool','fallback',*TOOL_NAMES)
logger = logging.getLogger(__name__)


def validate_breakpoints(values):
    from src.exceptions import BadRequestException
    if any(value not in NODE_NAMES for value in values):
        raise BadRequestException('断点必须是当前图中的节点')
    return list(dict.fromkeys(values))


def public_summary(state):
    """Counts and public task state only; no prompts, quiz answers, raw arguments or documents."""
    task = state.get('task')
    return {'mode':state.get('mode'), 'citation_count':len(state.get('citations') or []),
            'tool_names':[c['name'] if c.get('name') in TOOL_NAMES else 'invalid_tool' for c in state.get('pending_tool_calls') or []],
            'task_status':task.get('status') if task else None,
            'task_kind':task.get('kind') if task else None,
            'evidence_count':len(task.get('evidence') or []) if task else 0,
            'draft_count':len(task.get('drafts') or []) if task else 0,
            'validated':bool(task.get('validated')) if task else False,
            'grounded':bool(state.get('grounded')), 'answer_chars':len(state.get('answer') or '')}


class Execution:
    def __init__(self, store, run_id, course_id, *, resume=False, mode='run'):
        self.store, self.run_id, self.course_id = store,run_id,course_id
        self.resume, self.mode = resume,mode
        self.owner = None
        self.stopped = False
        self.saver = SQLiteGraphSaver(store,owner=lambda:self.owner)
        self.config = {'configurable':{'thread_id':run_id}, 'recursion_limit':100}

    def public(self):
        return self.store.public(self.run_id,self.course_id)

    def observe(self, name, function):
        def observed(state):
            started = time.time()
            clock_start = time.perf_counter()
            event_id = f'{name}:{time.time_ns()}'
            writer = get_stream_writer()
            event = self.store.event(self.run_id,self.owner, {'id':event_id,'node':name,'status':'running','started_at':started,'input':public_summary(state)})
            logger.debug('Agent node start run=%s node=%s',self.run_id,name)
            writer({'type':'node','data':event})
            try:
                result = function(state)
            except Exception:
                event = self.store.event(self.run_id,self.owner, {'id':event_id,'node':name,'status':'failed','started_at':started,
                    'ended_at':time.time(),'duration_ms':round((time.perf_counter()-clock_start)*1000),'error':'节点执行失败，可从检查点重试'})
                writer({'type':'node','data':event})
                raise
            failed = result.get('decision') == 'fallback' or any(not c.get('ok',True) for c in result.get('tool_calls') or [])
            event = self.store.event(self.run_id,self.owner, {'id':event_id,'node':name,'status':'failed' if failed else 'completed',
                'started_at':started,'ended_at':time.time(),'duration_ms':round((time.perf_counter()-clock_start)*1000),
                'output':public_summary({**state,**result})})
            writer({'type':'node','data':event})
            logger.info('Agent node end run=%s node=%s status=%s duration_ms=%s',self.run_id,name,event['status'],event['duration_ms'])
            return result
        return observed

    def stream(self, graph, initial):
        """Static native interrupts before every node make each transition a durable boundary.

        Re-invoking with None resumes the same thread, without replaying completed nodes.
        A DB lease serializes resume calls; a heartbeat survives long model/tool calls.
        """
        self.owner = self.store.claim(self.run_id,self.course_id,self.mode)
        stop = threading.Event()

        def heartbeat():
            while not stop.wait(15):
                if not self.store.renew(self.run_id,self.owner):
                    return

        thread = threading.Thread(target=heartbeat,daemon=True)
        thread.start()
        final = initial
        try:
            if not self.resume:
                # Writes initial state and stops before the first business node.
                for _, _ in graph.stream(initial,self.config,stream_mode=['values','custom']):
                    pass
            snapshot = graph.get_state(self.config)
            final = snapshot.values
            if not final:
                raise conflict('检查点尚未建立，请取消该执行后重新提问')
            yield {'type':'execution','data':self.public()}
            yield {'type':'state','data':final}
            executed = 0
            while snapshot.next:
                record = self.store.read(self.run_id,self.course_id)
                next_nodes = list(snapshot.next)
                self.store.boundary(self.run_id,self.owner,next_nodes)
                if record['control'] == 'cancel':
                    self.store.finish(self.run_id,self.owner,'cancelled',next_nodes)
                    self.stopped = True
                    break
                # A resumed breakpoint is passed once; subsequent visits stop again.
                breakpoint_hit = any(name in record['breakpoints'] for name in next_nodes)
                if record['control'] == 'pause' or (self.mode == 'step' and executed >= 1) or (breakpoint_hit and (not self.resume or executed)):
                    self.store.finish(self.run_id,self.owner,'paused',next_nodes)
                    self.stopped = True
                    break
                for kind, value in graph.stream(None,self.config,stream_mode=['values','custom']):
                    if kind == 'custom':
                        yield value
                    else:
                        final = value
                        yield {'type':'state','data':value}
                executed += 1
                snapshot = graph.get_state(self.config)
                final = snapshot.values
            if not self.stopped:
                # Check a cancellation arriving during the last node as well.
                if self.store.read(self.run_id,self.course_id)['control'] == 'cancel':
                    self.store.finish(self.run_id,self.owner,'cancelled')
                    self.stopped = True
            yield {'type':'execution','data':self.public()}
        except GeneratorExit:
            if not self.stopped:
                self.store.finish(self.run_id,self.owner,'interrupted')
            raise
        except Exception:
            self.store.finish(self.run_id,self.owner,'failed',graph.get_state(self.config).next)
            raise
        finally:
            stop.set()
            thread.join(timeout=1)

    def finish(self, result):
        self.store.finish(self.run_id,self.owner,'ready',result=result)
        return result | {'execution':self.public()}

    def status_result(self, state):
        public = self.public()
        cancelled = public['status'] == 'cancelled'
        task = self.store.cancelled_task(self.run_id,self.course_id) if cancelled else state.get('task')
        return {'answer':'执行已取消，已完成的保存结果保留。' if cancelled else '执行已暂停，可继续或单步执行。',
                'citations':[], 'grounded':False,'response_kind':'status', 'agentic':True,'agent_used':True,
                'tool_calls':state.get('tool_calls') or [],'steps':state.get('steps') or [],
                'task':tasks.public_task(task) if task else None,
                'artifacts':task.get('artifacts') or [] if task else [], 'execution':public}


def build_graph(vs, llm, bank, checkpoint, start_task, execution):
    from langgraph.graph import END, StateGraph
    from src.services.agent.state import AgentState
    graph = StateGraph(AgentState)
    def decide(state):
        try:
            return nodes.agentic_agent_node(state,llm)
        except (LLMAPIException,LLMTimeoutException):
            if state.get('task'):
                raise
            return {'decision':'fallback','steps':['fallback: tool decision unavailable']}

    def fallback(state):
        from src.services.dialogue import classify_dialogue, dialogue_result
        dialogue = classify_dialogue(state['question'])
        if dialogue:
            model = getattr(llm,'model','')
            return dialogue_result(dialogue,model if isinstance(model,str) else '') | {'used_fallback':True}
        result = tools.rag_answer(state['question'],vs=vs,llm=llm,course_id=state['course_id'],mode=state['mode'],
            history=state.get('history'),scenario=state.get('scenario'),as_of=state.get('as_of'),
            top_k=state['top_k'],score_threshold=state['score_threshold'])
        return result | {'used_fallback':True,'tool_calls':[nodes._tool_observation(
            {'id':'fallback','name':'rag_answer'},{'query':state['question']},result)],'steps':['fallback: rag_answer']}

    graph.add_node('agent',execution.observe('agent',decide))
    graph.add_node('refuse',execution.observe('refuse',nodes.refuse_node))
    graph.add_node('fallback',execution.observe('fallback',fallback))

    def one_tool(state):
        pending = state.get('pending_tool_calls') or []
        result = nodes.agentic_tool_node(state,vs,llm,bank,checkpoint,start_task,single=True)
        result['pending_tool_calls'] = pending[1:]
        result['tool_rounds'] = int(state.get('tool_rounds',0)) + (1 if len(pending) == 1 else 0)
        return result

    for name in ('invalid_tool',*TOOL_NAMES):
        graph.add_node(name,execution.observe(name,one_tool))

    def route_tool(state):
        pending = state.get('pending_tool_calls') or []
        name = pending[0].get('name') if pending else None
        return name if name in TOOL_NAMES else 'invalid_tool'

    def route_agent(state):
        if state.get('decision') == 'fallback':
            return 'fallback'
        if state.get('decision') == 'tools':
            return route_tool(state) if int(state.get('tool_rounds',0)) < int(state['max_steps']) else 'refuse'
        return END if state.get('decision') == 'finish' else 'refuse'

    graph.set_entry_point('agent')
    graph.add_conditional_edges('agent',route_agent)
    for name in ('invalid_tool',*TOOL_NAMES):
        graph.add_conditional_edges(name,lambda state:route_tool(state) if state.get('pending_tool_calls') else 'agent')
    graph.add_edge('refuse',END)
    graph.add_edge('fallback',END)
    return graph.compile(checkpointer=execution.saver,interrupt_before=list(NODE_NAMES))
