## ADDED Requirements

### Requirement: Workers claim only their registered job kinds
后台 Worker SHALL 按进程已注册的任务类型领取和恢复任务。

#### Scenario: Sandbox executor runs separately
- **WHEN** 通用服务和验证执行器同时运行
- **THEN** 通用服务 MUST 不领取验证任务；验证 Worker MUST 不领取普通诊断或索引。
