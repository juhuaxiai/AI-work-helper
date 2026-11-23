## Why

组织诊断案例保存在受权限保护的独立案例知识库中。现有链接只传文档 ID，无法从默认个人知识库定位此文档。

## What Changes

- 返回已授权案例的知识库 ID，打开时使用知识库和文档的完整地址。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `diagnosis-case-knowledge`: 已授权案例文档的导航要求。

## Impact

案例列表和详情 API、共享契约、前端路由及相应测试。
