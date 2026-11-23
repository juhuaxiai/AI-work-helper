## Context

浏览器入口目前静态导入了 `project.json` 和 `user.project.json`。Vite 在本机运行时也能直接服务允许范围内的文件。

## Goals / Non-Goals

保留既有 API 地址设置和用户覆盖规则，将两份项目 JSON 留在 Node 和 Python 进程内。

## Decisions

- Vite 配置读取本机 JSON 的公开 `frontend.apiBaseUrl` 字段，通过编译常量传给浏览器。基础配置必需，用户配置可选。配置修改后重启 Vite 或重新构建。
- Vite 开发服务器的文件访问规则覆盖两份本地 JSON，保留默认的敏感文件阻止规则。
- 用合成配置启动临时 Vite 服务器并构建入口。检查 API 地址可用、开发访问被拒绝、构建资产无测试密钥。

## Risks / Trade-offs

浏览器中的 API 地址属于公开配置。应由服务端配置保存的凭据有独立边界。
