# AGENTS.md

> 溯知（exam-rag）· 据源而答的课程资料问答（FastAPI + Chroma + SQLite 本地 RAG）。
> 完整约束见 `docs/01-产品边界.md`、`docs/03-工程规范.md`、`docs/04-后续演进规范.md`；本文是 Codex 在本仓库工作时的强制约定。

## 项目概览

- 两条独立流水线：**入库**（上传 → 解析 → 分块 → 向量化 → 写入）与**查询**（检索 → 阈值 → 生成/拒答）。
- 前端在 `frontend/`（Vue 3 + Vite），只调 `/api/v1/*`；`www-dist/` 是被忽略的构建产物；RAG 逻辑全部在 `src/services/`，不进 UI。
- 多课隔离：一切检索/上传/删除都带 `course_id`。

## 技术栈（不可随意更换）

- Python ≥ 3.11、FastAPI ≥ 0.115、uvicorn ≥ 0.30、ChromaDB ≥ 0.5、SQLite（标准库）、OpenAI 兼容 LLM API。
- 依赖锁在 `uv.lock`；**只用 `uv add <package>`** 添加依赖，不手写 `pyproject.toml`、不手动改 `uv.lock`、不走 `pip install`。

## 运行与验证

```bash
uv sync --locked             # 仅首次安装或主动同步环境时执行
uv run --no-sync exam        # 启动服务（端口读 .env 的 PORT，默认 8787）
uv run --no-sync pytest -q   # 单元测试（默认跳过 integration）
uv run --no-sync pytest -q -m integration   # 集成测试（需显式传入 Embedding + LLM 环境变量）
```

- 服务启动时按需执行 Vue 构建；前端开发在 `frontend/` 运行 `pnpm dev`，手动验证用 `pnpm lint && pnpm test && pnpm build`。提交源码和 `pnpm-lock.yaml`，不提交 `www-dist/`。
- 已有定制 GPU Torch / Paddle / 外部解析器的环境，日常启动与测试使用 `--no-sync`；修改发布依赖时只解析锁文件，验证安装另建环境，保留本地 `.env`、资料和已安装包。
- 环境要求：PDF 默认尝试外部 MinerU，不可用时回退 PyMuPDF 链路；扫描版 PDF 的回退 OCR 可选 Tesseract；旧版 `.doc` 转换需 LibreOffice 或本机 Word。

## 工程规范

- **配置**：优先级 环境变量 > `.env` > 代码默认值；敏感参数走环境变量，不硬编码；应用参数集中在 `src/config.py` 的 dataclass；`.env` 不入 git，改模板用 `.env.example`。
- **日志**：标准库 `logging`，模块级 `logging.getLogger(__name__)`；禁止 `print()`；级别 DEBUG/INFO/WARNING/ERROR。
- **异常**：业务异常继承 `src/exceptions.py` 的 `AppException`；外部库异常在调用处转为业务异常；禁止 `except Exception: pass`；`services` 层不捕获异常（除非转业务异常），`apis` 层不写 try/except。
- **存储**：业务代码不得直接 `chromadb.PersistentClient` 或裸 `sqlite3.connect`，必须走 `src/services/storage/` 抽象（`VectorStore` / `DocStore`）。
- **接口**：成功 `{"code": 200, "data": ...}`；失败 `{"code": HTTP, "message": ..., "detail": ...}`；用 HTTP 状态码，不自定义业务错误码；`detail` 仅 DEBUG 模式返回。
- **依赖注入**：复用组件经 `src/dependencies.py` 的 `@lru_cache()` 工厂注入，保持单例。

## 产品边界（不做的事）

- 必须实现**拒答**：检索最高分低于阈值时返回 `grounded: false`，固定文案「资料库中未找到相关内容」，禁止无引用硬编。
- 不做：用户管理/登录、多人协作、在线编辑器、MCP/Skills 框架、跨会话长期记忆。
- PDF 支持自动版本更新：为同课程的新文件计算内容哈希、标识疑似版本，新版解析/入库成功后才替换旧版；旧版保留历史元数据。
- 证据元数据：入库自动提取版本、生效期和权威候选；固定适用范围必须使用人工维护的 `applicability_scope` 场景键。查询传 `scenario` / `as_of` 时先过滤范围与时效，再按权威层级、生效时间择证，并在 citation 解释选择依据。
- 意图路由采用三层漏斗：规则层优先；指代追问继承 `ConversationStore` 的已确认意图；仅无状态的复杂模糊请求允许受约束 LLM 返回 JSON 计划。LLM 不得绕过检索、课程隔离、场景和时效校验；课程隔离不是用户鉴权。
- Agent 是默认对话入口：`/ask` 与 `/agent/run` 默认执行 `agent ↔ tool` 决策循环。RAG 位于 `src/services/rag.py`，通过 `rag_answer` 注册为工具，复用检索/生成/视觉复核，不重写入库主链路。
- `agentic` 默认 `true`；工具决策异常或未配置时降级执行同一 RAG 工具。`/agent/run` 显式 `agentic=false` 才走旧固定图。工具白名单、系统强制课程/模式/场景/日期/历史、循环上限、拒答和引用约束必须保留；响应返回脱敏 `tool_calls` 与实际路径。仍不引入 MCP / Skills。
- “我的题库”位于 `QuestionBankStore`、`services/question_bank.py`、`/api/v1/question-bank/*` 与 `/sz-bank/`：自动出题必须先检索当前课程的有效证据，无证据不保存；题目和试卷均以 `course_id` 隔离。自动组卷只复用带有效证据的题目，缺题时才受控补题，并校验蓝图、去重、总分与课程范围。

