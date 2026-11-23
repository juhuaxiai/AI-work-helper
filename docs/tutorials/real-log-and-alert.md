# 真实 CLS 日志、告警与 SOP 教程

本教程生成 10 套 Java 电商微服务故障。每套数据均包含唯一 trace ID、一条 CLS 关键日志、一条 Alertmanager 活动告警和一份处理 SOP，并通过一致的 `incident_id`、`service`、`alertname` 与 `sop` 字段关联。

完整场景及根因说明见 [Java 电商微服务 AIOps 测试数据](../aiops/ecommerce-aiops-fixture.md)。

## 本地验证告警和 SOP

可以先在本地验证告警列表、知识库和诊断失败提示。启动 Docker Compose 的 Milvus 依赖和 Alertmanager，然后手动启动后端与前端。用 `http://127.0.0.1:8000/health` 确认后端可访问。只用本地告警时，可以将外部 Prometheus 来源的 `alertsApi` 留空。

在 `apps/backend` 执行下列命令。先确认配置里的 Alertmanager 地址指向本地测试实例。

```bash
uv run python scripts/publish_java_ecommerce_alerts.py --profile java-ecommerce
```

在「智能诊断」刷新活动告警，确认列表出现带 `environment=test` 的条目。从任一条测试告警发起诊断。当 CLS 地域或日志主题未配置时，任务会进入失败状态，详情提示需要填写哪些配置。重新从历史打开任务时也可以查看该提示。

验证 SOP 检索需要运行 Milvus 并配置可用的模型。可以将 [单份量化定价故障 SOP](../aiops/ecommerce-quant-pricing-latency-sop.md) 上传到自己的知识库并等待索引，再在对话中让 Agent 从知识库检索该 SOP，核对回复引用。也可以按下文步骤 3 用专用 `aiopsDemo` 测试账户导入并索引与这批告警关联的 10 份 SOP。

没有模型或 CLS 凭据时，可以从 `apps/backend` 运行以下回归测试。测试传输和模型替身仅用于自动化测试。

```bash
uv run pytest tests/test_active_alerts.py tests/test_aiops_diagnostics.py tests/test_ecommerce_aiops_fixtures.py tests/test_knowledge_retrieval_tool.py
```

## 本地模拟 TTS 日志诊断

在 `apps/backend` 执行以下测试。环境已安装依赖时，可用 `--no-sync` 直接复用现有虚拟环境。

```bash
uv run --no-sync pytest -q tests/test_aiops_diagnostics.py -k local_tts_log
```

测试启动一个只监听回环地址的临时服务。TTS 测试请求会实际等待，服务将等待时长和请求 ID 写入 JSONL。现有 MCP 客户端通过 `SearchLog` 从该文件查询日志，诊断后台任务随后持久化证据并通过 SSE 返回报告。另一场测试跳过日志写入，确认结果为空时报告会提示证据不足。

测试使用临时 SQLite，诊断告警由测试直接提交，报告由内置回退逻辑生成。整个过程可以离线执行，结束后临时服务自动关闭。

### 在工作台运行本地 TTS 诊断

