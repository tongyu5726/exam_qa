"""AgentState：LangGraph 状态字段与累加字段。"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    task: dict | None
    scope_override: bool
    previous_task: dict | None
    task_context_id: str
    direct_response: dict | None
    response_kind: str
    artifacts: list[dict]
    history: list[dict] | None
    question: str
    course_id: str
    mode: str
    top_k: int
    score_threshold: float
    max_steps: int
    attempts: int
    current_query: str
    queries: Annotated[list[str], operator.add]
    steps: Annotated[list[str], operator.add]
    chunks: list[dict]
    citations: list[dict]
    answer: str
    grounded: bool
    decision: str
    scenario: str | None
    as_of: str | None
    messages: Annotated[list[dict], operator.add]
    pending_tool_calls: list[dict]
    tool_batch_size: int
    used_fallback: bool
    tool_calls: Annotated[list[dict], operator.add]
    tool_rounds: int
