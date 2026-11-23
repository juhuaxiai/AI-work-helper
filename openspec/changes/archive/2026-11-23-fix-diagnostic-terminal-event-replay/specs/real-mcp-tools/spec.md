## ADDED Requirements

### Requirement: MCP attempt deadlines include negotiation
使用 SSE 或 Streamable HTTP 的 MCP 每次连接及操作 SHALL 遵守配置中的整体尝试超时。

#### Scenario: Handshake stalls after connecting
- **WHEN** MCP 传输已连接但握手或操作没有在配置时限内完成
- **THEN** 尝试 MUST 及时取消并遵守配置的重试次数，耗尽后按既有错误契约报告失败
