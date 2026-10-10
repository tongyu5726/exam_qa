"""隔离的 Agent 出题浏览器验收：固定模型桩、临时题库，无外部请求。
uv run --no-sync python -m tools.preview.serve_agent_preview --port 8877
"""
import json
from unittest.mock import MagicMock, patch
import uvicorn
from tools.preview.serve_practice_preview import main

_run = uvicorn.run


def run_preview(app, **kwargs):
    from src.dependencies import get_llm_client, get_vector_store
    llm = MagicMock(configured=True, model="preview-model")
    hit = {"id": "preview_ev", "text": "采样频率至少为最高频率的两倍。", "score": .9,
           "metadata": {"course_id": "course-default", "chapter": "第三章", "source_file": "signals.md", "page": 1}}

    def decide(messages, schemas, **options):
        names = {s["function"]["name"] for s in schemas}
        if "respond_to_user" in names:
            question = next(m["content"] for m in reversed(messages) if m["role"] == "user")
            if "准备五个" in question:
                name, args = "create_task", {"kind": "question_generate", "topic": "卷积", "count": 5}
            elif "介绍" in question or "什么ai" in question.lower():
                name, args = "respond_to_user", {"kind": "conversation", "topic": "identity", "answer": "我是溯知，一个帮助你学习课程资料的 AI Agent。当前配置的模型标识是 preview-model。我可以据源讲解、出题、组卷并带你复习。"}
            elif "谢谢" in question:
                name, args = "respond_to_user", {"kind": "conversation", "topic": "social", "answer": "不客气，需要继续学习时直接告诉我。"}
            elif any(m["role"] == "tool" for m in messages):
                return {"content": "采样频率至少为最高频率的两倍。", "tool_calls": []}
            else:
                name, args = "rag_answer", {"query": question}
            return {"content": "", "tool_calls": [{"id": name, "name": name, "arguments": json.dumps(args, ensure_ascii=False)}]}
        study = any(s["function"]["name"] == "build_study_plan" for s in schemas)
        if study:
            context_text = next(m["content"] for m in reversed(messages) if m["role"] == "system" and '\n{"spec":' in m["content"])
            context = json.JSONDecoder().raw_decode('{"spec":' + context_text.split('\n{"spec":', 1)[1])[0]
            observations = [json.loads(m["content"]) for m in messages if m.get("role") == "tool"]
            plan = context.get("plan") or []
            if not observations:
                name = "replan_study_goal" if context.get("needs_replan") else "assess_study_progress" if context.get("feedback_pending") else "build_study_plan" if not plan else "save_study_questions" if context["spec"]["save_requested"] else "execute_study_step"
            else:
                name = observations[-1].get("next", "finish_study_goal")
                plan = next((o["plan"] for o in reversed(observations) if "plan" in o), plan)
            if name == "execute_study_step":
                step = next((s for s in plan if s["status"] != "done"), None)
                if step:
                    args = {"step_id": step["id"]}
                else:
                    name, args = "finish_study_goal", {}
            else:
                args = {}
            return {"content": "", "tool_calls": [{"id": str(len(observations)), "name": name, "arguments": json.dumps(args)}]}
        paper = any(s["function"]["name"] == "prepare_paper_draft" for s in schemas)
        observations = [json.loads(m["content"]) for m in messages if m.get("role") == "tool"]
        if not observations:
            name = "retrieve_evidence"
            # 已验证的预览任务允许直接保存。
            if '"validated": true' in messages[0]["content"]:
                name = "save_paper_draft" if paper else "save_question_drafts"
        elif "evidence" in observations[-1]:
            name = "prepare_paper_draft" if paper else "generate_question_drafts"
        else:
            name = observations[-1].get("next", "validate_question_drafts")
        return {"content": "", "tool_calls": [{"id": str(len(observations)), "name": name, "arguments": "{}"}]}

    def chat(messages, **options):
        if "核验普通交流" in messages[0]["content"]:
            return '{"allowed": true}'
        if "你是课程复习计划器" in messages[0]["content"]:
            steps = [{"action": "self_test", "focus": "采样条件", "reason": "通过自测定位需要补讲的内容"}, {"action": "explain", "focus": "采样条件", "reason": "结合本轮作答补讲"}]
            if "先讲" in messages[1]["content"]:
                steps.reverse()
            return json.dumps({"steps": steps}, ensure_ascii=False)
        if "你是课程复习反馈决策器" in messages[0]["content"]:
            data = json.loads(messages[1]["content"])
            wrong = any(not r["correct"] for r in data["results"])
            action = "retest" if wrong and data["retests_used"] < 2 else "review" if wrong else "continue"
            return json.dumps({"action": action, "focus": "采样条件", "reason": "追加重测已达上限，本轮完成据源错项补讲" if action == "review" else "本轮答错下限，先补讲再换题复核" if wrong else "本轮新题回答正确，继续现有计划"}, ensure_ascii=False)
        if "独立出题校验员" in messages[0]["content"]:
            data = json.loads(messages[1]["content"])
            return json.dumps([{"index": i+1, "supported": True, "reason": "固定样例支持"} for i in range(len(data["drafts"]))])
        if "已提交的旧题干" in messages[1]["content"]:
            old = json.loads(messages[1]["content"].split("已提交的旧题干（仅作去重数据，新题不能重复）：", 1)[1])
            stems = ["采样频率下限应当如何选择？", "采样下限与最高频率有什么关系？", "哪种采样条件符合资料要求？"]
            stem = next(s for s in stems if s not in old)
            return json.dumps([{"stem": stem, "options": ["两倍", "一倍", "一半", "任意"], "answer": "两倍", "analysis": "原文要求至少两倍。", "evidence_ids": ["preview_ev"]}], ensure_ascii=False)
        if "题型：short_answer" in messages[1]["content"]:
            return json.dumps([{"stem": "采样频率下限如何确定？", "answer": "至少为最高频率的两倍", "analysis": "原文明确给出下限", "evidence_ids": ["preview_ev"]}], ensure_ascii=False)
        return json.dumps([{"stem": stem, "options": ["两倍", "一倍", "一半", "任意"], "answer": "两倍",
                           "analysis": "原文要求采样频率至少为最高频率的两倍。", "evidence_ids": ["preview_ev"]}
                          for stem in (("采样频率至少为最高频率的多少倍？",) if "题数：1" in messages[1]["content"] else ("采样频率至少为最高频率的多少倍？", "最高频率翻倍后，最低采样频率如何变化？"))], ensure_ascii=False)

    llm.chat_with_tools.side_effect = decide
    llm.chat.side_effect = chat
    app.dependency_overrides[get_llm_client] = lambda: llm
    app.dependency_overrides[get_vector_store] = lambda: MagicMock()
    lecture = {"answer": "采样频率至少为最高频率的两倍。低于该下限不能满足资料给出的采样条件。", "grounded": True, "citations": [{"source_file": "signals.md", "page": 1, "snippet": hit["text"], "score": .9}]}
    with patch("src.services.rag._retrieve", return_value=[hit]), patch("src.services.rag.answer", return_value=lecture):
        return _run(app, **kwargs)


if __name__ == "__main__":
    with patch("uvicorn.run", side_effect=run_preview):
        main()
