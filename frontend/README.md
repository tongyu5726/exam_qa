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

目录按职责划分：`src/features/` 放四个页面及各自组件，`src/api/` 负责 HTTP/SSE，`src/stores/` 管理课程、会话与主题，`src/layouts/` 是共享外壳，`src/router/` 定义页面路由，`src/styles/` 保存全局样式。聊天页进一步拆为消息流、输入区与会话侧栏，`ChatPage.vue` 只编排状态和请求。

旧版 `sz.*` 本地存储键继续读取。课程切换时，页面取消旧课程的未完成请求并清空旧课程显示的数据。上传进度只表示文件传输，服务端解析完成后才显示成功。资料操作需要后端可用的解析器、向量模型及相应配置。
