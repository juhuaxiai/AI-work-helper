## ADDED Requirements

### Requirement: Authorized operator confirms chat handoff
工作台 SHALL 为本部门在普通过程中的排查问题提供显式的诊断草稿入口。

#### Scenario: Operator opens the diagnosis draft
- **WHEN** 获授权的成员从对话进入诊断草稿
- **THEN** 界面 MUST 预填故障描述，MUST 在成员提交后才创建诊断任务。
