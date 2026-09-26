"""聚合 v1 路由，main.py 只 include 一次。"""

from fastapi import APIRouter

from src.apis.v1 import agent, ask, catalog, config, conversations, documents, embedding, health, llm_providers, question_bank, vlm

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(config.router)
api_router.include_router(llm_providers.router)
api_router.include_router(catalog.router)
api_router.include_router(embedding.router)
api_router.include_router(documents.router)
api_router.include_router(conversations.router)
api_router.include_router(ask.router)
api_router.include_router(agent.router)
api_router.include_router(question_bank.router)
api_router.include_router(vlm.router)
