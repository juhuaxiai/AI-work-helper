## Why

本地工作台需要演示同一企业内两种权限，以及跨企业的隔离。现有 bearer 登录验证用户身份，但所有已登录用户都能请求诊断。

## What Changes

- 用本机配置将已存在的本地账号 ID 绑定到飞书风格的企业、成员和部门标识。
- 按本部门身份过滤告警和诊断入口，并在工作流执行前确认权限。其他账号仍可访问普通对话。
- 记录诊断任务所属的企业，防止账号更换组织后继续访问原诊断记录。

## Capabilities

### New Capabilities

### Modified Capabilities

- `authorization-and-tenant-isolation`　为诊断流程增加本地企业和部门检查。

## Impact

更新后端授权、诊断 API 和任务执行、认证用户返回字段、前端诊断入口及运维文档。
