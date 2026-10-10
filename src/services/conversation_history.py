"""Read-only recovery of published turns and the current scoped checkpoint."""
from src.exceptions import NotFoundException
from src.services.agent.tasks import public_task


def read_conversation(store, conversation_id, course_id, *, limit=200):
    conversation = store.get_conversation(conversation_id)
    if not conversation or conversation["course_id"] != course_id:
        raise NotFoundException("对话不存在或不属于当前课程")
    task = store.get_agent_task(conversation_id, course_id)
    snapshot = public_task(task) if task else None
    hints = {"waiting_answer": "请提交本轮答案后继续", "waiting_input": "请补充任务缺少的信息",
             "completed": "本轮任务已完成，可查看结果", "cancelled": "任务已停止；明确重试后才继续",
             "no_evidence": "缺少有效资料，可更新资料后重试", "failed": "任务尚未完成，可重试或调整计划",
             "running": "已恢复执行检查点，可继续未完成步骤"}
    from src.services.storage.agent_run_store import AgentRunStore
    return {"conversation": conversation, "messages": store.get_messages(conversation_id, limit),
            'executions':AgentRunStore(store).list_public(conversation_id,course_id),
            "total_messages": store.count_messages(conversation_id), "task": snapshot,
            "resume_hint": hints.get(task["status"], "") if task else ""}
