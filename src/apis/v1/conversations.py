"""GET/POST/DELETE /api/v1/conversations —— 多轮对话管理。"""

from fastapi import APIRouter, Depends, Query
from starlette import status

from src.dependencies import get_conversation_store, get_current_user
from src.services.storage.conversation_store import ConversationStore
from src.services.conversation_history import read_conversation
from src.exceptions import NotFoundException

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("")
async def create_conversation(
    course_id: str = Query(..., description="课程 ID"),
    title: str = Query("新对话", description="对话标题"),
    store: ConversationStore = Depends(get_conversation_store),
    _user=Depends(get_current_user),
):
    conv = store.create_conversation(course_id, title)
    return {"code": status.HTTP_200_OK, "data": conv}


@router.get("")
async def list_conversations(
    course_id: str = Query(..., description="课程 ID"),
    store: ConversationStore = Depends(get_conversation_store),
    _user=Depends(get_current_user),
):
    convs = store.list_conversations(course_id)
    return {"code": status.HTTP_200_OK, "data": convs}


@router.get("/{conv_id}")
async def get_conversation_detail(
    conv_id: str,
    course_id: str = Query(..., min_length=1, description="课程 ID（校验归属）"),
    limit: int = Query(200, ge=1, le=500),
    store: ConversationStore = Depends(get_conversation_store),
    _user=Depends(get_current_user),
):
    return {"code": status.HTTP_200_OK, "data": read_conversation(store, conv_id, course_id, limit=limit)}


@router.delete("/{conv_id}")
async def delete_conversation(
    conv_id: str,
    course_id: str = Query(..., description="课程 ID（校验归属）"),
    store: ConversationStore = Depends(get_conversation_store),
    _user=Depends(get_current_user),
):
    conv = store.get_conversation(conv_id)
    if not conv or conv.get("course_id") != course_id:
        raise NotFoundException("对话不存在或不属于当前课程")
    store.delete_conversation(conv_id)
    return {"code": status.HTTP_200_OK, "data": None}
