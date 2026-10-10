---
name: exam-rag-update
description: 更新 exam 入库、检索、问答或 Agent 证据链时使用，保持课程隔离、拒答、引用和交互契约；纯配置或文档修改不使用。
---

# 更新证据链

先读根目录 AGENTS.md、docs/01-产品边界.md、docs/03-工程规范.md；架构变化再读 docs/02-模块架构.md 与 docs/04-后续演进规范.md。
存在 .codegraph 时，先查待改符号及调用链，以当前实现为准。

- 入库：src/services/ingestion.py、parsing_adapters/、document_updates.py；保留 chunk 顺序、页码和解析来源，新版成功入库后才替换旧版。
- 检索：src/services/retrieval.py、storage/vector_store.py、evidence_metadata.py；强制 course_id，保留版本、场景和时效过滤。
- 问答：src/services/query.py、generation.py、citations.py、apis/v1/ask.py；保留响应、会话保存与引用编号，retrieving 事件先于检索。
- Agent：src/services/agent/；复用主链路，保留白名单、强制范围、循环上限与异常降级。

无有效证据沿用“资料库中未找到相关内容”拒答；题库生成与组卷不得保存无证据结果。
使用 storage 抽象与 dependencies 工厂，外部异常在调用边界转为业务异常。
按影响范围选择 test_ingest、test_retrieval、test_ask、test_agent_tools 及证据测试，验证流程见 ../exam-regression/SKILL.md。
调整召回窗口、读取上限、评分或提示词前做旧新结果对照；缺少质量证据时不缩小候选集。
