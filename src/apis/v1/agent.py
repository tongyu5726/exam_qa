"""POST /agent/run — LangGraph Agent（P2-B 固定图 / P2-C 工具调用）。"""

from fastapi import APIRouter, Depends, Query
from starlette import status

from src.dependencies import (
    get_catalog_store,
    get_current_user,
    get_conversation_store,
    get_question_bank_store,
    get_llm_client,
    get_vector_store,
)
from src.models import AgentRunData, AgentRunRequest, AgentControlRequest
from src.services.query import ask
from src.services.llm import OpenAIClient
from src.services.storage.catalog_store import CatalogStore
from src.services.storage.vector_store import ChromaVectorStore

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/run")
def agent_run(
    body: AgentRunRequest,
    vs: ChromaVectorStore = Depends(get_vector_store),
    llm: OpenAIClient = Depends(get_llm_client),
    catalog: CatalogStore = Depends(get_catalog_store),
    conversation_store=Depends(get_conversation_store),
    question_bank_store=Depends(get_question_bank_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(body.course_id)
    result = ask(
        question=body.question, mode=body.mode if body.agentic or body.mode != "auto" else "qa", vs=vs, llm=llm, course_id=body.course_id,
        conversation_store=conversation_store, conversation_id=body.conversation_id,
        scenario=body.scenario, as_of=body.as_of, scope_override=body.scope_override,
        question_bank_store=question_bank_store, agentic=body.agentic,
        max_steps=body.max_steps if "max_steps" in body.model_fields_set else None,
        top_k=body.top_k, score_threshold=body.score_threshold,
        breakpoints=body.breakpoints,
    )
    return {"code": status.HTTP_200_OK, "data": AgentRunData(**result.model_dump()).model_dump()}


@router.get('/nodes')
def agent_nodes():
    from src.services.agent.execution import NODE_NAMES
    return {'code':200,'data':list(NODE_NAMES)}


@router.get('/runs/{run_id}')
def get_run(run_id: str, course_id: str = Query(...,min_length=1), store=Depends(get_conversation_store), _user=Depends(get_current_user)):
    from src.services.storage.agent_run_store import AgentRunStore
    return {'code':200,'data':AgentRunStore(store).public(run_id,course_id)}


@router.post('/runs/{run_id}/control')
def control_run(run_id: str, body: AgentControlRequest, store=Depends(get_conversation_store),
                vs=Depends(get_vector_store),llm=Depends(get_llm_client),bank=Depends(get_question_bank_store),_user=Depends(get_current_user)):
    from src.services.agent.execution import validate_breakpoints
    from src.services.storage.agent_run_store import AgentRunStore
    from src.services.query import resume_execution
    runs = AgentRunStore(store)
    if body.breakpoints is not None:
        runs.control(run_id,body.course_id,'breakpoints',validate_breakpoints(body.breakpoints))
    if body.action in ('resume','step','retry'):
        data = resume_execution(run_id,body.course_id,'step' if body.action == 'step' else 'run',store,vs,llm,bank)
    else:
        data = runs.control(run_id,body.course_id,body.action)
    return {'code':200,'data':data}


@router.get('/runs/{run_id}/checkpoints')
def get_checkpoints(run_id: str, course_id: str = Query(...,min_length=1), store=Depends(get_conversation_store), _user=Depends(get_current_user)):
    from src.services.storage.agent_run_store import AgentRunStore
    return {'code':200,'data':AgentRunStore(store).checkpoint_history(run_id,course_id)}
