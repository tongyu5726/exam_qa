"""POST /api/v1/ask — 问答（SSE；必填 course_id）。"""

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from starlette import status

from src.dependencies import (
    get_catalog_store,
    get_conversation_store,
    get_current_user,
    get_llm_client,
    get_question_bank_store,
    get_vector_store,
)
from src.exceptions import AppException
from src.models import AskRequest
from src.services.llm import OpenAIClient
from src.services.query import ask as query_ask
from src.services.query import ask_stream as query_ask_stream
from src.services.storage.catalog_store import CatalogStore
from src.services.storage.conversation_store import ConversationStore
from src.services.storage.question_bank_store import QuestionBankStore
from src.services.storage.vector_store import ChromaVectorStore

router = APIRouter(prefix="/ask", tags=["ask"])


def _sse_encode(events: Iterator[dict]) -> Iterator[str]:
    for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("")
def ask_question(
    body: AskRequest,
    vs: ChromaVectorStore = Depends(get_vector_store),
    llm: OpenAIClient = Depends(get_llm_client),
    catalog: CatalogStore = Depends(get_catalog_store),
    conv_store: ConversationStore = Depends(get_conversation_store),
    bank: QuestionBankStore = Depends(get_question_bank_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(body.course_id)

    if body.stream:

        def event_gen():
            try:
                for line in _sse_encode(
                    query_ask_stream(
                        question=body.question,
                        mode=body.mode,
                        vs=vs,
                        llm=llm,
                        course_id=body.course_id,
                        conversation_store=conv_store,
                        conversation_id=body.conversation_id,
                        scenario=body.scenario,
                        as_of=body.as_of,
                        scope_override=body.scope_override,
                        question_bank_store=bank,
                        breakpoints=body.breakpoints,
                    )
                ):
                    yield line
            except AppException as exc:
                yield (
                    f"data: {json.dumps({'type': 'error', 'message': exc.message}, ensure_ascii=False)}\n\n"
                )
            except Exception as exc:
                yield (
                    f"data: {json.dumps({'type': 'error', 'message': str(exc) or '问答失败'}, ensure_ascii=False)}\n\n"
                )

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = query_ask(
        question=body.question,
        mode=body.mode,
        vs=vs,
        llm=llm,
        course_id=body.course_id,
        conversation_store=conv_store,
        conversation_id=body.conversation_id,
        scenario=body.scenario,
        as_of=body.as_of,
        scope_override=body.scope_override,
        question_bank_store=bank,
        breakpoints=body.breakpoints,
    )

    return {
        "code": status.HTTP_200_OK,
        "data": result.model_dump(),
    }
