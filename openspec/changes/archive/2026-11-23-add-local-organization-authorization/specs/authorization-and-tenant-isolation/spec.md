## ADDED Requirements

### Requirement: OnCall organization membership is server resolved
系统 SHALL 根据已认证账号与服务端本地目录的绑定解析企业、成员和部门身份，并仅向指定部门开放故障诊断。

#### Scenario: Department determines access
- **WHEN** 本部门成员、其他部门成员或未绑定用户登录
- **THEN** 三者 MUST 可使用普通对话，只有本部门成员 MUST 能查看指定的告警并发起诊断。

#### Scenario: Diagnosis stays in its original tenant
- **WHEN** 已登录成员请求诊断、证据、案例、任务或事件
- **THEN** 后端 MUST 检查当前本部门权限、原企业和资源所有者；告警 MUST 按被授权的来源过滤。

#### Scenario: Worker rechecks a queued operation
- **WHEN** 原发起人在任务执行前或步骤之间失去诊断权限
- **THEN** 后台任务 MUST 停止后续敏感操作，并持久化失败终态。
