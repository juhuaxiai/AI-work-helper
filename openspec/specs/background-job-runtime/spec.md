# background-job-runtime Specification

## Purpose
TBD - created by archiving change durable-background-job-runtime. Update Purpose after archive.

## Requirements

### Requirement: Durable owner-scoped background jobs
系统 SHALL 在 SQLite 中持久化后台任务、执行尝试、租约、取消请求、失败原因和终态，并通过 Repository 边界访问。

#### Scenario: 服务在任务执行期间重启
- **WHEN** running 任务的租约过期且服务重新启动
- **THEN** Runtime MUST 将任务重新置为可领取状态，并 MUST 保留原 attempt 信息。

#### Scenario: 其他用户查询任务
- **WHEN** 用户查询不属于自己的后台任务
- **THEN** API MUST 返回统一权限错误且不得泄露任务信息。

### Requirement: Registered background job handlers
Runtime SHALL 通过 kind 到 handler 的注册表执行任务，并 SHALL 限制并发、超时和最大尝试次数。

#### Scenario: handler 执行成功
- **WHEN** 注册 handler 正常完成
- **THEN** job MUST 进入 succeeded 并记录开始和完成时间。

#### Scenario: handler 暂时失败
- **WHEN** attempt 小于最大次数
- **THEN** job MUST 使用退避时间重新排队；达到上限后 MUST 进入 failed。

### Requirement: Durable background job events
Runtime SHALL 按 job 和单调递增 sequence 持久化事件，使订阅者可以断点读取。

#### Scenario: SSE 客户端断开
- **WHEN** AIOps SSE 客户端断开但后台任务仍在运行
- **THEN** 任务 MUST 继续，重新订阅 MUST 能读取此前已保存和后续事件。

### Requirement: Background job cancellation and retry
用户 SHALL 能取消自己的 queued/running 任务，并为 failed/cancelled 任务创建重试。

#### Scenario: 取消 running 任务
- **WHEN** 用户请求取消 running 任务
- **THEN** Runtime MUST 记录取消请求并在安全事件边界停止，最终状态 MUST 为 cancelled。

### Requirement: Workers claim only their registered job kinds
后台 Worker SHALL 按进程已注册的任务类型领取和恢复任务。

#### Scenario: Sandbox executor runs separately
- **WHEN** 通用服务和验证执行器同时运行
- **THEN** 通用服务 MUST 不领取验证任务；验证 Worker MUST 不领取普通诊断或索引。

### Requirement: Diagnostic streams drain persisted terminal events
诊断 SSE SHALL 在关闭连接前返回截至终态已经持久化的事件，保留具体的错误码和完成结果。

#### Scenario: Worker finishes between polling reads
- **WHEN** Worker 在诊断 SSE 一次轮询的状态与事件读取之间写入最后一条事件并结束任务
- **THEN** 连接 MUST 将已持久化的最后一条事件重放一次，并保持按顺序关闭
