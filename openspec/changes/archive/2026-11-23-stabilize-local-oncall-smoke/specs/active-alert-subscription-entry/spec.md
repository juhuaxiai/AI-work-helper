## ADDED Requirements

### Requirement: 空地址告警来源可停用

后端 SHALL 把配置中地址为空或仅有空白的告警来源视为停用，继续查询已配置的真实来源。存在配置有效且返回成功的来源时，后端 MUST 返回这些来源的规范化结果。

#### Scenario: 外部来源停用且本地 Alertmanager 可用
- **WHEN** 外部 Prometheus 来源的 `alertsApi` 留空，且本地 Alertmanager 来源返回带测试标签的告警
- **THEN** 活跃告警接口 MUST 返回该告警及其来源、标签和上下文。

#### Scenario: 所有告警来源停用
- **WHEN** 所有声明的告警来源的 `alertsApi` 都为空或仅有空白
- **THEN** 活跃告警接口 MUST 返回现有的服务不可用错误。
