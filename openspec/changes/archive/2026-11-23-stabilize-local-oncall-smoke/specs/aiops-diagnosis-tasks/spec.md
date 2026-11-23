## MODIFIED Requirements

### Requirement: Plan-Execute-Replan diagnostic graph

后端 SHALL 在依赖配置有效时，通过具有命名 `Planner`、`Executor`、`Replanner` 和 `Report` 节点的 LangGraph 工作流执行经过身份验证的 AIOps 诊断。若诊断图因日志目标配置缺失而无法启动，后端 MUST 保存诊断失败及可对用户公开的原因，并通过现有 SSE 错误事件返回该原因。

#### Scenario: Diagnostic follows the graph lifecycle
- **WHEN** 已认证的用户启动诊断任务，并且诊断图依赖配置有效
- **THEN** 后端 MUST 将任务保存为运行中，并在终端成功或失败前执行 Planner、Executor、Replanner 和 Report 节点。

#### Scenario: Replanner continues only when warranted
- **WHEN** Executor 返回需要其他限定诊断步骤的证据
- **THEN** Replanner MUST 调整计划并返回到 Executor。否则它 MUST 返回到 Report。

#### Scenario: CLS 日志目标缺失时保存失败终态
- **WHEN** 后台诊断使用 CLS 数据源且遇到未配置的 CLS 地域或日志主题
- **THEN** 诊断和后台任务 MUST 都以失败结束。诊断详情 MUST 包含完成时间及安全的中文配置提示。SSE MUST 返回可重放的 `SYSTEM_UNAVAILABLE` 错误事件，且报告和证据列表 MUST 保持真实。
