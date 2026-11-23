## Why

当前诊断证据只保存少量有界样本。值班成员需要在已授权的诊断范围内获得较大的脱敏日志样本，并跟踪文件用途、读取和有效期。

## What Changes

- 从真实本地 TTS 观测源为已完成诊断创建限时 JSONL 导出。
- 提供受保护的诊断导出、下载入口与工具审计。

## Capabilities

### New Capabilities

- `local-diagnostic-log-exports`: 有界脱敏导出及访问审计。

### Modified Capabilities

无。

## Impact

本地 TTS 文件读取、诊断数据、后台鉴权、共享 API 契约和操作文档。
