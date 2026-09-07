# -*- coding: utf-8 -*-
"""实测当前 LLM 是否支持 function calling（tool_calls）。

仅显式运行时调用外部 API，可能产生服务商费用。
用法（在项目根目录）：
    uv run --no-sync python check_function_calling.py
"""
import logging

logger = logging.getLogger(__name__)


def main() -> int:
    import os
    from dotenv import load_dotenv
    from pathlib import Path

    load_dotenv(Path(__file__).resolve().with_name(".env"))
    if not os.getenv("LLM_API_KEY"):
        logger.error("请先配置 LLM_API_KEY，再运行此手动探测脚本。")
        return 2

    from openai import OpenAI

    client = OpenAI(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        timeout=60,
        max_retries=1,
    )
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    tools = [
        {
            "type": "function",
            "function": {
                "name": "retrieve_material",
                "description": "检索课程资料库，返回与问题相关的原文片段",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "用于检索的关键词"},
                    },
                    "required": ["query"],
                },
            },
        }
    ]

    logger.info("模型: %s", model)



    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "你是课程助教。回答学生问题前，必须先调用 retrieve_material 检索资料。",
            },
            {"role": "user", "content": "卷积定理是什么？"},
        ],
        tools=tools,
        tool_choice="auto",
    )

    msg = resp.choices[0].message
    if msg.tool_calls:
        logger.info("本次成功返回工具调用；未实际执行检索工具。")
        for tc in msg.tool_calls:
            logger.info("工具: %s", tc.function.name)
    else:
        logger.warning("本次未返回工具调用；不能据单次 auto 响应判定模型不支持该功能。")
        return 1

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
