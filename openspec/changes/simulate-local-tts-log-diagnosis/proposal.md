## Why

当前本机无法访问腾讯云 CLS。需要用本机生成的 TTS 测试日志，验证已有诊断流程能否查询日志并在报告里引用证据。

## What Changes

- 提供可重复运行的本地集成测试，执行一次带排队延迟的 TTS 示例请求，并记录实测等待时长。
- 用回环地址上的 MCP 服务读取这次测试写出的 JSONL，走现有诊断 API、后台任务、SSE 和报告。
- 覆盖没有测试日志的场景，并记录运行和验收方法。

## Capabilities

### New Capabilities

### Modified Capabilities

## Impact

涉及后端集成测试、OpenSpec WIKI 和本地验收文档。演示入口位于自动化测试中，依赖现有 Python 和 MCP SDK。
