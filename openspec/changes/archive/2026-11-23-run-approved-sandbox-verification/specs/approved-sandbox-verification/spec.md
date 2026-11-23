## ADDED Requirements

### Requirement: Approval is scoped to an immutable verification plan
后端 SHALL 从授权测试证据生成带版本和到期时间的持久验证计划，批准前 MUST 不入队执行。

#### Scenario: Approval survives restart and repeated submission
- **WHEN** 应用在等待审批时重启，同一版本随后被多次批准
- **THEN** 计划 MUST 保留原记录，且执行编号和执行任务只创建一次。

#### Scenario: Access or approval is invalid
- **WHEN** 组织权限、计划版本或有效期不匹配，或审批被拒绝
- **THEN** 系统 MUST 不启动实验。

### Requirement: Fixed tests run inside an isolated sandbox
独立 Docker Worker SHALL 执行固定模板并核验当前组织权限。沙箱 SHALL 限制网络、用户、文件系统、资源和执行时间。

#### Scenario: Approved local measurement is verified
- **WHEN** Worker 执行获批的测试验证
- **THEN** 它 MUST 将实测对照、镜像内容 ID、执行编号和退出状态写入原诊断证据链，并在结果持久化后清理容器。

#### Scenario: Execution can be cancelled or recovered
- **WHEN** 运行期间被取消，或 Worker 重启后发现已有同编号容器
- **THEN** Worker MUST 清理已取消容器，并根据原容器状态恢复结果且不启动第二次实验。
