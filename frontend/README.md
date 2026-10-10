# 溯知 Vue 前端

Vue 3、Vite、Vue Router Hash、Naive UI、Pinia 和 JavaScript。应用保留 `/api/v1/*` 后端契约与现有青绿设计令牌。

## 开发

在仓库根目录先运行 `uv run --no-sync exam`（会按需构建 Vue），再在另一个终端运行：

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

访问 `http://127.0.0.1:5173/sz/`。Vite 将 `/api/v1` 转发到本地 FastAPI `127.0.0.1:8787`。字体位于 `public/fonts/`，不依赖旧页面目录。

## 验证与交付

```bash
pnpm lint
pnpm test
pnpm build
```

构建输出位于仓库根目录 `www-dist/`，并已加入 `.gitignore`。FastAPI 启动时检查源码和产物时间；缺少产物或源码较新时，按锁文件安装缺失依赖并运行构建。发布包可以预先包含 `www-dist/`，也可以在有 Node.js 20+ 与 pnpm 的目标机器上启动时构建。新应用路由为 `/sz/#/chat`、`/sz/#/documents`、`/sz/#/question-bank`、`/sz/#/settings`；旧 `/sz-docs/`、`/sz-bank/`、`/sz-cfg/` 会跳转到对应路由，题库 `?topic=` 保留。

目录按职责划分：`src/features/` 放对话、资料、题库、练习和设置页面及各自组件，`src/api/` 负责 HTTP/SSE，`src/stores/` 管理课程、会话与主题，`src/layouts/` 是共享外壳，`src/router/` 定义页面路由，`src/styles/` 保存全局样式。聊天页进一步拆为消息流、输入区与会话侧栏，`ChatPage.vue` 编排状态和请求。练习入口为 `/sz/#/practice`，交互和证据契约见 [练习与问答质量](../docs/08-练习与问答质量.md)。

旧版 `sz.*` 本地存储键继续读取。课程切换时，页面取消旧课程的未完成请求并清空旧课程显示的数据。上传进度只表示文件传输，服务端解析完成后才显示成功。资料操作需要后端可用的解析器、向量模型及相应配置。

## 界面约定

使用现有青绿设计令牌、自托管字体和 Naive UI 主题；保留浅色、深色及跟随系统三态。动态效果尊重 `prefers-reduced-motion`，保证键盘操作和小屏可用。问答保留 `phase/delta/done/error` 事件、固定拒答、来源入口，以及出题请求跳转题库并传递 `topic`。前端只负责展示和交互，不实现 RAG 判定。

当前代码规模与精简任务统一见 [代码规模与优化计划](../docs/06-代码规模与优化计划.md)，旧迁移和组件候选方案已清理。
