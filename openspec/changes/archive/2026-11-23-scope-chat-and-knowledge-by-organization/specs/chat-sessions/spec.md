## ADDED Requirements

### Requirement: Organization-bound chat sessions
后端 SHALL 为绑定成员的新会话保存企业身份，并将同一个范围应用于会话、消息、工具审计和记忆。

#### Scenario: Member moves to another organization
- **WHEN** 已认证的成员更换企业或取消组织绑定
- **THEN** 原企业的新式会话 MUST 不可列出、读取、修改或删除，已有个人会话仍归同一账号。
