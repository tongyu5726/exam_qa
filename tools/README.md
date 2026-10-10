# 开发工具

所有命令在项目根目录执行。产品启动仍用 `uv run --no-sync exam`，这些工具不参与服务启动。

| 目录 | 用途 |
| --- | --- |
| `diagnostics/` | 手动探测外部模型能力 |
| `preview/` | 隔离存储、固定样例的浏览器验收服务 |

## 隔离预览

```powershell
uv run --no-sync python -m tools.preview.serve_practice_preview --port 8876
uv run --no-sync python -m tools.preview.serve_agent_preview --port 8877
```

预览使用临时数据库、固定资料和模型桩，无真实资料写入及外部模型请求。仅用于开发验收。

## 模型工具调用探测

```powershell
uv run --no-sync python -m tools.diagnostics.check_function_calling
```

显式运行会读取项目 `.env` 并请求所配置的 LLM，可能产生服务商费用；仅验证是否返回工具调用，不执行检索工具。

离线检索与问答评估保留在 `tests/eval/`，固定资料在 `tests/fixtures/`，历史报告及截图归档到 `docs/artifacts/quality/`。运行方式及结果解释见 [练习与问答质量](../docs/08-练习与问答质量.md)。

一次性对照脚本与源码快照放到系统临时目录，完成核验后清理；不要在仓库根目录堆积 `tasks/` 或提交构建产物、缓存、资料库。
