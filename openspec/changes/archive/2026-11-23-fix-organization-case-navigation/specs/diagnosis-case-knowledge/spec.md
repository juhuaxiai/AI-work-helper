## ADDED Requirements

### Requirement: Open a diagnostic case in its scoped knowledge base
系统 SHALL 使用后端授权的知识库和文档地址，从案例列表进入生成的知识文档。

#### Scenario: 打开组织案例文档
- **WHEN** 诊断所有者从案例库打开某个案例的文档
- **THEN** 前端 MUST 传递接口返回的知识库和文档 ID，并访问该授权知识库中的文档
