## REMOVED Requirements

### Requirement: Chat Agent can use discovered MCP tools

**Reason**: 普通聊天工具由后端白名单决定。观测工具由诊断工作流调用。

**Migration**: 通过智能诊断入口使用已授权的真实 MCP 工具。

## ADDED Requirements

### Requirement: Ordinary chat tools and knowledge are scoped
普通聊天 Agent SHALL 只使用知识检索、时间和已选 Skill 工具。后端 SHALL 在两路召回和进入模型前校验知识范围。

#### Scenario: Two departments use ordinary chat
- **WHEN** 同一企业的两类成员通过普通聊天查询知识
- **THEN** Agent MUST 能检索该企业共享库和个人资料，MUST NOT 加载观测 MCP 服务或索引中的诊断案例。
