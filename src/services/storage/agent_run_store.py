"""Local execution journal and native LangGraph checkpoints; private state never leaves storage."""
from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP

from src.exceptions import AppException, NotFoundException


def conflict(message):
    return AppException(message, status_code=409)


class AgentRunStore:
    def __init__(self, conversations):
        self.conversations = conversations
        with self.conn() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, course_id TEXT NOT NULL,
                    request TEXT NOT NULL, status TEXT NOT NULL, control TEXT NOT NULL DEFAULT 'run',
                    breakpoints TEXT NOT NULL DEFAULT '[]', next_nodes TEXT NOT NULL DEFAULT '[]',
                    owner TEXT, lease REAL NOT NULL DEFAULT 0, result TEXT, published INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_agent_run ON agent_runs(conversation_id)
                    WHERE status IN ('running','paused','interrupted','failed','ready');
                CREATE TABLE IF NOT EXISTS agent_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, event TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS graph_checkpoints (
                    run_id TEXT NOT NULL, ns TEXT NOT NULL, id TEXT NOT NULL, parent TEXT,
                    type TEXT NOT NULL, data BLOB NOT NULL, metadata TEXT NOT NULL,
                    PRIMARY KEY(run_id,ns,id), FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS graph_writes (
                    run_id TEXT NOT NULL, ns TEXT NOT NULL, checkpoint TEXT NOT NULL,
                    task TEXT NOT NULL, idx INTEGER NOT NULL, channel TEXT NOT NULL, type TEXT NOT NULL, data BLOB NOT NULL,
                    PRIMARY KEY(run_id,ns,checkpoint,task,idx),
                    FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE
                );
            """)

    @contextmanager
    def conn(self):
        db = self.conversations._conn()
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, conversation_id, course_id, request, breakpoints):
        self.conversations.ensure_conversation(conversation_id, course_id, request['question'][:40])
        if self.conversations.get_conversation(conversation_id)['course_id'] != course_id:
            raise NotFoundException('对话不属于当前课程')
        run_id = 'run_' + uuid.uuid4().hex
        with self.conn() as db:
            db.execute('BEGIN IMMEDIATE')
            # A cancelled worker that died cannot keep the conversation reserved forever.
            db.execute("UPDATE agent_runs SET status='cancelled',owner=NULL,lease=0 WHERE conversation_id=? AND status='running' AND control='cancel' AND lease<?",(conversation_id,time.time()))
            if db.execute("SELECT 1 FROM agent_runs WHERE conversation_id=? AND status IN ('running','paused','interrupted','failed','ready')", (conversation_id,)).fetchone():
                raise conflict('当前对话仍有未结束的执行，请继续或取消后再提问')
            db.execute('INSERT INTO agent_runs(id,conversation_id,course_id,request,status,breakpoints,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                       (run_id, conversation_id, course_id, json.dumps(request, ensure_ascii=False), 'paused', json.dumps(breakpoints), time.time(), time.time()))
        return run_id

    def read(self, run_id, course_id):
        with self.conn() as db:
            row = db.execute('SELECT * FROM agent_runs WHERE id=? AND course_id=?', (run_id, course_id)).fetchone()
        if not row:
            raise NotFoundException('执行不存在或不属于当前课程')
        value = dict(row)
        for key in ('request', 'breakpoints', 'next_nodes', 'result'):
            value[key] = json.loads(value[key]) if value[key] else None
        if value['status'] == 'running' and value['lease'] < time.time():
            value['status'] = 'cancelled' if value['control'] == 'cancel' else 'interrupted'
        return value

    def public(self, run_id, course_id):
        value = self.read(run_id, course_id)
        with self.conn() as db:
            events = [json.loads(row[0]) | {'seq': row[1]} for row in db.execute('SELECT event,seq FROM agent_events WHERE run_id=? ORDER BY seq', (run_id,))]
            checkpoint = db.execute("SELECT id FROM graph_checkpoints WHERE run_id=? AND ns='' ORDER BY id DESC LIMIT 1",(run_id,)).fetchone()
        return {key: value[key] for key in ('id','conversation_id','course_id','status','control','breakpoints','next_nodes','created','updated')} | {'events':events,'checkpoint_id':checkpoint[0] if checkpoint else None}

    def checkpoint_history(self, run_id, course_id):
        from src.services.agent.execution import public_summary
        self.read(run_id,course_id)
        saver = SQLiteGraphSaver(self)
        return [{'id':item.config['configurable']['checkpoint_id'], 'parent_id':item.parent_config['configurable']['checkpoint_id'] if item.parent_config else None,
                 'created_at':item.checkpoint['ts'], 'step':item.metadata.get('step'),
                 'summary':public_summary(item.checkpoint.get('channel_values') or {})}
                for item in saver.list({'configurable':{'thread_id':run_id}},limit=200)]

    def list_public(self, conversation_id, course_id):
        with self.conn() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM agent_runs WHERE conversation_id=? AND course_id=? ORDER BY created DESC LIMIT 30', (conversation_id, course_id))]
        return [self.public(rid, course_id) for rid in ids]

    def claim(self, run_id, course_id, mode):
        owner = uuid.uuid4().hex
        with self.conn() as db:
            changed = db.execute("UPDATE agent_runs SET owner=?,lease=?,status='running',control=?,updated=? WHERE id=? AND course_id=? AND control!='cancel' AND status IN ('paused','interrupted','failed','running') AND (owner IS NULL OR lease<?)",
                                 (owner, time.time()+60, mode, time.time(), run_id, course_id, time.time())).rowcount
        if not changed:
            raise conflict('执行正在运行、已结束或已被其他请求接管')
        return owner

    def renew(self, run_id, owner):
        with self.conn() as db:
            return bool(db.execute('UPDATE agent_runs SET lease=? WHERE id=? AND owner=?', (time.time()+60, run_id, owner)).rowcount)

    def boundary(self, run_id, owner, next_nodes):
        with self.conn() as db:
            if not db.execute('UPDATE agent_runs SET next_nodes=?,updated=? WHERE id=? AND owner=?', (json.dumps(list(next_nodes)),time.time(),run_id,owner)).rowcount:
                raise conflict('执行已删除或所有权已失效')

    def control(self, run_id, course_id, action, breakpoints=None):
        self.read(run_id, course_id)
        with self.conn() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status,owner,lease FROM agent_runs WHERE id=? AND course_id=?', (run_id,course_id)).fetchone()
            if row['status'] not in ('running','paused','interrupted','failed'):
                raise conflict('当前执行已结束')
            if breakpoints is not None:
                db.execute('UPDATE agent_runs SET breakpoints=? WHERE id=?', (json.dumps(breakpoints),run_id))
            if action in ('pause','cancel'):
                idle = not row['owner'] or row['lease'] < time.time()
                db.execute('UPDATE agent_runs SET control=?,status=?,updated=? WHERE id=?',
                           (action, ('cancelled' if action == 'cancel' else 'paused') if idle else 'running', time.time(),run_id))
        result = self.public(run_id, course_id)
        if result['status'] == 'cancelled':
            self.cancelled_task(run_id,course_id)
        return result

    def cancelled_task(self, run_id, course_id):
        """Cancel the same unfinished task only after the current node settles; preserve committed artifacts."""
        record = self.read(run_id,course_id)
        snapshot = SQLiteGraphSaver(self).get_tuple({'configurable':{'thread_id':run_id}})
        task = snapshot.checkpoint.get('channel_values',{}).get('task') if snapshot else None
        current = self.conversations.get_agent_task(record['conversation_id'],course_id)
        if task and current and task['id'] == current['id'] and current['status'] == 'running':
            return self.conversations.checkpoint_agent_task(record['conversation_id'],course_id,current | {'status':'cancelled'})
        return current if task and current and task['id'] == current['id'] else None

    def finish(self, run_id, owner, status, next_nodes=(), result=None):
        with self.conn() as db:
            changed = db.execute('UPDATE agent_runs SET status=?,next_nodes=?,result=COALESCE(?,result),owner=NULL,lease=0,updated=? WHERE id=? AND owner=?',
                                 (status,json.dumps(list(next_nodes)),json.dumps(result,ensure_ascii=False) if result is not None else None,time.time(),run_id,owner)).rowcount
        if not changed:
            raise conflict('执行已删除或所有权已失效')

    def event(self, run_id, owner, event):
        with self.conn() as db:
            if not db.execute('SELECT 1 FROM agent_runs WHERE id=? AND owner=?', (run_id,owner)).fetchone():
                raise conflict('执行已删除或所有权已失效')
            cursor = db.execute('INSERT INTO agent_events(run_id,event) VALUES(?,?)', (run_id,json.dumps(event,ensure_ascii=False)))
            return event | {'seq': cursor.lastrowid}

    def publish(self, run_id, course_id, data):
        """Final answer and the published flag commit together; retries cannot duplicate history."""
        with self.conn() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM agent_runs WHERE id=? AND course_id=?', (run_id,course_id)).fetchone()
            if not row:
                raise NotFoundException('执行不存在')
            if row['published']:
                return json.loads(row['result'])
            if row['status'] != 'ready':
                raise conflict('执行尚未完成，不能发布回答')
            question = json.loads(row['request'])['question']
            for role, content in (('user',question),('assistant',data['answer'])):
                db.execute('INSERT INTO messages(id,conversation_id,role,content,citations,intent,grounded,mode) VALUES(?,?,?,?,?,?,?,?)',
                           (run_id+'_'+role,row['conversation_id'],role,content,json.dumps(data['citations'] if role == 'assistant' else [],ensure_ascii=False),
                            json.dumps(data['intent'] if role == 'user' else {},ensure_ascii=False),int(data['grounded']),data['intent']['mode']))
            data['execution']['status'] = 'completed'
            db.execute("UPDATE agent_runs SET published=1,status='completed',result=?,updated=? WHERE id=?", (json.dumps(data,ensure_ascii=False),time.time(),run_id))
            db.execute("UPDATE conversations SET updated_at=datetime('now','localtime') WHERE id=?", (row['conversation_id'],))
        return data


class SQLiteGraphSaver(BaseCheckpointSaver):
    """The synchronous BaseCheckpointSaver contract for this application's SQLite storage."""
    def __init__(self, store, owner=None):
        super().__init__()
        self.store = store
        self.owner = owner

    def _fence(self, db, run_id):
        if self.owner is not None:
            active = self.owner()
            if not active or not db.execute('SELECT 1 FROM agent_runs WHERE id=? AND owner=? AND lease>?', (run_id,active,time.time())).fetchone():
                raise conflict('图写入的执行租约已失效')

    @staticmethod
    def config(run_id, ns, checkpoint=None):
        return {'configurable': {'thread_id':run_id,'checkpoint_ns':ns, **({'checkpoint_id':checkpoint} if checkpoint else {})}}

    def get_tuple(self, config):
        cfg = config['configurable']
        rid, ns = cfg['thread_id'], cfg.get('checkpoint_ns','')
        with self.store.conn() as db:
            sql = 'SELECT * FROM graph_checkpoints WHERE run_id=? AND ns=?'
            args = [rid,ns]
            if cfg.get('checkpoint_id'):
                sql += ' AND id=?'
                args.append(cfg['checkpoint_id'])
            row = db.execute(sql+' ORDER BY id DESC LIMIT 1', args).fetchone()
            if not row:
                return None
            writes = [(r['task'],r['channel'],self.serde.loads_typed((r['type'],r['data']))) for r in db.execute('SELECT * FROM graph_writes WHERE run_id=? AND ns=? AND checkpoint=? ORDER BY task,idx', (rid,ns,row['id']))]
        return CheckpointTuple(self.config(rid,ns,row['id']),self.serde.loads_typed((row['type'],row['data'])),json.loads(row['metadata']),
                               self.config(rid,ns,row['parent']) if row['parent'] else None,writes)

    def list(self, config, *, filter=None, before=None, limit=None):
        clauses, args = [], []
        if config:
            clauses += ['run_id=?','ns=?']
            args += [config['configurable']['thread_id'],config['configurable'].get('checkpoint_ns','')]
        if before:
            clauses += ['id<?']
            args += [before['configurable']['checkpoint_id']]
        with self.store.conn() as db:
            rows = db.execute('SELECT run_id,ns,id,metadata FROM graph_checkpoints'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY id DESC',args).fetchall()
        count = 0
        for row in rows:
            if filter and any(json.loads(row['metadata']).get(k) != v for k,v in filter.items()):
                continue
            if limit is not None and count >= limit:
                break
            yield self.get_tuple(self.config(row['run_id'],row['ns'],row['id']))
            count += 1

    def put(self, config, checkpoint, metadata, new_versions):
        cfg = config['configurable']
        rid, ns = cfg['thread_id'],cfg.get('checkpoint_ns','')
        kind, data = self.serde.dumps_typed(checkpoint)
        with self.store.conn() as db:
            db.execute('BEGIN IMMEDIATE')
            self._fence(db,rid)
            db.execute('INSERT OR REPLACE INTO graph_checkpoints VALUES(?,?,?,?,?,?,?)', (rid,ns,checkpoint['id'],cfg.get('checkpoint_id'),kind,data,json.dumps(metadata,ensure_ascii=False)))
        return self.config(rid,ns,checkpoint['id'])

    def put_writes(self, config, writes, task_id, task_path=''):
        cfg = config['configurable']
        with self.store.conn() as db:
            db.execute('BEGIN IMMEDIATE')
            self._fence(db,cfg['thread_id'])
            for idx,(channel,value) in enumerate(writes):
                kind,data = self.serde.dumps_typed(value)
                idx = WRITES_IDX_MAP.get(channel,idx)
                verb = 'REPLACE' if idx < 0 else 'IGNORE'
                db.execute(f'INSERT OR {verb} INTO graph_writes VALUES(?,?,?,?,?,?,?,?)', (cfg['thread_id'],cfg.get('checkpoint_ns',''),cfg['checkpoint_id'],task_id,idx,channel,kind,data))

    def delete_thread(self, thread_id):
        with self.store.conn() as db:
            db.execute('DELETE FROM graph_writes WHERE run_id=?',(thread_id,))
            db.execute('DELETE FROM graph_checkpoints WHERE run_id=?',(thread_id,))
