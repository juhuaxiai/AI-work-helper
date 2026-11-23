## MODIFIED Requirements

### Requirement: Authenticated chat session lifecycle
系统 SHALL 允许经过身份验证的 users 从后端 APIs 创建、列出、读取、清除和删除他们自己的聊天会话。

#### Scenario: 创建聊天会话
- **WHEN** 已认证的 user 使用可选标题创建聊天会话
- **THEN** 后端 MUST 在配置选定的仓库中持久化会话，使用当前 user 的 owner ID 和组织上下文，并返回已创建的会话

#### Scenario: List chat sessions
- **WHEN** 已认证的 user 列出聊天会话
- **THEN** 后端 MUST 仅返回该 user 当前可见的会话，并按最近更新的顺序排列

#### Scenario: Read chat session history
- **WHEN** 已认证的 user 读取其其中一个聊天会话
- **THEN** 后端 MUST 按照创建顺序返回会话及请求范围内的持久化消息

#### Scenario: 按游标读取更早历史
- **WHEN** 客户端请求最初一页或使用同一会话返回的 `before` 游标
- **THEN** 后端 MUST 按创建时间和 ID 稳定返回有界历史，客户端可以连续向前翻页且无重复；来自其他会话的游标 MUST 被拒绝

#### Scenario: Clear chat session history
- **WHEN** 已认证的 user 可清除其聊天会话之一
- **THEN** 后端 MUST 在保留会话记录可访问的同时，删除该会话的消息

#### Scenario: Delete chat session
- **WHEN** 已认证的 user 删除其其中一个聊天会话
- **THEN** 后端 MUST 从 user 的可访问会话列表中删除该会话及其消息
