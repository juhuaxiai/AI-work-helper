# sandbox-log-statistics Specification

## Purpose

定义脱敏日志快照经隔离 Worker 执行离线统计、验证权限和回写诊断证据的行为。

## Requirements

### Requirement: Fixed read-only sandbox statistics for local logs
系统 SHALL 允许诊断所有者对当前未过期的已脱敏快照显式发起离线统计。

#### Scenario: 沙箱产生结构化统计
- **WHEN** 有权限的成员为诊断快照发起统计
- **THEN** 隔离 Worker MUST 用固定镜像、预设模板、只读输入及无网络容器执行，并将结构化结果关联该任务与报告

#### Scenario: 重复、取消及失效
- **WHEN** 请求重复、执行被取消或输入失效
- **THEN** 重复请求 MUST 使用同一任务；被取消或失效的执行 MUST 不写入成功结果

#### Scenario: 不具备权限
- **WHEN** 普通部门成员、其他企业或其他所有者尝试发起统计或读取任务
- **THEN** 服务 MUST 拒绝且不启动执行器
