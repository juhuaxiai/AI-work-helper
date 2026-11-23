## ADDED Requirements

### Requirement: Diagnosis conditionally delegates valid hypotheses
诊断 SHALL 根据授权测试证据、配置和有限模板决定是否委派给独立 Verification Agent，并保留决策理由。

#### Scenario: Evidence does not justify a fixed experiment
- **WHEN** 诊断已完成但未找到符合范围的异常阶段或合法模板
- **THEN** 工作流 MUST 生成有限诊断报告并保存跳过原因。

#### Scenario: Verification Agent selects a supported experiment
- **WHEN** 诊断交接了可验证的测试假设
- **THEN** Verification Agent MUST 选择获准模板或明确跳过；模板通过政策校验后才可生成审批计划。

### Requirement: Verification uses a durable interrupt and constrained interpretation
Verification Agent SHALL 在等待批准和实验结果时持久保存 LangGraph 中断状态，在审批终态恢复同一线程。

#### Scenario: Restart and approve after delegation
- **WHEN** 诊断委派后进程重启，审批随后通过并且沙箱返回数值
- **THEN** 系统 MUST 恢复原 LangGraph 线程、引用实际实验并更新原报告。

#### Scenario: Rejected or non-reproduced hypothesis
- **WHEN** 审批未通过，或实验未提供支持假设的证据
- **THEN** 报告 MUST 明确标记原因、未复现或证据不足。
