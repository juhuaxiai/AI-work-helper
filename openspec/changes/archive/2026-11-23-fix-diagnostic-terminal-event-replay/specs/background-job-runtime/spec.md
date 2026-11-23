## ADDED Requirements

### Requirement: Diagnostic streams drain persisted terminal events
诊断 SSE SHALL 在关闭连接前返回截至终态已经持久化的事件，保留具体的错误码和完成结果。

#### Scenario: Worker finishes between polling reads
- **WHEN** Worker 在诊断 SSE 一次轮询的状态与事件读取之间写入最后一条事件并结束任务
- **THEN** 连接 MUST 将已持久化的最后一条事件重放一次，并保持按顺序关闭
