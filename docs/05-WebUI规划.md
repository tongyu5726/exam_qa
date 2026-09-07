# 05 Web UI 规划

> **状态（2026-07）**：已定稿并随 P2-A 同步。废弃 React/Vite `workbench/`；手写 HTML/CSS/JS，四个页面入口 `/sz`（对话）、`/sz-docs`（资料）、`/sz-cfg`（设置）、`/sz-bank`（题库）。
> 产品：溯知（exam-rag）· 据源而答的课程资料问答
> **关联**：[01-产品边界](./01-产品边界.md) · [02-模块架构](./02-模块架构.md) · [03-工程规范](./03-工程规范.md) · [04-后续演进规范](./04-后续演进规范.md)
> **本轮 UI 契约**：`mode=chapter` · 资料「强制重建」· 设置页 BGE 精排开关

---

## 1. 视觉风格

### 1.1 品牌定位

| 项 | 决策 |
|---|---|
| 产品名 | 溯知（UI 文案）；路径/代码前缀 `sz` |
| 气质 | 专注复习、学术克制、开发者工具感 |
| 视觉方向 | 青绿强调、面板分层、亮/暗双主题 |
| 明确不要 | 花哨 AI 紫渐变、过度玻璃拟态、营销落地页风、React/Vite/组件库堆栈 |

### 1.2 视觉语言

| 项 | 决策 |
|---|---|
| 字体 | LXGW WenKai + Nunito（本地字体） |
| 强调色 | 亮色 `#2a9d8f` / 暗色 `#4ecdc4` |
| 主题 | **默认跟随系统**（`prefers-color-scheme`）；可手动覆盖；写入 `localStorage` 键 `sz.theme`（`system` \| `light` \| `dark`） |
| 圆角 | sm / md / lg ≈ `6 / 8 / 12px` |
| 层次 | 边框分层为主，少阴影 |
| 动效 | CSS transition；遵守 `prefers-reduced-motion` |

### 1.3 页面

| URL | 目录 | 职责 |
|---|---|---|
| `/sz/` | `www/sz/` | 对话：顶栏 + 左会话历史 + 右问答 |
| `/sz-docs/` | `www/sz-docs/` | 资料：上传 / 扫描 / 列表 / 删除 |
| `/sz-cfg/` | `www/sz-cfg/` | 设置：顶栏 + 左配置分组 + 右表单 |
| `/sz-bank/` | `www/sz-bank/` | 题库：基于证据出题、保存与组卷 |
| `/` | — | 有前端时重定向到 `/sz/`；否则 `/docs` |

小屏（`≤768px`）：对话页双栏改为上下堆叠；设置页左列表置顶可滚，主表单在下；顶栏链接可收进「⋯」。

---

## 2. 技术方案

### 2.1 基础栈（已确认）

- [x] **手写** HTML / CSS / JS（ES modules），**无** React、Vite、pnpm、TypeScript、UI 组件库
- [x] **工程形态** 按挂载应用分子目录，CSS/JS 分文件，浏览器直载，无前端构建
- [x] **独立页面挂载点与共享静态资源挂载点**

| 挂载前缀 | 静态目录 | 说明 |
|---|---|---|
| `/sz` | `www/sz/` | 对话 |
| `/sz-docs` | `www/sz-docs/` | 资料 |
| `/sz-cfg` | `www/sz-cfg/` | 设置 |
| `/sz-bank` | `www/sz-bank/` | 题库 |
| `/shared` | `www/shared/` | 共享 tokens、api、theme、shell、KaTeX。**必须单独 mount**：`StaticFiles` 不能从 `/sz` 挂载点越界提供 `../shared`，浏览器把相对路径解析成 `/shared/...` |

### 2.2 与后端集成

