---
name: exam-config-update
description: 更新 exam 配置项、配置保存接口、模型注册表或设置页时使用，核对字段映射、校验、保存与热更新契约。
---

# 更新配置

先读 AGENTS.md 与 docs/03-工程规范.md，用 CodeGraph 查字段从请求到保存、运行时读取的路径。

- 核对 src/config.py 的 dataclass、src/models.py 的请求、src/apis/v1/config.py 的映射与响应。
- 公开默认值同步 .env.example；设置页核对 frontend/src/features/settings/fields.js 及对应表单。
- 模型配置检查 src/services/llm_providers.py，保持注册表和密钥掩码契约。

所有字段及端口先校验再保存，混合合法/非法请求不得部分写入。
保留 False、0、空值的区别，不用真值判断替代字段存在性判断。
保持环境变量 > .env > 默认值，沿用热更新与重启提示。
验证使用临时配置与模拟注册表，不改真实 .env、密钥或已安装的 GPU 环境。
优先运行 tests/test_config_updates.py、test_port_selection.py 及受影响 API 测试，流程见 ../exam-regression/SKILL.md。
修改设置页再跑前端检查，同步用户可见说明，不新增重复配置清单。
