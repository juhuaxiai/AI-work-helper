## ADDED Requirements

### Requirement: Local TTS workload produces scoped observations
本地演示服务 SHALL 记录自身执行的测试请求，保留 Trace ID、时间和各阶段实测耗时。

#### Scenario: Two latency faults produce different observations
- **WHEN** 操作员对 TTS 演示服务分别触发排队延迟和合成延迟
- **THEN** 本地 JSONL MUST 记录对应阶段的测量值、Trace ID 和测试环境。

#### Scenario: Source access is scoped
- **WHEN** 诊断请求指定其他企业、其他服务或其他环境
- **THEN** 观测源 MUST 不返回数据。记录查询 MUST 按指定 Trace 和后端时间范围过滤。