| 项 | 约定 |
|---|---|
| API 基址 | 同端口 `/api/v1` |
| 鉴权 | MVP 无登录 |
| 响应 | `{code, data}` / `{code, message}` |
| 健康 | `GET /api/v1/health` → 顶栏状态点 |
| 目录 | `GET /api/v1/colleges`、`GET /api/v1/courses`；`course_id` 必填并持久化 `sz.course_id` |
| 资料 | `POST/GET/DELETE /api/v1/documents`、`POST /api/v1/documents/scan`（Form：`course_id`；可选 `force=true`） |
| 问答 | `POST /api/v1/ask`（`mode=auto|qa|concept|chapter`；`stream=true` → SSE）；展示 citations、KaTeX（拒答看 `grounded`） |
| 配置 | `GET/PATCH /api/v1/config`（含检索精排字段）；LLM 注册/切换：`/api/v1/llm-providers` |

### 2.3 启动

```bash
uv run --no-sync exam
# 浏览器：http://127.0.0.1:8787/sz/
# 资料：  http://127.0.0.1:8787/sz-docs/
# 设置：  http://127.0.0.1:8787/sz-cfg/
```

无 Node 安装/构建步骤。改静态文件后刷新即可（必要时硬刷新）。

### 2.4 非目标

```
- 不引入 React / Vite / Next.js / Ant Design / shadcn
- 不做用户注册登录
- 不做 Agent/MCP 前端
- 前端不写 RAG 逻辑，只调 API
- 不拆独立前端仓库
```

### 2.5 后端挂载改动（实施时）

`src/main.py`：

- 删除「整棵 `www/` 挂到 `/`」的做法
- `app.mount("/shared", StaticFiles(...))`（无 `html=True`）
- `app.mount("/sz", StaticFiles(..., html=True))`
- `app.mount("/sz-docs", StaticFiles(..., html=True))`
- `app.mount("/sz-cfg", StaticFiles(..., html=True))`
- `GET /` → `RedirectResponse("/sz/")`
- banner / 启动日志中的 UI 地址改为 `/sz/`

---

## 3. UI 结构（无组件库）

手写 DOM + CSS 类前缀 `sz-*`。必要区块：

**壳（shared）**

- 顶栏：品牌「溯知」、链到对话 `/sz/`、资料 `/sz-docs/`、设置 `/sz-cfg/`、课程选择、HealthBadge、主题切换
- Toast / 行内错误提示

**对话 `/sz`**

- 左：会话历史（新对话 / 切换 / 删除；`localStorage` 按 `course_id` 隔离）
- 右：回答区（独立滚动，KaTeX + citations）+ 输入区（底部固定）
- 输入区含 **模式**（持久化 `sz.ask_mode`）：

| mode | 文案 | 输入提示 | 行为摘要 |
|------|------|----------|----------|
| `qa` | 自由问答 | 输入问题… | 混合检索（可选精排）→ 生成 |
| `concept` | 知识点 | 如：卷积定理 | 更大 top_k → 定义/公式/例题 |
| `chapter` | 章节概览 | 如：第3章 傅里叶变换 | 按 `chapter` 聚合，不走语义检索 |

- 章节模式空状态须提示：旧资料到资料页勾选「强制重建」再扫描
- 分区各自 `overflow`，互不带动整页滚动

**资料 `/sz-docs`**

