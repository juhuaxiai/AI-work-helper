## ADDED Requirements

### Requirement: Diagnostic graph adapts to local TTS evidence
诊断图 SHALL 先读取本地 Trace，然后根据阶段证据决定有限次数的后续排查，并保存具体来源。

#### Scenario: A dominant phase changes investigation
- **WHEN** 本地 Trace 显示排队或合成阶段明显变慢
- **THEN** 图 MUST 分别查询排队日志或合成指标，并保存可追溯的实测结果。

#### Scenario: Local data are unavailable
- **WHEN** 测试日志缺失、为空或时间范围无命中
- **THEN** 报告 MUST 明确说明证据缺口并完成有限诊断。
