## Why

本地集成测试已经验证日志可以进入诊断报告。工作台需要一个可运行的本地观测源，供本部门实际演示语音延迟的故障处理。

## What Changes

- 提供 TTS 测试服务，记录实测排队和合成等待时间、Trace ID 及测试环境标记。
- 诊断按组织选择本地文件查询，先读 Trace，再根据阶段耗时查询日志或指标。
- 保存可追溯的观测证据，保留真实 CLS 默认接入和离线报告模式。

## Capabilities

### New Capabilities

- `local-tts-observability`　采集及读取受限的 TTS 测试观测。

### Modified Capabilities

- `aiops-diagnosis-tasks`　支持基于本地观测选择后续诊断步骤。

## Impact

涉及诊断运行器、日志证据、配置模板、工作台手动复现与端到端验证。
