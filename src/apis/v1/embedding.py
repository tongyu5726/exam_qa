"""Embedding 预热 / 状态：本地模型拉取进度 + 远程探测。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from starlette import status

from src.dependencies import get_embedding_client
from src.exceptions import AppException, BadRequestException
from src.services.embedding import (
    EmbeddingClient,
    build_embedding_status,
    start_warmup_background,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["embedding"])


@router.get("/embedding/status")
async def embedding_status(embedding: EmbeddingClient = Depends(get_embedding_client)):
    return {"code": status.HTTP_200_OK, "data": build_embedding_status(embedding)}


@router.post("/embedding/warmup")
async def warmup_embedding(
    request: Request,
    embedding: EmbeddingClient = Depends(get_embedding_client),
):
    """后台拉取/加载本地模型，或探测远程 API；轮询 GET /embedding/status 看进度。"""
    if embedding.status() == "unavailable":
        raise BadRequestException(
            "Embedding 未配置：请设置 EMBEDDING_API_KEY 或切换为本地模型"
        )

    data = build_embedding_status(embedding)
    if data["status"] == "ok":
        return {
            "code": status.HTTP_200_OK,
            "data": {**data, "message": "向量化已就绪"},
        }

    try:
        start_warmup_background(embedding)
    except Exception as e:
        logger.exception("Embedding 启动加载失败")
        raise AppException(f"Embedding 加载失败: {e}", status_code=500) from e

    request.app.state.embedding_health = "loading"
    return {
        "code": status.HTTP_200_OK,
        "data": {
            **build_embedding_status(embedding),
            "message": "已开始加载，优先使用本地缓存；进度见 /embedding/status",
        },
    }
