## Context

诊断和已审批 Docker 验证已经可以独立运行。委派负责把两者的证据和结果连接起来。

## Decisions

- 诊断保存观察结果后，按当前企业、配置、可验证的阶段和可用模板决定是否调用独立 Verification Agent。交接只包含 Trace 引用、数值和允许的模板。
- Verification Agent 在独立 LangGraph 子流程中选择固定模板。使用异步 SQLite Checkpointer 和稳定的 `thread_id`，在等待审批及结果时中断。实际批准、拒绝和 Docker 执行沿用持久计划。
- 执行结束、计划被拒绝、取消或过期时，SQLite 在持久状态变更中注册结果回传任务。后端 Worker 恢复思考流程，Docker 执行器不需要访问模型。
- 恢复后模型用结构化输出解释数值，策略验证解释与实测相符。报告保存结论、决策理由、实验适用范围和原证据 ID。模型不可用时保留确定性的测量与失败信息。

## Risks / Trade-offs

测试模拟和模型均受本地配置控制。固定模板负载产生的结果只适用于相同测试条件。LangGraph 的 checkpoint 文件需与应用数据库一起保存。