先配置[本地组织身份](../operations-and-monitoring.md#本地演示组织身份)。在本机 `project.json` 的 `localObservability.tenantKey` 填写演示企业标识。默认文件 `var/tts-observations.jsonl` 相对于 `apps/backend`。保留 `useModel: false` 即可在无云凭据的环境生成回退报告。

在 `apps/backend` 启动测试服务。

```bash
uv run uvicorn super_ai.aiops.tts_demo:create_app --factory --host 127.0.0.1 --port 8091
```

在 PowerShell 触发一次排队延迟或合成延迟。

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8091/simulate/queue-slow
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8091/simulate/synthesis-slow
```

每模拟一种延迟后，用本部门账号在「智能诊断」提交一次手动诊断。工作台读取最近 20 分钟内的最新测试请求，由实测占比选择排队日志或合成指标。通过 API 发起时也可在 `alert.traceId` 指定返回的 `trace_id`。报告保留测试阶段耗时和 Trace ID，作为对应测试请求的证据。

### 接收本地 Alertmanager 推送

本机 `project.json` 的 `alertDelivery.sources` 可配置来源。生成至少 16 个字符的随机 token，将同一值写入本机配置的 `webhookToken` 和 Alertmanager 私有凭据文件。该来源 ID 必须在当前企业的 `organizationAccess.tenants[].alert_sources` 中。配置示例中的自动诊断使用本部门演示账号的 ID，删除 `autoDiagnosticAlertNames` 即只保留提醒。

```json
{
  "alertDelivery": {
    "sources": [
      {
        "sourceId": "local-alertmanager",
        "webhookToken": "",
        "autoDiagnosticOwnerUserId": "替换为已绑定到本部门的本地账号ID",
        "autoDiagnosticAlertNames": ["LocalTtsLatencyHigh"]
      }
    ]
  }
}
```

使用仓库的 `infra/prometheus/prometheus.example.yml`、`infra/prometheus/tts-demo.rules.yml` 和 `infra/alertmanager/oncall.example.yml` 启动本机 Prometheus 与 Alertmanager。Alertmanager 模板里的 URL 与凭据文件路径应能被该进程访问。容器部署时，填入容器实际可访问的服务地址和容器内凭据文件路径。

执行一次 `queue-slow` 或 `synthesis-slow`，等待工作台标题栏出现活跃告警，再打开「告警通知」。配置了自动诊断规则的账号可以从对应通知打开诊断。执行 `/simulate/normal` 后，旧 Trace 的通知会在 Alertmanager 发送解除事件时更新为「已解除」。工作台每三秒刷新；完整时延也受采集和告警分组周期影响。

### 导出诊断范围内的本地日志

以发起诊断的本部门账号调用 `POST /aiops/diagnostics/{diagnosticId}/log-exports`。本地 TTS 诊断完成后，接口返回 `downloadPath`、记录数、字节数、时间窗和过期时间。带同一登录凭证访问 `downloadPath` 获取 JSONL 附件。

快照只包含 TTS 测试环境的时间、Trace ID、排队与合成实测耗时等允许字段，复用原诊断的查询范围。单次最多 500 条且不超过 128 KiB，文件引用有效期为十分钟。诊断的工具审计可查看导出和读取记录。

在同一登录态向 `downloadPath` 后加 `:analyze` 发送 `POST`，即可调度离线统计。用返回的任务 ID 访问 `GET /background-jobs/{jobId}` 查看状态，成功后从当前诊断的证据链和报告查看排队、合成阶段各自的均值、P95 和最大耗时。重复提交同一个未过期引用会返回同一个任务。统计复用下面的沙箱 Worker 与镜像，镜像须包含 `tts-log-statistics` 模板。

### 审批本地 TTS 沙箱验证

在仓库根目录构建固定验证镜像并读取镜像的内容 ID。

```bash
docker build -t agent-py-verifier:local infra/sandbox
docker image inspect agent-py-verifier:local --format '{{.Id}}'
```

在本机 `project.json` 的 `sandboxVerification` 填入当前测试企业 `tenantKey` 和返回的 `imageId`，按需填写 `approvalSeconds`。迁移数据库后，从 `apps/backend` 启动独立 Worker。

```bash
uv run alembic upgrade head
uv run python -m super_ai.aiops.sandbox_worker
```

对已有测试 Trace 的诊断打开「沙箱验证」，申请后检查实验模板、镜像、有效期和资源限制。批准后 Worker 才会领取运行。排队实验比较不同并发数下的等待耗时，合成实验比较测试耗时与基线。实测值与执行编号会回写该诊断。实验在固定容器内运行，使用无外部网络、非 root、只读根文件系统及有限资源。

在同一配置段启用 `useAgent: true`，并配置本机 Qwen 模型后，诊断可根据实测阶段将假设交给验证 Agent。验证 Agent 选择预设模板或记录跳过原因。待审批时，验证线程保存在 `checkpointPath` 指定的 SQLite 文件。完成、拒绝或取消后从同一线程接收结果，在原报告中补充结论。迁移或备份时同时保留应用数据库和该 checkpoint 文件。 [LangGraph 中断与恢复](https://docs.langchain.com/oss/python/langgraph/interrupts)

本部门成员在对话中提出故障排查问题时，可以点「转入诊断」预填草稿，点击「开始诊断」后提交任务。

下文用于验证接入腾讯云 CLS 后的日志证据链。

## 前置条件

1. 按操作系统安装指南完成依赖安装。
2. 在仓库根目录执行 `./scripts/start-local.sh` 或 Windows 的 `scripts\start-local.bat`。
3. 确认 `http://127.0.0.1:8000/ready` 返回就绪状态。
4. 检查项目配置中的 `clsLogUpload`、`clsMcpServer`、`prometheusAlerts` 和 `aiopsDemo` 指向目标测试环境。

## 1. 上传 10 条真实 CLS 日志

在 `apps/backend` 执行：

```bash
uv run python scripts/generate_and_upload_cls_logs.py --profile java-ecommerce
```

脚本使用腾讯云官方 CLS SDK 上传 10 条结构化 Java 日志。每条日志属于不同微服务和 incident，包含唯一 trace ID、异常类、依赖组件、指标观测值、触发阈值和匹配 SOP，不包含密钥、Token 或客户数据。

## 2. 发布 10 条本地活动告警

启动器已启动 Alertmanager。执行：

```bash
uv run python scripts/publish_java_ecommerce_alerts.py --profile java-ecommerce
```

脚本通过 Alertmanager v2 API 一次发布 10 条告警。所有告警均标记为 `environment=test`、`fixture=java-ecommerce`，可在 `http://127.0.0.1:9093` 查看。

## 3. 上传并索引 10 份关联 SOP

```bash
uv run python scripts/seed_java_ecommerce_aiops_sops.py --profile java-ecommerce
```

脚本使用 `aiopsDemo` 账户通过真实后端 API 上传 10 份 Markdown SOP，并逐份等待 Milvus 索引成功。每份 SOP 包含对应 trace 查询、指标阈值、根因假设、排查步骤、恢复动作和验证标准。

## 4. 从前端执行诊断

1. 打开 `http://127.0.0.1:5173` 并登录。
2. 进入 **AIOps diagnosis** 页面并刷新活动告警。
3. 选择任一 `java-ecommerce` 告警并开始诊断。
4. 等待 Planner、Executor、Replanner、Report 的 SSE 事件结束。

成功证据链必须包括选中告警、同 incident 的 SOP 知识引用，以及通过真实 CLS MCP `SearchLog` 查询到的相同 trace ID 日志。某个工具失败时报告必须如实说明，不得使用其他场景证据填充。

## 常见排查

- Milvus 未就绪：确认 `etcd`、`minio` 和 `milvus` 容器健康。
- MCP 失败：确认 `cls-mcp-server` 本机进程监听 `3000` 端口。
- CLS 查询无新日志：核对地域、主题 ID 与凭据，等待短暂写入延迟后按 trace ID 重试。
- 没有活动告警：再次运行 `publish_java_ecommerce_alerts.py --profile java-ecommerce`。
- SOP 未命中：确认 10 个索引任务均为 `succeeded`，并使用告警 annotations 中的 SOP ID 检索。
