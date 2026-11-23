# alertmanager-notifications Specification

## Purpose

TBD - created by archiving change deliver-alertmanager-notifications. Update Purpose after archive.

## Requirements

### Requirement: Alertmanager delivery is authorized and durable
后端 SHALL 仅接受已配置来源的认证通知，并在其企业范围内按告警指纹和发生周期持久化。

#### Scenario: Alerts retry or resolve
- **WHEN** 同一周期的告警被重复推送或解除
- **THEN** 系统 MUST 保留一条通知和最多一个自动诊断，解除更新原记录，迟到的触发通知不能恢复已解除状态。

#### Scenario: Authorization restricts delivery and reads
- **WHEN** 未知来源或无有效凭据的请求尝试推送，或非本部门用户尝试读取
- **THEN** 系统 MUST 拒绝请求；其他企业的本部门 MUST 只能读到自己企业已分配来源的通知。

### Requirement: Workbench displays new local alerts and optional diagnoses
工作台 SHALL 展示经授权的新告警和历史，并支持配置基于本地 TTS 实测指标的监控规则。

#### Scenario: Local TTS measurement exceeds the rule
- **WHEN** 监控规则触发，Alertmanager 成功推送
- **THEN** 刷新后工作台 MUST 可见通知，启用匹配规则且自动账号有效时 MUST 入队一个诊断。
