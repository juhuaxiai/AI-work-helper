# local-diagnostic-log-exports Specification

## Purpose

定义本地诊断日志导出的数据范围、下载鉴权、有效期、留存上限和审计行为。

## Requirements

### Requirement: Scoped and bounded TTS log snapshots
系统 SHALL 允许具备本部门权限的诊断所有者导出该任务的脱敏本地 TTS 日志。

#### Scenario: 导出有界日志
- **WHEN** 诊断完成并生成带范围的本地证据
- **THEN** 导出 MUST 只包含该证据范围内的允许字段，最多 500 条记录及 128 KiB，并返回大小、计数、范围、有效期和受保护的下载引用

#### Scenario: 下载过期及越权引用
- **WHEN** 下载文件引用已过期、用户不同或用户失去组织权限
- **THEN** 系统 MUST 拒绝访问；拒绝结果与文件内容 MUST 不泄漏给其他组织

#### Scenario: 审计创建与读取
- **WHEN** 授权主体完成导出或读取
- **THEN** 系统 MUST 保存任务关联的审计、文件编号和读取结果，审计中 MUST 不包含文件正文
