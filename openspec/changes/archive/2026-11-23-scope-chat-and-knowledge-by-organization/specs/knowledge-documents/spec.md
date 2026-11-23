## ADDED Requirements

### Requirement: Organization document scopes
后端 SHALL 通过服务端目录限定企业共享库、运维 SOP 和个人库访问范围。

#### Scenario: Member reads organization knowledge
- **WHEN** 当前账号访问企业共享或运维文档和索引任务
- **THEN** 本企业成员 MUST 可读取共享知识，仅本部门 MUST 能读取运维知识和维护两类企业文档。其他企业 MUST 收到授权错误。

#### Scenario: Diagnostic artifacts do not enter ordinary knowledge
- **WHEN** 诊断生成案例或原个人库已包含诊断案例
- **THEN** 普通知识检索和普通个人库入口 MUST 排除诊断案例。
