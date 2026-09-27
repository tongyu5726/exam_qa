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
- Agent（LangGraph 多步循环）已落地为 P2-B：`src/services/agent/` + `POST /agent/run`，仅薄封装 `retrieve` / `generate`，不重写 P0 主链路（ingestion → retrieval → generation）。
- P2-C 已接入 `/agent/run`：`agentic=true` 启用 `agent ↔ tool` 的 function-calling 循环，默认仍为 P2-B，LLM 异常或未配置会自动降级。只读工具以白名单分发、系统强制 `course_id` / 场景 / 时效范围，并返回脱敏的 `tool_calls` 观测；仍不引入 MCP / Skills。
- “我的题库”位于 `QuestionBankStore`、`services/question_bank.py`、`/api/v1/question-bank/*` 与 `/sz-bank/`：自动出题必须先检索当前课程的有效证据，无证据不保存；题目和试卷均以 `course_id` 隔离。自动组卷只复用带有效证据的题目，缺题时才受控补题，并校验蓝图、去重、总分与课程范围。

## 测试约定

- pytest 覆盖核心链路：对话（`test_ask`）、入库（`test_ingest`）、检索隔离（`test_retrieval`）、解析（`test_parsing`）、API（`test_api`）。
- 覆盖核心链路，不镜像每个工具函数；离线 Recall@K / MRR 用 `tests/eval/run_retrieval_eval.py`，不进默认 pytest。
- 集成测试标 `@integration`。
