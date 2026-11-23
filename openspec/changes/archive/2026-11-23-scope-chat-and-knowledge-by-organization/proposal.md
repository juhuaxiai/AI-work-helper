## Why

部门权限需要延伸到知识检索、聊天会话和对话工具。组织变更后，原组织的会话、知识与诊断资料也需要受到保护。

## What Changes

- 新会话记录服务端解析的企业，历史个人会话保留原归属。
- 企业共享知识由本企业成员读取，运维 SOP 仅允许本部门读取和管理。个人文档继续由本人管理。
- 普通会话使用固定工具清单，诊断工具只在授权流程中调用。

## Capabilities

### New Capabilities

### Modified Capabilities

- `chat-sessions`　会话按组织和所有者校验。
- `knowledge-documents`　知识库、文档和索引采用一致的授权范围。
- `stream-rag-chat`　限制普通聊天工具和检索范围。
- `mcp-connection-management`　将连接管理和装配限定在获授权的诊断工作区。
- `real-mcp-tools`　将观测工具的发现、调用和审计限定在诊断流程。

## Impact

涉及聊天存储迁移、RAG、Milvus 作用域、文档 API、任务执行和工作台知识库。