- 上传区（进度条；接受 PDF/TXT/MD/DOC/DOCX/**PPTX**）+ 文档列表（独立滚动）+ 删除
- **扫描目录**：Form 带 `course_id`；勾选「强制重建」时附带 `force=true`（确认框后提交），用于补写 `chapter` 等元数据；普通扫描仅处理新文件 / mtime 变更 / failed
- 侧栏/提示文案须说明：章节概览依赖强制重建或重新上传

**设置 `/sz-cfg`**

- 左：配置分组列表（可搜索，独立滚动）
- 右：当前分组表单 + 保存 / 重置（独立滚动）
- **模型管理**：表格式注册表 +「+ 添加模型」；色系用溯知青绿 tokens
- **检索**分组须含精排字段（见 §5）

---

## 4. 目录结构

```
exam/
├── www/
│   ├── shared/                 # mount → /shared
│   │   ├── css/
│   │   │   └── tokens.css
│   │   ├── js/
│   │   │   ├── api.js
│   │   │   ├── theme.js
│   │   │   ├── shell.js
│   │   │   └── conversations.js  # 本地会话历史
│   │   └── lib/katex/
│   ├── sz/                     # mount → /sz（对话）
│   ├── sz-docs/                # mount → /sz-docs（资料）
│   └── sz-cfg/                 # mount → /sz-cfg（设置）
├── src/
└── docs/
```

### 命名规范

| 层 | 规则 | 示例 |
|---|---|---|
| URL 挂载 | 产品前缀短码 | `/sz`、`/sz-docs`、`/sz-cfg` |
| 目录 | 与挂载同名 | `www/sz/`、`www/sz-docs/` |
| CSS 类 | `sz-*` | `sz-shell`、`sz-panel`、`sz-config-list` |
| localStorage | `sz.*` | `sz.theme`、`sz.course_id`、`sz.ask_mode`、`sz.conversations.{courseId}` |
| 后端 API | 不变 | `/api/v1/*` |

禁止再用已删除的 `workbench/`（React）路径与 `pnpm` 工作区约定。

---

## 5. 模块边界

| 模块 | 入口 | 独占职责 | 可依赖 |
|---|---|---|---|
| `shell` | `shared/js/shell.js` | 顶栏、主题、health 轮询、课程选择、页间导航 | `api.js`、`theme.js` |
| `conversations` | `shared/js/conversations.js` | 本地会话 CRUD（按 course_id） | — |
| `workbench` | `sz/js/workbench.js` | 会话历史；`qa|concept|chapter`；问答 SSE、citations、KaTeX | `shared/*` |
| `docs` | `sz-docs/js/docs.js` | 资料 CRUD；扫描可传 `force`；上传进度 | `shared/*` |
| `settings` | `sz-cfg/js/settings.js` | 配置分组（含 BGE 精排）；PATCH、脱敏密钥 | `shared/*` |
| `api` | `shared/js/api.js` | 统一请求与错误 | — |

### 设置页全量可写分组（对应 `PATCH /api/v1/config`）

| 分组 | 字段来源 |
|---|---|
| 模型管理 | 表格式注册表增删/切换（`llm-providers`）+ timeout；活跃项同步写入 `.env` 的 `LLM_*` / `LLM_PROVIDER` |
| Embedding | provider、model、base_url、api_key、timeout；**local 时「拉取并加载模型」**（`POST /embedding/warmup` + 轮询 `GET /embedding/status` 进度条） |
| 检索 | top_k、score_threshold、**rerank_enabled**、**rerank_model**、**rerank_candidates**、**rerank_top_n**（0=同 top_k；精排默认关；开启后阈值作用于 sigmoid(logit)） |
| 分块 | chunk_size、chunk_overlap |
| 解析 / OCR | pdf_use_ocr、pdf_force_ocr、pdf_ocr_language |
| 代理 | url、no_proxy、**enabled**（可写；关闭后仍保留 URL，出站直连） |
| 服务 / 上传 | host、port、max_upload_mb、debug、**log_level**（DEBUG/INFO/WARNING/ERROR，立即生效） |

密钥：读时脱敏；写时若仍为掩码则跳过该字段。保存后展示后端返回的 `settings_effects`。只读展示：`storage.knowledge_dir`、`meta.config_path`、`meta.env_writable`。

学院/课程**目录管理**不在本阶段设置页内（选课在顶栏）；后续若要做可另开分组。

---

## 6. 复用规则

| 抽象 | 位置 | 说明 |
|---|---|---|
| `api.get/post/patch/delete` | `shared/js/api.js` | 解析 `{code,data}`，失败抛可读错误 |
| 主题 | `shared/js/theme.js` | 默认 `system`；监听系统变化（仅在 system 模式） |
| 壳顶栏 | `shared/js/shell.js` | 两页共用同一套 DOM 约定 / 渲染函数 |
| KaTeX | `shared/lib/katex` + workbench 内调用 | 仅回答区 |

文案 P0 中文硬编码。拒答文案与后端 `grounded: false` 固定语一致。

---

## 7. 样式系统

### 7.1 Tokens（`www/shared/css/tokens.css`）

在 `:root` / `:root[data-theme="light"]` / `:root[data-theme="dark"]` 定义：

- `--sz-bg-base`、`--sz-bg-panel`、`--sz-bg-surface`
- `--sz-text`、`--sz-text-muted`、`--sz-border`
- `--sz-accent`、`--sz-on-accent`、`--sz-success`、`--sz-warning`、`--sz-danger`
- `--sz-radius-sm|md|lg`

亮色：浅底、白面板；暗色：深底、抬升面板。禁止业务 CSS 写死 hex，一律 `var(--sz-*)`。具体色值见 §1.2。

### 7.2 滚动

| 区域 | 行为 |
|---|---|
| 对话 · 会话列表 | 独立 `overflow-y: auto` |
| 对话 · 回答区 | 独立滚动；输入区 sticky/固定底 |
| 资料 · 文档列表 | 独立 `overflow-y: auto`；上传区不随列表滚走 |
| 设置 · 分组列表 | 独立滚动 |
| 设置 · 表单主区 | 独立滚动 |
| `html/body` | 桌面避免整页双滚动条 |

### 7.3 响应式与无障碍

- `≤768px`：对话列改行；设置双栏改堆叠
- `:focus-visible` 可见焦点环
- Enter 提交问题（Shift+Enter 换行，若采用 textarea）
- `prefers-reduced-motion: reduce` 关闭非必要动画

---

## 8. 实施计划

| 阶段 | 目标 | 完成标准 |
|---|---|---|
| **W0** | `main.py` 双 mount + `/`→`/sz/`；空壳 `sz`/`sz-cfg`/`shared` | 打开 `/sz/`、`/sz-cfg/` 有顶栏与双主题 |
| **W1** | 工作台：课程选择 + documents + ask SSE + KaTeX + citations | 上传→提问→引用/拒答可用 |
| **W2** | 设置：全部分组可读写 PATCH | 改 LLM/检索等写入 `.env` 并见 effects |
| **W3** | 小屏布局、reduced-motion、空状态/错误态打磨 | 人工回归清单通过 |

### 验证清单

```
- [x] GET / → 302/重定向到 /sz/
- [x] /sz/ · /sz-docs/ · /sz-cfg/ 顶栏互链；主题跟随系统且可覆盖
- [x] 对话页：新对话 / 历史切换 / 删除；`mode=auto|qa|concept|chapter`；问答 SSE + citations + KaTeX
- [x] 资料页：选课 → 上传（含 PPTX）→ 列表 → 扫描；「强制重建」带确认并传 force
- [x] grounded: false 拒答态（样式提示即可，无「有据可查」徽章）
- [x] /sz-cfg/ 各组保存成功（含精排开关）；密钥不回明文；env 不可写时有提示
- [ ] ≤768px 无横向撑破、分区仍可独立滚动（人工回归）
```

### 风险

| 风险 | 缓解 |
|---|---|
| StaticFiles 双 mount 与 API 路由顺序 | 先注册 API，再 mount 静态；避免 `mount("/"` 吞掉 `/api` |
| 共享资源路径 | HTML 可用 `/shared/...` 或 `../shared/...`（浏览器解析到 `/shared`）；后端须 mount `/shared` |
| 旧 `www/` 根文件残留 | 只保留 `shared`/`sz`/`sz-cfg`，勿再放根级 `index.html` 误挂 |

---

## 附录 A：与历史方案差异

| 旧（已废） | 新 |
|---|---|
| React 18 + Vite 7 + `workbench/` → `www/` | 手写静态，无构建 |
| 单挂载 `/` → 整棵 `www/` | `/sz` + `/sz-cfg` 两个挂载点 |
| Ant Design / shadcn | 无 UI 库，`sz-*` CSS |
| 设置只读或次要 | 设置为一等页面，全量可写配置 |
| 课程/主题等未统一命名 | `sz` 前缀贯穿 URL、目录、CSS、localStorage |

## 附录 B：资料目录

```
data/knowledge/    # 入库资料；扫描与上传指向此处
```

环境变量见 `src/config.py`（`KNOWLEDGE_DIR` 等）。配置持久化仍写项目根 `.env`。

---

*文档版本：2.0 · 手写双挂载定稿 · 2026-07*
