# Third-party notices

以下第三方静态资源随仓库分发，其版权与许可证由各自作者保留。

## KaTeX

KaTeX 由 `frontend/package.json` 声明，经 pnpm 安装并由 Vite 打包。版本以 `frontend/pnpm-lock.yaml` 为准。

项目：[KaTeX](https://github.com/KaTeX/KaTeX)。

Copyright (c) 2013-2020 Khan Academy and other contributors.

MIT 许可证完整文本随 pnpm 安装的 KaTeX 包提供。

数学字体由 KaTeX 包提供，其内嵌版权记录为 Design Science, Inc.（2009-2010）和 Khan Academy（2014-2018），许可证为 SIL Open Font License 1.1。

## LXGW WenKai 1.311

文件：`frontend/public/fonts/lxgw-wenkai-500.woff2`、`lxgw-wenkai-700.woff2`。

内嵌版本：1.311（2023-10-06）。Copyright 2021-2023 LXGW；Copyright 2020 The Klee Project Authors。

项目：[LXGW WenKai](https://github.com/lxgw/LxgwWenKai/tree/v1.311)。SIL Open Font License 1.1 完整文本：[LXGW-WenKai-OFL.txt](frontend/public/fonts/LXGW-WenKai-OFL.txt)，取自对应版本。保留名称以许可证为准。

## Nunito 3.602

文件：`frontend/public/fonts/nunito-500.woff2`、`nunito-600.woff2`、`nunito-700.woff2`。

内嵌版本：3.602。Copyright 2014 The Nunito Project Authors。

项目：[Nunito](https://github.com/googlefonts/nunito)。SIL Open Font License 1.1 完整文本：[Nunito-OFL.txt](frontend/public/fonts/Nunito-OFL.txt)，来自 [Google Fonts 的 Nunito 目录](https://github.com/google/fonts/tree/main/ofl/nunito)。

## 安装依赖与模型

Python 依赖通过 `pyproject.toml` / `uv.lock` 声明和安装，不将本地虚拟环境复制到仓库。各依赖仍适用其各自许可证。

本仓库不分发下载的模型权重、用户资料、API 凭据或运行数据库。使用者自行下载模型或接入远程服务时，应查看相应模型及服务的使用条件。
