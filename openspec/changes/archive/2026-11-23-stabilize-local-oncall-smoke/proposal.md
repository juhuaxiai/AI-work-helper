## Why

本地验证中，空的 Prometheus 地址会阻断可用的 Alertmanager。缺少 CLS 地域或日志主题时，后台任务已经失败，诊断却停留在等待状态。先稳定这条本地链路，后续的权限和双 Agent 改造才有可重复验证的基线。

## What Changes

- 跳过明确留空的告警来源，继续从已配置的真实 API 读取告警。
- 诊断图因 CLS 配置缺失无法启动时，保存失败终态和安全提示，通过现有 SSE 和诊断详情展示原因。
- 复用本地测试告警和 SOP 脚本，补充无需腾讯云 CLS 的验证步骤和回归测试。

## Capabilities

### New Capabilities

### Modified Capabilities

- `active-alert-subscription-entry`　定义空地址来源的跳过规则。
- `aiops-diagnosis-tasks`　定义诊断图启动失败时的终态、持久化错误和流式提示。
- `aiops-diagnosis-ui`　定义缺少日志目标时的中文提示与历史详情行为。

## Impact

涉及后端告警来源配置、诊断任务处理、前端诊断工作区及本地验收文档。沿用 SQLite 和现有 HTTP/SSE 契约。
