## Why

值班成员取得脱敏日志快照后，需要可复现的统计结果和报告证据，不必在本机下载文件或把日志正文交给模型。

## What Changes

- 对尚未过期的本地日志快照执行固定 Docker 统计模板。
- 通过持久后台任务显示运行状态，结果关联当前诊断报告和据源。

## Capabilities

### New Capabilities

- `sandbox-log-statistics`: 离线、只读日志统计与证据回写。

### Modified Capabilities

无。

## Impact

后端工作流、Docker 镜像、后台任务 API、诊断证据和运维文档。
