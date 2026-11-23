## Why

工作台可以主动查询当前告警。排队延迟发生时，需要由 Alertmanager 通知后端并保存事件，便于工作台提醒、查询历史和按规则发起诊断。

## What Changes

- 接收受认证的 Alertmanager 通知，按来源、指纹和发生周期去重，更新解除状态。
- 为授权企业的本部门显示告警记录，可访问自己的自动诊断。
- 提供 TTS 实测指标、Prometheus 规则和本地告警接线示例。

## Capabilities

### New Capabilities

- `alertmanager-notifications`　定义告警接收、持久化、提醒和有条件自动诊断。

### Modified Capabilities

## Impact

涉及 SQLite、后台任务、AI 工作台、指标端点和本地配置。
