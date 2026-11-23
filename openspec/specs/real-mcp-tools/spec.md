# real-mcp-tools Specification

## Purpose

为本地官方 MCP 服务器定义实际的 MCP 集成边界，包括发现、调用、故障处理、readiness 和审计行为。

## Requirements

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

### Requirement: Real MCP tools are available to AIOps execution
诊断 Executor SHALL 使用发现的本地 MCP 工具定义，根据诊断计划调用实际的 MCP 工具。

#### Scenario: MCP execution failure is surfaced
- **WHEN** 一个 AIOps MCP 工具连接或调用在配置的重试后失败
- **THEN** 诊断流、审计、持久化结果和最终任务状态 MUST 记录明确的失败，而不捏造证据。

### Requirement: MCP server credentials use merged project configuration
本地 CLS MCP Server 启动和后端 MCP 配置检查 SHALL 从 merged 项目配置读取 CLS 凭据和连接参数。

#### Scenario: Base config leaves personal MCP credentials empty
- **WHEN** 检查 `config/project.json`
- **THEN** `clsMcpServer.secretId` 和 `clsMcpServer.secretKey` MUST 为空字符串，并由用户配置文件覆盖。

#### Scenario: Local startup receives merged MCP credentials
- **WHEN** 使用本地启动脚本启动 CLS MCP Server
- **THEN** 脚本 MUST 从 merged 项目配置导出 `TENCENTCLOUD_SECRET_ID`、`TENCENTCLOUD_SECRET_KEY`、`TRANSPORT`、`PORT` 和 `TZ`。

### Requirement: MCP attempt deadlines include negotiation
使用 SSE 或 Streamable HTTP 的 MCP 每次连接及操作 SHALL 遵守配置中的整体尝试超时。

#### Scenario: Handshake stalls after connecting
- **WHEN** MCP 传输已连接但握手或操作没有在配置时限内完成
- **THEN** 尝试 MUST 及时取消并遵守配置的重试次数，耗尽后按既有错误契约报告失败
