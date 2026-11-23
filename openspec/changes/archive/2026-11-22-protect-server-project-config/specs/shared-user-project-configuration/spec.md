## ADDED Requirements

### Requirement: Browser receives only public project configuration
Vite SHALL 仅将公开的 `frontend.apiBaseUrl` 传到浏览器模块和构建产物，并保留用户配置覆盖基础配置的优先级。

#### Scenario: Browser uses the configured API address
- **WHEN** 基础项目配置提供 API 地址，用户项目配置可以覆写它
- **THEN** 前端 MUST 采用有效配置中的 API 地址，并继续支持基础默认值。

#### Scenario: Private project files are unavailable to the browser
- **WHEN** 浏览器请求开发服务器中的运行时项目 JSON，或检查公开的脚本和构建产物
- **THEN** 开发服务器 MUST 拒绝提供完整运行时 JSON，浏览器可访问的资源 MUST NOT 包含模型、云服务或演示账号凭据。
