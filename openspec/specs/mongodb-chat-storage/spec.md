# mongodb-chat-storage Specification

## Purpose

规定 MongoDB 聊天存储的配置、可靠性、权限和 SQLite 会话迁移行为。

## Requirements

### Requirement: Configured MongoDB chat persistence
系统 SHALL 支持从本地项目 JSON 选择 MongoDB 保存聊天会话、消息、引用、会话摘要与聊天工具审计，同时支持 SQLite 默认模式。

#### Scenario: MongoDB 启用后读写聊天
- **WHEN** 使用已配置的 MongoDB 副本集或分片集群创建会话并完成对话
- **THEN** 服务重启后原所有者 SHALL 可以读取原消息、记忆、引用和审计，其他成员或未经授权的组织上下文 SHALL 无法访问

#### Scenario: 聊天存储不可用
- **WHEN** 启用 MongoDB 且配置的连接或事务拓扑不可用
- **THEN** 就绪检查 SHALL 报告依赖不可用，聊天写入 SHALL 不切换到其他数据库

### Requirement: Repeatable SQLite chat import
迁移工具 SHALL 保留 SQLite 会话、消息和聊天工具审计的原始 ID、范围、顺序与记忆状态。

#### Scenario: 重试迁移
- **WHEN** 迁移已经完成或中途中断并再次运行
- **THEN** 工具 SHALL 只导入未完成会话，保留已迁移会话的新内容或删除状态，SQLite 原始记录 SHALL 保留
