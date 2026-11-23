## Why

正式部署需要独立扩展聊天存储，并保留已有会话、摘要、引用与所有者。长会话需要按页读取历史。

## What Changes

- 提供可配置的 MongoDB 聊天仓库和 SQLite 到 MongoDB 的可重试迁移。
- 保留当前已认证的企业与所有者鉴权，新增历史游标和前端加载入口。
- 保持身份、诊断、审批和后台任务的现有 SQLite 存储。

## Capabilities

### New Capabilities

- `mongodb-chat-storage`: MongoDB 聊天仓库、迁移和存储就绪检查。

### Modified Capabilities

- `chat-sessions`: 已认证的历史分页和配置选定的聊天持久化仓库。

## Impact

涉及聊天仓库、审计、应用配置、前后端 HTTP 契约、聊天页面和运维文档。
