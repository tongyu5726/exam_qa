"""配置变更生效方式标签（立即 / 下次请求 / 需重启）。"""

from __future__ import annotations

APPLY_IMMEDIATE = "immediate"
APPLY_NEXT_TURN = "next_turn"
APPLY_RESTART = "restart"

SECTION_APPLY_MODE: dict[str, str] = {
    "llm": APPLY_NEXT_TURN,
    "embedding": APPLY_NEXT_TURN,
    "retrieval": APPLY_NEXT_TURN,
    "chunk": APPLY_NEXT_TURN,
    "parsing": APPLY_NEXT_TURN,
    "app": APPLY_IMMEDIATE,
    "server": APPLY_RESTART,
    "proxy": APPLY_IMMEDIATE,
}


def build_settings_effects(patched_sections: list[str]) -> dict:
    hot_reload: list[str] = []
    restart_required: list[str] = []
    notes: list[str] = []
    for section in patched_sections:
        mode = SECTION_APPLY_MODE.get(section, APPLY_NEXT_TURN)
        title = {
            "llm": "LLM",
            "embedding": "Embedding",
            "retrieval": "检索",
            "chunk": "分块",
            "parsing": "PDF 解析",
            "app": "上传/日志",
            "server": "端口",
            "proxy": "网络代理",
        }.get(section, section)
        if mode == APPLY_RESTART:
            restart_required.append(title)
        else:
            hot_reload.append(title)
    if "embedding" in patched_sections:
        notes.append(
            "更换 Embedding 模型/维度后，首次入库会重建向量库；"
            "旧资料若标为 failed，请重新扫描或上传。"
        )
    return {
        "hot_reload": hot_reload,
        "restart_required": restart_required,
        "notes": notes,
    }
