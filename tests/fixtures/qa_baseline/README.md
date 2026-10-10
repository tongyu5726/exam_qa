# 课程问答质量基线

30 个用例的结论要求和原文锚点见 cases.jsonl，人工固定范围与时效见 sources.json。
signals、lab、policy 是仓库已有测试课程资料的固定 Markdown 快照；policy-draft 是专为冲突验证编写的低权威草案。
它们是可重复的代表性样例，不代表用户真实学期全部资料。当前覆盖表格文字读取，不覆盖 PDF 视觉读图或原 DOCX/PPTX 解析质量。

只校验原文锚点，不调用模型：

```powershell
uv run --no-sync python -m tests.eval.run_qa_eval
```

使用当前已配置的 Embedding / LLM 运行真实链路：

```powershell
uv run --no-sync python -m tests.eval.run_qa_eval --live --output docs/artifacts/quality/qa-baseline-report.json
```

该命令只发送此处固定样例，创建临时 Chroma/SQLite，不改写现有资料库。使用本地 Embedding 时只接受已有模型缓存，不自动下载；不经过应用启动的配置/提供商写回流程。
报告逐题记录回答、引用、耗时和异常标记，逐题复核 required_facts、公式条件、引用是否支持回答，再填写 review。
`refusal_mismatch`、`unexpected_refusal`、`expected_source_missing` 是机器异常标记，不能据此报告事实准确率。
拒答用例必须是 grounded=false 且固定文案；仅回答“我不知道”不满足产品契约。
