## Why

诊断图可从观测数据判断慢在哪个阶段。需要把待验证的假设交给独立 Agent 选择对照实验，并在审批后解读实际输出。

## What Changes

- 诊断根据可信的测试证据和配置进入验证委派，保存选择或跳过原因。
- Verification Agent 选择固定实验模板并通过持久 LangGraph checkpoint 等待结果。
- 已审批沙箱输出由 Verification Agent 解读，为原诊断补充实验结论和适用范围。

## Capabilities

### New Capabilities

- `verification-agent-delegation`　条件委派、持久图恢复和验证结论。

### Modified Capabilities

- `stream-rag-chat`　获授权的成员可把问题带到诊断草稿，提交由成员确认。

## Impact

涉及诊断后台编排、独立验证 Agent、审批结果回传、报告以及 SQLite checkpoint。