## 测试约定

- pytest 覆盖核心链路：对话（`test_ask`）、入库（`test_ingest`）、检索隔离（`test_retrieval`）、解析（`test_parsing`）、API（`test_api`）。
- 覆盖核心链路，不镜像每个工具函数；离线 Recall@K / MRR 用 `tests/eval/run_retrieval_eval.py`，不进默认 pytest。
- 集成测试标 `@integration`。

## 项目更新技能

开发技能统一放在 `.agents/skills/`，不进入产品运行时。

- [证据链更新](.agents/skills/exam-rag-update/SKILL.md)：入库、检索、问答与 Agent。
- [配置更新](.agents/skills/exam-config-update/SKILL.md)：配置字段、保存与设置页。
- [回归与测试精简](.agents/skills/exam-regression/SKILL.md)：选择检查、保留行为断言。
- [优化与文档更新](.agents/skills/exam-optimization-update/SKILL.md)：继续计划、记录净变化。

- 对话出题任务使用 `agent/tasks.py` 的四个任务工具，缺参先澄清，单批 1—10 道单一题型。仅明确授权后保存；无有效证据、题目重复或独立证据校验失败均不得保存。任务结果来自工具状态。检查点按对话/课程隔离，批次保存按任务 ID 原子幂等；取消保留已保存结果。任务异常不得静默降级为普通问答。

- 对话混合组卷位于 `agent/paper_tasks.py`，使用独立组卷工具白名单并共享 `question_bank.prepare_paper` 选择蓝图题目。先匹配当前有效证据，补题只生成草稿，独立校验后按授权保存。题数1—10，各题型分值明确，总分冲突先澄清；默认只复用，明确允许补题才生成。补题、试卷与任务批次必须单事务幂等保存，保存前检查资料和复用题更新时间；失败不留下半份写入。SSE `tool` 事件与最终 `tool_calls` 展示实际执行观察。


复习目标使用 `src/services/agent/study_goals.py` 的当前对话计划层：限定讲解、自测、错项复习，隐藏未提交答案，按反馈调整剩余计划，完成前复核资料和过程条件。默认不保存，明确授权才调用原有题库保存链路；不推断掌握度，不增加跨会话记忆。完整契约见 `docs/04-后续演进规范.md`「对话复习目标」。

作答提交后必须先执行 `assess_study_progress` 检查反馈；错项须有提交后的据源补讲，自动追加重测最多两次，新题不得重复已提交题干，反馈安排必须纳入完成条件。

对话恢复通过按课程隔离的历史读取接口加载已发布消息和 `public_task` 脱敏检查点，禁止恢复时自动调用模型、泄露未提交答案或伪造历史工具记录；删除对话同时删除服务端检查点。

配置模型下的身份、能力、帮助、交流和语义任务请求由 Agent 决策；`respond_to_user` 的非课程回复须独立核验，`create_task` 的结构化需求复用原任务链路。完整请求交流规则仅用于未配置/决策异常时的降级。混合课程问题仍据源回答，不能以交流掩盖取证失败。模型不授予保存或补题许可；询问保存方法和引用文字不是授权。交流及无任务澄清不覆盖课程意图、不消费检查点。两个对话 API 共用范围解析、执行与历史保存；契约见 docs/04-后续演进规范.md「Agent 交互与任务入口」。

配置模型下的对话执行经 `agent/execution.py` 使用原生 LangGraph 检查点；每个工具为独立节点。节点边界暂停、单步与恢复使用同一 run/thread，不得以新规划冒充原图恢复。执行开始先原子预约对话，恢复采用租约防止并发；取消不可被失联后的重试撤销。节点观察与检查点历史只发布脱敏摘要，禁止提示词、原始资料、未提交答案出现在追踪 API。最终消息与执行发布标记原子提交；删除对话级联清理图检查点、写入与观察。普通问答降级必须进入可观测 fallback 节点，业务任务异常仍不得降级。完整契约见 docs/04「Agent 节点执行控制」。
