## Why

前端读取 API 地址时导入了两份完整的项目配置。应在构建和开发服务器的 Node 端提取浏览器需要的公开值，避免让浏览器读取服务端配置。

## What Changes

- 在 Vite 的 Node 端读取基础配置和用户覆盖，只向前端注入 API 地址。
- 开发服务器明确拒绝读取两份运行时项目配置。
- 使用临时配置验证浏览器模块、开发服务器和构建资源的凭据边界。

## Capabilities

### New Capabilities

### Modified Capabilities

- `shared-user-project-configuration`　限定浏览器可接收的项目配置字段。

## Impact

涉及 Vite 配置、前端 API 地址初始化、运维文档和前端回归测试。原有项目 JSON 配置路径和覆盖优先级继续使用。
