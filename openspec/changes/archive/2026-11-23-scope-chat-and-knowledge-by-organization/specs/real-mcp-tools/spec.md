## MODIFIED Requirements

### Requirement: Real MCP SSE tools
诊断流程 SHALL 通过当前用户启用的受管理 MCP 连接接入真实 Server，并 SHALL 在授权用户未配置连接时回退到项目 CLS SSE 配置。

#### Scenario: Agent loads managed tools
- **WHEN** 授权用户启动 AIOps 诊断
- **THEN** Agent MUST 只加载该用户启用连接中真实发现的工具。

### Requirement: MCP calls are audited
每个 MCP 工具调用 SHALL 都会使用现有的工具调用审计生命周期，包括工具名称、参数、结果摘要、持续时间、状态和错误信息。

#### Scenario: MCP call completes
- **WHEN** 诊断 Agent 调用发现的 MCP 工具
- **THEN** 相应的工具审计 MUST 完成或失败，并保存真实 MCP 结果的摘要。
