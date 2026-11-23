## Why

诊断流可能先读到空事件，再读到已结束的任务，导致刚写入的完成或具体错误事件漏发。修复终态与事件读取的时序可保持报告和错误一致。

## What Changes

- 在诊断 SSE 核对终态前读取该次状态观察之后可见的事件。
- 为 MCP 服务的连接、握手与操作设置每次尝试的整体超时。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `background-job-runtime`: 诊断结束时完整重放已持久化终止事件。
- `real-mcp-tools`: 服务握手停滞时按已配置的时间与重试次数结束尝试。

## Impact

诊断 SSE 消费顺序及后台任务回归测试。事件字段保持相同。
