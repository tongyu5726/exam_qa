"""Trusted product fallback when model decisions are unavailable; not the semantic router."""
import re

_RULES = (
    ("model", r"(?:你(?:现在|目前)?(?:用|使用)(?:的)?(?:是)?什么(?:大语言|语言|ai)?模型|你是什么(?:大语言|语言|ai)?模型|你的(?:底层)?模型(?:是什么|叫什么)|你(?:是|用的是)(?:哪种|哪个|什么)模型)"),
    ("identity", r"(?:你(?:是(?:谁|什么(?:ai|人工智能|助手|机器人)?)|叫什么(?:名字)?|是(?:ai|人工智能|机器人|智能体|agent)吗)|介绍(?:一下)?(?:你自己|自己)|你能介绍一下自己吗|who are you)"),
    ("capabilities", r"(?:你(?:都)?(?:能|可以)(?:做什么|干什么|帮我做什么|帮我干什么|帮我什么)|你有什么(?:功能|能力)|你能帮我学习吗|怎么使用(?:你|溯知)|如何使用(?:你|溯知))"),
    ("upload", r"(?:怎么|如何)(?:上传|添加|导入)(?:课程)?资料(?:到资料库)?"),
    ("greeting", r"(?:你好|您好|嗨|哈喽|hello|hi|早上好|晚上好|在吗|你在吗)"),
    ("thanks", r"(?:谢谢(?:你|您)?|感谢(?:你|您)?|多谢|辛苦了|thanks|thank you)"),
)


def classify_dialogue(question: str) -> str | None:
    # Match the entire request. A greeting prefix must not bypass course grounding.
    text = re.sub(r"\s+", "", question).lower().strip("。！？!?，,.~～")
    for kind, pattern in _RULES:
        if re.fullmatch(pattern.replace(" ", ""), text):
            return kind
    return None


def dialogue_result(kind: str, model: str = "") -> dict:
    model_info = f"当前配置的底层模型标识是 {model}。" if model else "底层模型由设置中选中的服务商提供，可在设置中查看。"
    answers = {
        "identity": "我是溯知，一个帮助你学习课程资料的 AI Agent。我可以检索资料、解释知识点、出题、组卷并带你复习。" + model_info,
        "model": model_info + "溯知是应用名称；模型标识来自当前客户端配置。",
        "capabilities": "我可以按当前课程检索资料并引用原文，讲解知识点、生成练习题、组卷和安排讲解—自测—错项复习。你也可以直接问我身份、能力和使用方法。出题或组卷默认先预览，明确授权后才保存。课程结论需要资料依据。",
        "upload": "先在顶部选择课程，再打开“资料”页面上传 PDF、TXT、Markdown、Word 或 PowerPoint 文件。等入库完成后回到“对话”提问，我会检索当前课程并给出来源。",
        "greeting": "你好，我是溯知。你可以问我能做什么，也可以告诉我想学习的知识点、出题需求或复习目标。",
        "thanks": "不客气。需要继续讲解、做几道练习或安排复习时，直接告诉我。",
    }
    return {"answer": answers[kind], "citations": [], "grounded": False,
            "response_kind": "conversation", "tool_calls": [], "steps": [f"dialogue: {kind}"],
            "agentic": False, "agent_used": True, "task": None, "artifacts": []}
