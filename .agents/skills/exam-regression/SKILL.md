---
name: exam-regression
description: exam 更新后的针对性验证、回归检查和测试精简，用于选择必要检查、保留核心行为断言，不镜像所有工具函数。
---

# 验证更新

根据调用链先跑相关模块；跨核心链路的修改再跑完整离线回归。

```powershell
uv run --no-sync pytest -q tests/test_retrieval.py
uv run --no-sync pytest -q
```

前端改动时，在 frontend/ 依次运行 pnpm lint、pnpm test、pnpm build。
保留已有 GPU Torch、Paddle 和解析器环境，日常检查不执行同步或安装；新增依赖遵守 AGENTS.md。
集成测试需要显式模型环境，未经授权不发真实模型请求；说明未验证的外部能力。

- 检索：保留课程、版本、场景、日期边界、列表去重和公式邻页断言。
- 问答：保留拒答、引用、保存、流式事件顺序和异常结果断言。
- 入库：保留解析回退、元数据与版本替换的成功/失败边界。
- 配置：保留混合请求零写入、故障恢复、密钥掩码和重启提示。

精简测试只合并准备数据、参数化同类输入，保留独立行为断言。
替身测试不等于真实 OCR、Embedding、LLM 或浏览器验收；报告实际结果和限制。
