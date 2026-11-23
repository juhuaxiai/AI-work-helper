# 配置与运维

应用只读取本地 `config/project.json` 和可选的 `config/user.project.json`，不读取本机环境变量。两个文件都被 Git 忽略；仓库只保留不含真实凭据的模板。不要为本项目创建 `.env` 文件，也不要提交本地配置。

首次使用时，在仓库根目录执行：

```bash
cp config/project.template.json config/project.json
cp config/user.project.template.json config/user.project.json
```

## 项目交付后的个人配置

在本地 `config/user.project.json` 中填写使用者自己的模型密钥、CLS 凭据和 CLS 日志目标。其他运行参数可在本地 `config/project.json` 中调整：

| 配置字段 | 替换为 |
| --- | --- |
| `llm.apiKey` | 使用者自己的模型服务密钥 |
| `clsMcpServer.secretId` | 使用者自己的 CLS 凭据 ID |
| `clsMcpServer.secretKey` | 使用者自己的 CLS 凭据密钥 |
| `clsLogUpload.region` | 使用者自己的 CLS 地域 |
| `clsLogUpload.logsetId` | 使用者自己的 CLS 日志集 ID |
| `clsLogUpload.topicId` | 使用者自己的 CLS 主题 ID |

模板中的凭据字段必须保持为空。即使仓库是私有仓库，也不要提交真实密钥；已暴露的密钥应在对应云平台立即轮换。

前端只接收 `frontend.apiBaseUrl`。Vite 在 Node 进程中读取该地址，`user.project.json` 可以覆盖基础配置。更改地址后重启前端开发服务器或重新构建。开发服务器会拒绝读取两份运行时 JSON。

### MongoDB 聊天存储

默认使用 SQLite 保存聊天。本地体验可继续使用默认配置。正式部署可在 `config/project.json` 将 `chatStorage.driver` 设为 `mongodb` 并设置 `chatStorage.mongodbDatabase`，连接串填写到被 Git 忽略的 `config/user.project.json` 的 `chatStorage.mongodbUri`。MongoDB 保存会话、消息、引用、记忆摘要及聊天工具审计。认证、诊断、审批、后台任务和反馈继续使用配置中的 SQLite。

MongoDB 连接需指向可写副本集或分片集群，应用账号需要对目标聊天数据库拥有创建索引及读写集合的权限。`/ready` 的 `dependencies.mongodb` 会报告连接与拓扑状态。启用后，连接故障会返回统一的不可用错误。[MongoDB 事务文档](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/transactions/)

切换前暂停后端写入，备份 SQLite，确保其 Alembic 版本已升级到 `head`，再从 `apps/backend` 运行：

```bash
uv run python -m super_ai.memory.migrate_chat
```

迁移保留会话、消息和审计的原始 ID、消息顺序、引用和记忆状态。命令以会话为单位提交，可在失败后用相同配置重跑。成功迁移后启动后端，确认 `/ready` 及代表性会话。SQLite 源库保留原记录，切回 SQLite 时需单独处置切换至 MongoDB 后产生的聊天变更。

### 本地演示组织身份

注册并登录演示账号，从 `GET /auth/me` 的 `data.id` 取得本地账号 ID。在本机 JSON 配置中添加 `organizationAccess`，以该 ID 为成员建立绑定。下面是有两个部门的测试企业。

```json
{
  "organizationAccess": {
    "tenants": [
      {
        "tenant_key": "demo-company",
        "oncall_department_id": "demo-oncall",
        "alert_sources": ["local-alertmanager"]
      }
    ],
    "members": [
      {
        "user_id": "替换为本部门演示账号ID",
        "tenant_key": "demo-company",
        "open_id": "demo-member-1",
        "department_ids": ["demo-oncall"]
      },
      {
        "user_id": "替换为其他部门演示账号ID",
        "tenant_key": "demo-company",
        "open_id": "demo-member-2",
        "department_ids": ["demo-business"]
      }
    ],
    "cls_tenant_key": "demo-company"
  }
}
```

`alert_sources` 使用项目配置中已有的告警来源 ID，每个来源只分配给一个企业。`cls_tenant_key` 指定可以使用全局 CLS 连接的唯一企业，留空时不启用 CLS 诊断。修改绑定后重新登录刷新前端入口，后端会在请求和执行阶段重新检查当前绑定。加入组织前已有的诊断保存在原数据库中，组织历史只列出已归属当前企业的诊断。

本部门可在工作台的知识库选择「企业共享知识」或「运维 SOP」并上传文档、建立索引。其他部门可读取本企业共享知识；个人知识库仍归个人。使用当前企业中新建的普通会话可检索共享知识。升级前的会话继续按个人范围访问，新企业会话随组织权限变更而停止访问。

智能诊断的「案例库」可以打开生成的知识文档，页面会自动切换到该文档所在的已授权知识库。

普通对话可调用知识检索、时间工具和已选 Skill。MCP 管理和日志查询位于本部门的诊断工作区。

## 真实日志与本地告警样例

真实 CLS 日志上传和本地 active-alert 演示属于显式运维流程，不是常规应用启动的一部分。请遵循[真实 CLS 日志与告警教程](tutorials/real-log-and-alert.md)，分别执行其中的日志上传脚本和本地告警命令。
