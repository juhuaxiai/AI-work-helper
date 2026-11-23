## Why

本地诊断可以看到 TTS 的测量数据。操作员需要在明确审批后，用受限容器运行可复现的对照实验，并保留实验结果与来源。

## What Changes

- 由本部门从本地测试诊断申请预设验证，持久化审批、版本、到期、取消和执行编号。
- 独立 Docker Worker 只处理已批准的固定模板，在无外部网络的容器内运行测试对照。
- 结果写入原诊断证据链并在工作台显示，支持后台中断后的状态恢复。

## Capabilities

### New Capabilities

- `approved-sandbox-verification`　验证审批、隔离执行和可追溯结果。

### Modified Capabilities

- `background-job-runtime`　分离 API 与 Docker Worker 可领取的任务类型。

## Impact

涉及 SQLite、独立验证 Worker、Docker 镜像、审批 API、工作台和测试。
