## MODIFIED Requirements

### Requirement: Owner-scoped MCP connection lifecycle
本部门成员 SHALL 能创建、查看、更新、启停和删除自己的诊断 MCP 连接，连接包含名称、transport、URL、timeout 和 retries。

#### Scenario: 用户访问其他用户连接
- **WHEN** 用户读取或修改不属于自己的连接
- **THEN** API MUST 返回统一权限错误。

#### Scenario: 用户提交不安全 URL
- **WHEN** URL 不是 http/https 或包含 userinfo
- **THEN** API MUST 返回参数错误且不得保存。

#### Scenario: Other departments cannot manage diagnostic connections
- **WHEN** 其他部门成员请求连接列表、变更或工具发现
- **THEN** 后端 MUST 拒绝请求。

### Requirement: Managed MCP runtime assembly
AIOps SHALL 从当前用户启用的 MCP 连接装配工具；授权企业无用户记录时 SHALL 使用项目 CLS 默认连接。

#### Scenario: 连接被禁用
- **WHEN** 用户禁用连接
- **THEN** 后续诊断 MUST NOT 加载或调用该连接工具。
