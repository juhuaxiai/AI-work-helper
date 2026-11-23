from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from super_ai.aiops import AiopsDiagnosticService, DiagnosisCasePersistor
from super_ai.aiops.tts_demo import create_app as create_tts_app
from super_ai.api.app import AiopsDiagnosticRunner, MilvusHealthCheckProvider, create_app
from super_ai.llm import LlmProvider, RerankResult
from super_ai.mcp_client import LocalMcpClient, McpClientError, McpToolDefinition
from super_ai.memory.database import create_memory_engine, create_memory_session_factory
from super_ai.memory.repositories import (
    BackgroundJobEventRecord,
    DiagnosticTaskRecord,
    MemoryRepositories,
    TenantScopeError,
)
from super_ai.memory.sqlite import create_sqlite_memory_repositories
from super_ai.organization import case_knowledge_base_id
from super_ai.retrieval import KnowledgeRetrievalTool
from super_ai.vector_store import (
    MilvusVectorStore,
    MilvusVectorStoreSettings,
    StoredVectorChunk,
    VectorSearchResult,
)


class FakeEmbeddingModel:
    def __init__(self) -> None:
        self.inputs: list[list[str]] = []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.inputs.append(texts)
        return [[0.1, 0.2] for _ in texts]


class FakeVectorStore:
    def __init__(self, hits: list[VectorSearchResult]) -> None:
        self.hits = hits

    def search_chunks(
        self,
        *,
        query_vector: Sequence[float],
        tenant_id: str,
        knowledge_base_ids: Sequence[str],
        limit: int,
    ) -> list[VectorSearchResult]:
        return list(self.hits[:limit])

    def list_chunks(
        self,
        *,
        tenant_id: str,
        knowledge_base_ids: Sequence[str],
    ) -> list[StoredVectorChunk]:
        return [
            StoredVectorChunk(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                knowledge_base_id=hit.knowledge_base_id,
                owner_user_id=hit.owner_user_id,
                tenant_id=hit.tenant_id,
                content=hit.content,
                source=hit.source,
                created_at=hit.created_at,
                metadata=hit.metadata,
            )
            for hit in self.hits
        ]


class FakeRerankModel:
    async def arerank(
        self, *, query: str, documents: Sequence[str], top_n: int
    ) -> list[RerankResult]:
        return [
            RerankResult(index=index, relevance_score=0.95 - index / 100)
            for index in range(min(top_n, len(documents)))
        ]


REPORT_MARKDOWN = """# 告警分析报告

---

## 📋 活跃告警清单

| 告警名称 | 级别 | 目标服务 | 首次触发时间 | 最新触发时间 | 状态 |
|---|---|---|---|---|---|
| WorkerCpuHigh | critical | worker | 未获取 | 未获取 | 活跃 |

---

## 🔍 告警根因分析1 - WorkerCpuHigh

### 告警详情
- **告警级别**: critical

### 日志证据
- SearchLog 返回 cpu_pressure

### 根因结论
证据支持 CPU 压力症状，但不足以确认最终根因。

---

## 🛠️ 处理方案执行1 - WorkerCpuHigh

### 已执行的排查步骤
1. 查询 CLS 日志

### 处理建议
依据 SOP 继续检查 CPU 饱和度。

### 预期效果
确认 CPU 压力来源。

---

## 📊 结论

### 整体评估
存在需要继续核实的 CPU 压力告警。

### 关键发现
- CLS 返回 cpu_pressure 证据。

### 后续建议
1. 补充 CPU 指标。

### 风险评估
当前风险较高，但影响范围尚未获取。"""


class FakeChatModel:
    def __init__(
        self,
        *,
        report_response: str = f"```markdown\n{REPORT_MARKDOWN}\n```",
        report_error: Exception | None = None,
    ) -> None:
        self.report_response = report_response
        self.report_error = report_error
        self.inputs: list[object] = []

    async def ainvoke(self, input: object) -> object:
        self.inputs.append(input)
        if "AIOps 诊断报告生成器" in str(input):
            if self.report_error is not None:
                raise self.report_error
            return self.report_response
        return '{"steps": []}'


class FakeLlmProvider:
    def __init__(
        self,
        *,
        report_response: str = f"```markdown\n{REPORT_MARKDOWN}\n```",
        report_error: Exception | None = None,
    ) -> None:
        self.chat_model = FakeChatModel(
            report_response=report_response,
            report_error=report_error,
        )

    def create_chat_model(self) -> FakeChatModel:
        return self.chat_model

    def create_embedding_model(self) -> FakeEmbeddingModel:
        return FakeEmbeddingModel()

    async def check_readiness(self) -> object:
        return object()


class FakeMcpClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def discover_tools(self) -> list[McpToolDefinition]:
        return [McpToolDefinition("SearchLog", "Search real logs", {})]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> object:
        self.calls.append((name, arguments))
        if self.error is not None:
            raise self.error
        return [
            {
                "type": "text",
                "text": json.dumps(
                    [
                        {
                            "LogJson": json.dumps(
                                {
                                    "timestamp": "2026-07-11T06:00:00Z",
                                    "level": "ERROR",
                                    "service": "worker",
                                    "event": "cpu_pressure",
                                    "message": "Worker CPU exceeded threshold",
                                    "latency_ms": "2450",
                                    "secret": "must-not-enter-report-context",
                                }
                            )
                        }
                    ]
                ),
            }
        ]


class FakeCaseIndexScheduler:
    def __init__(self) -> None:
        self.scheduled: list[tuple[str, str]] = []

    def schedule(self, *, owner_user_id: str, task_id: str) -> None:
        self.scheduled.append((owner_user_id, task_id))


LOCAL_TTS_TOPIC = "local-tts-test"
LOCAL_TTS_QUEUE_THRESHOLD_MS = 25


@pytest.mark.parametrize(
    ("scenario", "expected_tools"),
    [
        ("queue-slow", ["query_traces", "search_logs"]),
        ("synthesis-slow", ["query_traces", "query_metrics"]),
        ("absent", ["query_traces"]),
        ("production", []),
    ],
)
async def test_workbench_reads_actual_local_measurements(
    migrated_database_url: str,
    tmp_path: Path,
    bind_oncall: Callable[[str], None],
    scenario: str,
    expected_tools: list[str],
) -> None:
    config_path = tmp_path / "project.json"
    config_path.write_text(json.dumps({
        "localObservability": {
            "tenantKey": "test-tenant",
            "logPath": str(tmp_path / "tts.jsonl"),
            "useModel": False,
        },
    }), encoding="utf-8")
    demo = create_tts_app(config_path=config_path)
    trace_id = "a" * 32
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=demo), base_url="http://tts-demo"
    ) as demo_client:
        if scenario in {"queue-slow", "synthesis-slow"}:
            result = await demo_client.post(f"/simulate/{scenario}")
            assert result.status_code == 200
            measurement = result.json()
            trace_id = measurement["trace_id"]
            assert measurement["environment"] == "test"
            assert max(measurement["queue_ms"], measurement["synthesis_ms"]) >= 80
            metrics = await demo_client.get("/metrics")
            assert 'environment="test"' in metrics.text and trace_id in metrics.text
            assert f'phase="queue"}} {measurement["queue_ms"]}' in metrics.text
            assert f'phase="synthesis"}} {measurement["synthesis_ms"]}' in metrics.text
            # An unrelated observation in the same time range must not influence the plan.
            other = await demo_client.post("/simulate/normal")
            assert other.json()["trace_id"] != trace_id

    app = create_app(
        database_url=migrated_database_url,
        project_config_path=config_path,
        vector_store=MilvusVectorStore(settings=MilvusVectorStoreSettings()),
    )
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        owner = await _register(client, "workbench-tts@example.com")
        bind_oncall(owner["user"]["id"])
        headers = _auth_headers(owner["accessToken"])
        created = await client.post("/aiops/diagnostics", headers=headers, json={
            "query": "排查 TTS 延迟",
            "alert": {
                "service": "tts-demo",
                "environment": "production" if scenario == "production" else "test",
                "traceId": trace_id,
            },
        })
        assert created.status_code == 202
        task_path = f"/aiops/diagnostics/{created.json()['data']['id']}"
        stream = await asyncio.wait_for(client.post(f"{task_path}:stream", headers=headers), 20)
        detail = await client.get(f"{task_path}/evidence-chain", headers=headers)
    chain = detail.json()["data"]
    assert [call["toolName"] for call in chain["toolCalls"]] == expected_tools
    if scenario == "production":
        assert chain["task"]["status"] == "failed"
        assert "AUTH_FORBIDDEN" in stream.text
        return
    assert chain["task"]["status"] == "succeeded"
    assert "event: complete" in stream.text
    report = chain["reports"][0]
    assert report["payload"]["reportGeneration"] == "fallback"
    if scenario == "absent":
        assert "没有本地 TTS 观测" in report["content"]
        return
    assert trace_id in report["content"]
    assert all(item["id"] in report["evidenceIds"] for item in chain["evidence"])
    assert {item["kind"] for item in chain["evidence"]} == {
        "alert", "trace", "log" if scenario == "queue-slow" else "metric",
    }


@asynccontextmanager
async def _local_tts_mcp(log_path: Path) -> AsyncGenerator[str, None]:
    mcp = FastMCP("Local TTS observability test")

    def search_log(TopicId: str, From: int, To: int, Limit: int) -> str:  # noqa: N803
        """Read the JSONL produced by the local TTS test request."""
        if TopicId != LOCAL_TTS_TOPIC:
            raise ValueError("Unknown local test topic.")
        results: list[dict[str, str]] = []
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                record = cast(dict[str, object], json.loads(line))
                timestamp = datetime.fromisoformat(str(record["timestamp"]))
                timestamp_ms = int(timestamp.timestamp() * 1000)
                if From <= timestamp_ms <= To:
                    results.append({"LogJson": line})
        return json.dumps(results[:max(0, min(Limit, 100))])

    async def enqueue_tts(_request: Request) -> JSONResponse:
        queued_at = monotonic()
        await asyncio.sleep(0.05)
        queue_ms = int((monotonic() - queued_at) * 1000)
        request_id = f"local-tts-{uuid4().hex}"
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "environment": "test",
            "service": "tts-demo",
            "level": "WARNING",
            "event": "tts_queue_delay",
            "request_id": request_id,
            "latency_ms": queue_ms,
            "message": f"Local test request {request_id} waited {queue_ms} ms in the TTS queue.",
        }
        with log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record) + "\n")
        return JSONResponse(record)

    mcp.add_tool(search_log, name="SearchLog", structured_output=False)
    application = mcp.sse_app()
    application.add_route("/tts-demo/queue", enqueue_tts, methods=["POST"])

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
        server = uvicorn.Server(
            uvicorn.Config(application, log_config=None, access_log=False, http="h11")
        )
        running = asyncio.create_task(server.serve(sockets=[listener]))

        async def wait_until_listening() -> None:
            while not server.started:
                if running.done():
                    running.result()
                    raise RuntimeError("Local TTS test listener exited before startup.")
                await asyncio.sleep(0.01)

        try:
            await asyncio.wait_for(wait_until_listening(), timeout=5)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(running, timeout=5)


@pytest.mark.parametrize("emit_log", [True, False], ids=["queue-delay", "no-logs"])
async def test_local_tts_log_to_diagnostic_report(
    migrated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    emit_log: bool,
    bind_oncall: Callable[[str], None],
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    log_path = tmp_path / "tts-test.jsonl"
    local_config = tmp_path / "project.json"
    log_record: dict[str, Any] = {}

    async with _local_tts_mcp(log_path) as local_url:
        if emit_log:
            async with httpx.AsyncClient(base_url=local_url, trust_env=False) as workload:
                response = await workload.post("/tts-demo/queue")
                response.raise_for_status()
                log_record = cast(dict[str, Any], response.json())
            assert log_record["environment"] == "test"
            assert log_record["latency_ms"] >= LOCAL_TTS_QUEUE_THRESHOLD_MS
            assert json.loads(log_path.read_text(encoding="utf-8")) == log_record

        local_config.write_text(
            json.dumps(
                {
                    "clsLogUpload": {"region": "local-test", "topicId": LOCAL_TTS_TOPIC},
                    "mcp": {
                        "clsSseUrl": f"{local_url}/sse",
                        "timeoutSeconds": 4,
                        "retries": 0,
                    },
                }
            ),
            encoding="utf-8",
        )
        app = create_app(
            database_url=migrated_database_url,
            project_config_path=local_config,
            vector_store=cast(MilvusHealthCheckProvider, FakeVectorStore([])),
            embedding_model=FakeEmbeddingModel(),
            rerank_model=FakeRerankModel(),
            llm_provider=cast(LlmProvider, FakeLlmProvider(report_error=RuntimeError("Offline"))),
            index_task_scheduler=FakeCaseIndexScheduler(),
        )
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            account = await _register(client, "local-tts@example.com")
            bind_oncall(account["user"]["id"])
            headers = _auth_headers(account["accessToken"])
            alert = {
                "alertName": "LocalTtsQueueDelay",
                "service": "tts-demo",
                "severity": "warning",
                "status": "firing",
                "startsAt": log_record.get("timestamp", datetime.now(timezone.utc).isoformat()),
                "labels": {"environment": "test", "request_id": log_record.get("request_id", "")},
            }
            created = await client.post(
                "/aiops/diagnostics",
                headers=headers,
                json={"query": "排查本地 TTS 队列延迟", "alert": alert},
            )
            assert created.status_code == 202
            task = created.json()["data"]
            path = f"/aiops/diagnostics/{task['id']}"
            stream = await asyncio.wait_for(client.post(f"{path}:stream", headers=headers), 30)
            detail = await client.get(f"{path}/evidence-chain", headers=headers)
            job_id = task["backgroundJob"]["id"]
            job = await client.get(f"/background-jobs/{job_id}", headers=headers)

    assert stream.status_code == detail.status_code == job.status_code == 200
    assert "event: complete" in stream.text
    assert "event: error" not in stream.text
    assert job.json()["data"]["status"] == "succeeded"
    chain = detail.json()["data"]
    assert chain["task"]["status"] == "succeeded"
    phases = [step["phase"] for step in chain["steps"]]
    assert phases == ["planner", "executor", "replanner", "report"]
    evidence = next(item for item in chain["evidence"] if item["kind"] == "log")
    assert evidence["source"] == "SearchLog"
    assert evidence["payload"]["arguments"]["TopicId"] == LOCAL_TTS_TOPIC
    report = chain["reports"][0]
    assert report["payload"]["reportGeneration"] == "fallback"
    assert evidence["id"] in report["evidenceIds"]
    summary = json.loads(evidence["summary"])
    assert summary["recordCount"] == int(emit_log)
    assert "证据不足，无法确认根因" in report["content"]
    if emit_log:
        assert summary["records"][0]["request_id"] == log_record["request_id"]
        assert summary["records"][0]["latency_ms"] == log_record["latency_ms"]
        assert log_record["request_id"] in report["content"]
        assert "tts_queue_delay" in report["content"]
    else:
        assert summary["records"] == []
        assert "tts_queue_delay" not in report["content"]


@pytest.mark.asyncio
async def test_diagnostic_runs_sop_first_persists_evidence_and_audits(
    migrated_database_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    logging.getLogger("super_ai.aiops.diagnostics").disabled = False
    caplog.set_level(logging.INFO, logger="super_ai.aiops.diagnostics")
    engine = create_memory_engine(migrated_database_url)
    try:
        repositories = create_sqlite_memory_repositories(create_memory_session_factory(engine))
        task = await repositories.diagnostics.create_task(
            owner_user_id="user-a",
            task_id="diagnostic-a",
            status="accepted",
            query="Investigate worker CPU alarm",
            input_payload={"alert": {"severity": "critical"}},
        )
        embedding = FakeEmbeddingModel()
        retrieval = KnowledgeRetrievalTool(
            embedding_model=embedding,
            vector_store=FakeVectorStore([_sop_hit("user-a")]),
            rerank_model=FakeRerankModel(),
        )
        mcp = FakeMcpClient()
        scheduler = FakeCaseIndexScheduler()
        provider = FakeLlmProvider()
        service = AiopsDiagnosticService(
            repositories=repositories,
            llm_provider=cast(LlmProvider, provider),
            retrieval_tool=retrieval,
            mcp_client=cast(LocalMcpClient, mcp),
            cls_region="ap-guangzhou",
            cls_topic_id="topic-a",
            case_persistor=DiagnosisCasePersistor(
                repositories=repositories,
                index_task_scheduler=scheduler,
            ),
        )

        events = [
            event
            async for event in service.stream(
                task=task,
                accessible_knowledge_base_ids=("kb_user-a",),
            )
        ]
        persisted = await repositories.diagnostics.get_task(
            owner_user_id="user-a",
            task_id=task.id,
        )
        reports = await repositories.diagnostics.list_reports(
            owner_user_id="user-a",
            task_id=task.id,
        )
        checkpoints = await repositories.diagnostics.list_checkpoints(
            owner_user_id="user-a",
            task_id=task.id,
        )
        steps = await repositories.diagnostics.list_steps(
            owner_user_id="user-a",
            task_id=task.id,
        )
        evidence = await repositories.diagnostics.list_evidence(
            owner_user_id="user-a",
            task_id=task.id,
        )
        report_links = await repositories.diagnostics.list_report_evidence_links(
            owner_user_id="user-a",
            task_id=task.id,
        )
        audits = await cast(Any, repositories.tool_call_audits).list_for_diagnostic_task(
            owner_user_id="user-a",
            diagnostic_task_id=task.id,
        )
        cases = await repositories.diagnostics.list_cases(owner_user_id="user-a")
    finally:
        await engine.dispose()

    assert embedding.inputs == [["Investigate worker CPU alarm"]]
    assert mcp.calls[0][0] == "SearchLog"
    assert persisted is not None
    assert persisted.status == "succeeded"
    assert persisted.result_payload["noSopMatched"] is False
    assert reports[0].payload["reportGeneration"] == "llm"
    assert reports[0].title == "告警分析报告"
    assert reports[0].content == REPORT_MARKDOWN
    assert "```" not in reports[0].content
    report_prompt = next(
        str(item) for item in provider.chat_model.inputs if "AIOps 诊断报告生成器" in str(item)
    )
    assert "## 📋 活跃告警清单" in report_prompt
    assert "## 🔍 告警根因分析1" in report_prompt
    assert "## 🛠️ 处理方案执行1" in report_prompt
    assert "## 📊 结论" in report_prompt
    assert '"severity":"critical"' in report_prompt
    report_evidence = cast(list[dict[str, object]], reports[0].payload["evidence"])
    assert report_evidence[0]["tool"] == "SearchLog"
    assert "cpu_pressure" in str(report_evidence[0]["summary"])
    assert "must-not-enter-report-context" not in str(report_evidence[0]["summary"])
    assert [checkpoint.checkpoint_ns for checkpoint in checkpoints] == [
        "planner",
        "executor",
        "replanner",
        "report",
    ]
    assert [audit.tool_name for audit in audits] == ["knowledge_retrieval", "SearchLog"]
    assert [step.phase for step in steps] == ["planner", "executor", "replanner", "report"]
    assert {item.kind for item in evidence} == {"alert", "knowledge_reference", "log"}
    assert all(link.report_id == reports[0].id for link in report_links)
    assert {link.evidence_id for link in report_links} == {item.id for item in evidence}
    assert set(cast(list[str], reports[0].payload["evidenceIds"])) == {
        item.id for item in evidence
    }
    assert [event["type"] for event in events][-2:] == ["task.status", "complete"]
    assert any(event["type"] == "reference.source" for event in events)
    assert len(cases) == 1
    assert cases[0].task_id == task.id
    assert cases[0].report_id == reports[0].id
    assert cases[0].document_id
    assert cases[0].index_task_id
    assert scheduler.scheduled == [("user-a", cases[0].index_task_id)]
    events = [
        json.loads(record.message) for record in caplog.records if record.message.startswith("{")
    ]
    assert {event["event"] for event in events} == {
        "agent.aiops.started",
        "agent.aiops.completed",
    }
    assert all(event["diagnosticTaskId"] == task.id for event in events)
    assert "Investigate worker CPU alarm" not in "\n".join(
        record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_diagnostic_no_sop_and_mcp_failure_are_explicit(
    migrated_database_url: str,
) -> None:
    engine = create_memory_engine(migrated_database_url)
    try:
        repositories = create_sqlite_memory_repositories(create_memory_session_factory(engine))
        task = await repositories.diagnostics.create_task(
            owner_user_id="user-a",
            task_id="diagnostic-b",
            status="accepted",
            query="Investigate missing logs",
        )
        service = AiopsDiagnosticService(
            repositories=repositories,
            llm_provider=cast(
                LlmProvider,
                FakeLlmProvider(report_error=RuntimeError("report model unavailable")),
            ),
            retrieval_tool=KnowledgeRetrievalTool(
                embedding_model=FakeEmbeddingModel(),
                vector_store=FakeVectorStore([]),
                rerank_model=FakeRerankModel(),
            ),
            mcp_client=cast(LocalMcpClient, FakeMcpClient(error=McpClientError("CLS unavailable"))),
            cls_region="ap-guangzhou",
            cls_topic_id="topic-a",
            case_persistor=DiagnosisCasePersistor(
                repositories=repositories,
                index_task_scheduler=FakeCaseIndexScheduler(),
            ),
        )

        events = [
            event
            async for event in service.stream(
                task=task,
                accessible_knowledge_base_ids=("kb_user-a",),
            )
        ]
        persisted = await repositories.diagnostics.get_task(
            owner_user_id="user-a",
            task_id=task.id,
        )
        reports = await repositories.diagnostics.list_reports(
            owner_user_id="user-a",
            task_id=task.id,
        )
        cases = await repositories.diagnostics.list_cases(owner_user_id="user-a")
    finally:
        await engine.dispose()

    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.result_payload["noSopMatched"] is True
    assert reports[0].payload["reportGeneration"] == "fallback"
    assert reports[0].content.startswith("# 告警分析报告")
    assert "未检索到匹配的 SOP" in reports[0].content
    assert "诊断工具执行失败" in reports[0].content
    assert "证据不足，无法确认根因" in reports[0].content
    assert any(event["type"] == "error" for event in events)
    assert any(_is_failed_tool_event(event) for event in events)
    assert cases == []


@pytest.mark.parametrize(
    "cls_config",
    [{"region": "", "topicId": "example-topic"}, {"region": "example-region", "topicId": " "}],
)
async def test_missing_cls_configuration_persists_diagnostic_failure_and_replay(
    migrated_database_url: str,
    tmp_path: Path,
    cls_config: dict[str, str],
    bind_oncall: Callable[[str], None],
) -> None:
    from super_ai.vector_store import MilvusVectorStore, MilvusVectorStoreSettings

    config = tmp_path / "project.json"
    config.write_text(json.dumps({"clsLogUpload": cls_config}), encoding="utf-8")
    app = create_app(
        database_url=migrated_database_url,
        project_config_path=config,
        vector_store=MilvusVectorStore(settings=MilvusVectorStoreSettings()),
    )
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        owner = await _register(client, "missing-cls@example.com")
        other = await _register(client, "other-cls@example.com")
        bind_oncall(owner["user"]["id"])
        bind_oncall(other["user"]["id"])
        headers = _auth_headers(owner["accessToken"])
        created = await client.post(
            "/aiops/diagnostics", headers=headers, json={"query": "Investigate a test alert"}
        )
        assert created.status_code == 202
        task = created.json()["data"]
        path = f"/aiops/diagnostics/{task['id']}"
        stream = await client.post(f"{path}:stream", headers=headers)
        replay = await client.post(f"{path}:stream", headers=headers)
        detail = await client.get(f"{path}/evidence-chain", headers=headers)
        job = await client.get(f"/background-jobs/{task['backgroundJob']['id']}", headers=headers)
        forbidden = await client.get(
            f"{path}/evidence-chain", headers=_auth_headers(other["accessToken"])
        )

    assert stream.status_code == replay.status_code == detail.status_code == job.status_code == 200
    assert forbidden.status_code == 403
    assert job.json()["data"]["status"] == "failed"
    chain = detail.json()["data"]
    assert chain["task"]["status"] == "failed"
    assert chain["task"]["completedAt"] is not None
    assert chain["reports"] == chain["evidence"] == []
    reason = chain["task"]["resultPayload"]["failureReason"]
    assert "CLS 地域和日志主题 ID" in reason
    assert all(value not in stream.text for value in ("example-topic", "example-region"))
    assert stream.text == replay.text
    events = [
        json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")
    ]
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["error"]["code"] == "SYSTEM_UNAVAILABLE"
    assert events[0]["error"]["message"] == reason


@dataclass
class FakeAiopsRunner:
    calls: list[str] = field(default_factory=lambda: [])

    async def stream(
        self,
        *,
        task: DiagnosticTaskRecord,
        accessible_knowledge_base_ids: Sequence[str],
    ) -> AsyncIterator[dict[str, object]]:
        self.calls.append(task.id)
        yield {
            "id": "evt_1",
            "type": "task.status",
            "channel": "aiops",
            "timestamp": "2026-07-10T00:00:00+00:00",
            "task": {"id": task.id, "status": "running", "message": "Planner", "progress": 10},
        }


async def test_alert_webhook_deduplicates_and_resolves_authorized_delivery(
    migrated_database_url: str, tmp_path: Path,
) -> None:
    config = tmp_path / "project.json"
    config.write_text("{}", encoding="utf-8")
    runner = FakeAiopsRunner()
    app = create_app(
        database_url=migrated_database_url,
        project_config_path=config,
        vector_store=MilvusVectorStore(settings=MilvusVectorStoreSettings()),
        aiops_diagnostic_runner=runner,
    )
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        owner = await _register(client, "webhook-oncall@example.com")
        ordinary = await _register(client, "webhook-sales@example.com")
        foreign = await _register(client, "webhook-foreign@example.com")
        token = "webhook-test-dummy-token"
        webhook_source = "test-alertmanager"
        tenants = [
            {"tenant_key": "a", "oncall_department_id": "ops", "alert_sources": [webhook_source]},
            {"tenant_key": "b", "oncall_department_id": "ops", "alert_sources": ["company-b"]},
        ]
        members = [
            {
                "user_id": user["user"]["id"], "tenant_key": tenant,
                "open_id": f"local-{index}", "department_ids": [department],
            }
            for index, (user, tenant, department) in enumerate((
                (owner, "a", "ops"), (ordinary, "a", "sales"), (foreign, "b", "ops"),
            ))
        ]
        config.write_text(json.dumps({
            "organizationAccess": {"tenants": tenants, "members": members},
            "alertDelivery": {"sources": [{
                "sourceId": webhook_source, "webhookToken": token,
                "autoDiagnosticOwnerUserId": owner["user"]["id"],
                "autoDiagnosticAlertNames": ["LocalTtsLatencyHigh"],
            }]},
        }), encoding="utf-8")
        path = f"/aiops/alerts/webhooks/{webhook_source}"
        feed = "/aiops/alerts/notifications"
        alert = {
            "status": "firing", "fingerprint": "test-cycle",
            "startsAt": "2026-11-22T16:32:00+00:00",
            "labels": {
                "alertname": "LocalTtsLatencyHigh", "service": "tts-demo", "environment": "test",
                "trace_id": "d" * 32, "undisclosed": "test-only-secret-annotation",
            },
        }
        assert (await client.post(path, json={"alerts": [alert]})).status_code == 401
        denied = await client.post(
            path, headers=_auth_headers("wrong-token"), json={"alerts": [alert]}
        )
        assert denied.status_code == 403
        responses = await asyncio.gather(*(
            client.post(path, headers=_auth_headers(token), json={"alerts": [alert]})
            for _ in range(3)
        ))
        assert all(response.status_code == 202 for response in responses)
        owner_headers = _auth_headers(owner["accessToken"])
        ordinary_feed = await client.get(feed, headers=_auth_headers(ordinary["accessToken"]))
        assert ordinary_feed.status_code == 403
        foreign_feed = await client.get(feed, headers=_auth_headers(foreign["accessToken"]))
        assert foreign_feed.json()["data"]["items"] == []
        response = await client.get(feed, headers=owner_headers)
        [first] = response.json()["data"]["items"]
        assert first["status"] == "firing"
        assert token not in response.text and "test-only-secret-annotation" not in response.text
        diagnostic_path = f"/aiops/diagnostics/{first['diagnosticId']}"
        streamed = await asyncio.wait_for(
            client.post(f"{diagnostic_path}:stream", headers=owner_headers), 15
        )
        assert streamed.status_code == 200
        jobs = (await client.get("/background-jobs", headers=owner_headers)).json()["data"]["items"]
        assert len([job for job in jobs if job["kind"] == "aiops_diagnosis"]) == 1
        assert runner.calls == [first["diagnosticId"]]

        resolved = {**alert, "status": "resolved", "startsAt": "2026-11-23T00:32:00+08:00"}
        for event in (resolved, alert, resolved):
            received = await client.post(
                path, headers=_auth_headers(token), json={"alerts": [event]}
            )
            assert received.status_code == 202
        [updated] = (await client.get(feed, headers=owner_headers)).json()["data"]["items"]
        assert updated["status"] == "resolved"
        assert updated["id"] == first["id"] and updated["diagnosticId"] == first["diagnosticId"]


async def test_organization_directory_limits_diagnosis_and_revokes_access(
    migrated_database_url: str, tmp_path: Path
) -> None:
    from super_ai.vector_store import MilvusVectorStore, MilvusVectorStoreSettings

    config_path = tmp_path / "project.json"
    config_path.write_text("{}", encoding="utf-8")
    runner = FakeAiopsRunner()
    app = create_app(
        database_url=migrated_database_url,
        project_config_path=config_path,
        vector_store=MilvusVectorStore(settings=MilvusVectorStoreSettings()),
        aiops_diagnostic_runner=runner,
    )
    app.state.llm_context_window_tokens = 4096
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        owner = await _register(client, "organization-owner@example.com")
        colleague = await _register(client, "organization-other-department@example.com")
        second_tenant = await _register(client, "organization-second-tenant@example.com")
        accounts = (owner, colleague, second_tenant)
        assert all("organization" not in account["user"] for account in accounts)
        owner_headers = _auth_headers(owner["accessToken"])
        legacy = await client.post(
            "/chat/sessions", headers=owner_headers, json={"title": "旧会话"}
        )
        legacy_id = legacy.json()["data"]["id"]
        members = [
            {
                "user_id": account["user"]["id"],
                "tenant_key": tenant,
                "open_id": f"local-{index}",
                "department_ids": [department],
            }
            for index, (account, tenant, department) in enumerate((
                (owner, "a", "oncall"),
                (colleague, "a", "sales"),
                (second_tenant, "b", "oncall"),
            ))
        ]
        tenants = [
            {"tenant_key": "a", "oncall_department_id": "oncall", "alert_sources": ["alerts-a"]},
            {"tenant_key": "b", "oncall_department_id": "oncall", "alert_sources": ["alerts-b"]},
        ]

        def write_directory() -> None:
            config_path.write_text(
                json.dumps({"organizationAccess": {"tenants": tenants, "members": members}}),
                encoding="utf-8",
            )

        write_directory()
        for account, can_diagnose in zip(accounts, (True, False, True), strict=True):
            headers = _auth_headers(account["accessToken"])
            me = await client.get("/auth/me", headers=headers)
            assert me.json()["data"]["organization"]["canDiagnose"] is can_diagnose
            assert (await client.get("/chat/sessions", headers=headers)).status_code == 200
        colleague_headers = _auth_headers(colleague["accessToken"])
        second_tenant_headers = _auth_headers(second_tenant["accessToken"])
        chat = await client.post(
            "/chat/sessions", headers=owner_headers, json={"title": "企业会话"}
        )
        assert chat.status_code == 201
        chat_path = f"/chat/sessions/{chat.json()['data']['id']}"
        assert (await client.get(chat_path, headers=colleague_headers)).status_code == 403

        bases_response = await client.get("/knowledge-bases", headers=owner_headers)
        bases = bases_response.json()["data"]["items"]
        ids = {base["name"]: base["id"] for base in bases}
        shared = f"/knowledge-bases/{ids['企业共享知识']}/documents"
        sop = f"/knowledge-bases/{ids['运维 SOP']}/documents"
        upload = {"file": ("runbook.md", b"# Shared demo reference", "text/markdown")}
        assert (await client.post(shared, headers=owner_headers, files=upload)).status_code == 201
        assert (await client.post(sop, headers=owner_headers, files=upload)).status_code == 201
        colleague_documents = await client.get(shared, headers=colleague_headers)
        assert len(colleague_documents.json()["data"]["items"]) == 1
        forbidden_upload = await client.post(shared, headers=colleague_headers, files=upload)
        assert forbidden_upload.status_code == 403
        assert (await client.get(sop, headers=colleague_headers)).status_code == 403
        assert (await client.get(shared, headers=second_tenant_headers)).status_code == 403
        assert (await client.get("/mcp/connections", headers=colleague_headers)).status_code == 403
        path = "/aiops/diagnostics"
        denied = await client.post(path, headers=colleague_headers, json={"query": "delay"})
        assert denied.status_code == 403
        denied_source = await client.post(
            path, headers=owner_headers, json={"query": "delay", "alert": {"source": "alerts-b"}}
        )
        assert denied_source.status_code == 403
        created = await client.post(path, headers=owner_headers, json={"query": "delay"})
        assert created.status_code == 202
        task = created.json()["data"]
        task_path = f"{path}/{task['id']}"
        job_path = f"/background-jobs/{task['backgroundJob']['id']}"
        for headers in (colleague_headers, second_tenant_headers):
            denied = await client.get(f"{task_path}/evidence-chain", headers=headers)
            assert denied.status_code == 403
        assert (await client.post(f"{task_path}:stream", headers=owner_headers)).status_code == 200

        members[0]["tenant_key"] = "b"
        write_directory()
        assert (await client.get(task_path, headers=owner_headers)).status_code == 403
        assert (await client.get(job_path, headers=owner_headers)).status_code == 403
        assert (await client.get(path, headers=owner_headers)).json()["data"]["items"] == []
        assert (await client.get("/chat/sessions", headers=owner_headers)).status_code == 200
        assert (await client.get(chat_path, headers=owner_headers)).status_code == 403
        assert (await client.delete(chat_path, headers=owner_headers)).status_code == 403
        legacy_path = f"/chat/sessions/{legacy_id}"
        assert (await client.get(legacy_path, headers=owner_headers)).status_code == 200


async def test_worker_rechecks_revoked_organization_membership(
    migrated_database_url: str, tmp_path: Path
) -> None:
    from super_ai.vector_store import MilvusVectorStore, MilvusVectorStoreSettings

    config_path = tmp_path / "project.json"
    config_path.write_text("{}", encoding="utf-8")
    runner = FakeAiopsRunner()
    app = create_app(
        database_url=migrated_database_url,
        project_config_path=config_path,
        vector_store=MilvusVectorStore(settings=MilvusVectorStoreSettings()),
        aiops_diagnostic_runner=runner,
    )
    repositories = app.state.memory_repositories
    await repositories.diagnostics.create_task(
        owner_user_id="formerly-authorized",
        task_id="diagnostic-revoked",
        status="accepted",
        query="Test queue delay",
        input_payload={"tenantKey": "test-tenant"},
    )
    await repositories.background_jobs.enqueue(
        owner_user_id="formerly-authorized",
        job_id="job-revoked",
        kind="aiops_diagnosis",
        resource_type="aiops_diagnostic",
        resource_id="diagnostic-revoked",
        payload={"tenantKey": "test-tenant"},
        max_attempts=1,
    )
    async with app.router.lifespan_context(app):
        for _ in range(100):
            job = await repositories.background_jobs.get(
                owner_user_id="formerly-authorized", job_id="job-revoked"
            )
            if job.status == "failed":
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("The revoked job did not reach a failed state.")
        task = await repositories.diagnostics.get_task(
            owner_user_id="formerly-authorized", task_id="diagnostic-revoked"
        )
        assert task.status == "failed"
        assert runner.calls == []


@pytest.mark.asyncio
async def test_aiops_stream_requires_task_owner(
    migrated_database_url: str, bind_oncall: Callable[[str], None]
) -> None:
    runner = FakeAiopsRunner()
    transport = httpx.ASGITransport(
        app=create_app(
            database_url=migrated_database_url,
            aiops_diagnostic_runner=cast(AiopsDiagnosticRunner, runner),
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        owner = await _register(client, "owner@example.com")
        other = await _register(client, "other@example.com")
        bind_oncall(owner["user"]["id"])
        bind_oncall(other["user"]["id"])
        create_response = await client.post(
            "/aiops/diagnostics",
            headers=_auth_headers(owner["accessToken"]),
            json={"query": "Inspect CPU", "alert": {"severity": "high"}},
        )
        diagnostic_id = create_response.json()["data"]["id"]
        denied = await client.post(
            f"/aiops/diagnostics/{diagnostic_id}:stream",
            headers=_auth_headers(other["accessToken"]),
        )
        stream = await client.post(
            f"/aiops/diagnostics/{diagnostic_id}:stream",
            headers=_auth_headers(owner["accessToken"]),
        )
        history = await client.get(
            "/aiops/diagnostics",
            headers=_auth_headers(owner["accessToken"]),
        )
        owner_chain = await client.get(
            f"/aiops/diagnostics/{diagnostic_id}/evidence-chain",
            headers=_auth_headers(owner["accessToken"]),
        )
        denied_chain = await client.get(
            f"/aiops/diagnostics/{diagnostic_id}/evidence-chain",
            headers=_auth_headers(other["accessToken"]),
        )

    assert create_response.status_code == 202
    assert denied.status_code == 403
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: task.status" in stream.text
    assert runner.calls == [diagnostic_id]
    assert history.status_code == 200
    assert [item["id"] for item in history.json()["data"]["items"]] == [diagnostic_id]
    assert owner_chain.status_code == 200
    assert owner_chain.json()["data"]["task"]["id"] == diagnostic_id
    assert denied_chain.status_code == 403


@pytest.mark.asyncio
async def test_diagnostic_stream_replays_event_committed_before_terminal_state(
    migrated_database_url: str, bind_oncall: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(database_url=migrated_database_url)
    repositories = cast(MemoryRepositories, app.state.memory_repositories)
    jobs = repositories.background_jobs
    assert jobs is not None
    original_events = jobs.list_events

    async def start_without_worker() -> None:
        return None

    async def finish_after_first_read(
        *, owner_user_id: str, job_id: str, after_sequence: int,
    ) -> list[BackgroundJobEventRecord]:
        events = await original_events(
            owner_user_id=owner_user_id, job_id=job_id, after_sequence=after_sequence,
        )
        if after_sequence == 0 and not events:
            await jobs.append_event(owner_user_id=owner_user_id, job_id=job_id, payload={
                "id": "controlled-error", "type": "error", "channel": "aiops",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": {"code": "AUTH_FORBIDDEN", "httpStatus": 403,
                          "category": "auth", "message": "Access no longer available."},
            })
            await jobs.handle_failure(
                job_id=job_id, worker_id="controlled-worker",
                error_message="Access denied", retry_at=datetime.now(timezone.utc),
            )
        return events

    monkeypatch.setattr(app.state.background_job_runtime, "start", start_without_worker)
    monkeypatch.setattr(jobs, "list_events", finish_after_first_read)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver",
    ) as client:
        owner = await _register(client, "diagnostic-replay@example.com")
        bind_oncall(owner["user"]["id"])
        headers = _auth_headers(owner["accessToken"])
        response = await client.post(
            "/aiops/diagnostics", headers=headers, json={"query": "Replay"},
        )
        assert response.status_code == 202
        now = datetime.now(timezone.utc)
        assert await jobs.claim_next(worker_id="controlled-worker", now=now,
                                     lease_expires_at=now + timedelta(seconds=20)) is not None
        diagnostic_id = response.json()["data"]["id"]
        stream = await asyncio.wait_for(
            client.post(f"/aiops/diagnostics/{diagnostic_id}:stream", headers=headers), 5,
        )

    assert stream.status_code == 200
    assert stream.text.count("AUTH_FORBIDDEN") == 1
    assert "SYSTEM_INTERNAL_ERROR" not in stream.text


@pytest.mark.asyncio
@pytest.mark.parametrize("use_organization_base", [False, True])
async def test_diagnosis_case_api_lists_only_owner_cases_and_denies_direct_access(
    migrated_database_url: str,
    bind_oncall: Callable[[str], None],
    use_organization_base: bool,
) -> None:
    app = create_app(database_url=migrated_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        owner = await _register(client, "case-owner@example.com")
        other = await _register(client, "case-other@example.com")
        bind_oncall(owner["user"]["id"])
        bind_oncall(other["user"]["id"])
        base_id = (
            case_knowledge_base_id(owner["user"]["id"], "test-tenant") if use_organization_base
            else f"kb_{owner['user']['id']}"
        )
        repositories = app.state.memory_repositories
        task = await repositories.diagnostics.create_task(
            owner_user_id=owner["user"]["id"],
            task_id="diagnostic-case-owner",
            status="succeeded",
            query="Investigate checkout timeout",
            input_payload={"tenantKey": "test-tenant"},
        )
        report = await repositories.diagnostics.add_report(
            owner_user_id=owner["user"]["id"],
            report_id="report-case-owner",
            task_id=task.id,
            title="Checkout timeout report",
            content="Evidence-backed report.",
        )
        document = await repositories.documents.create_document(
            owner_user_id=owner["user"]["id"],
            document_id="document-case-owner",
            knowledge_base_id=base_id,
            filename="checkout-case.md",
            size_bytes=22,
            mime_type="text/markdown",
            content_hash="sha256:case-owner",
            metadata={"knowledgeType": "diagnostic-case"},
        )
        index_task = await repositories.document_index_tasks.create_task(
            owner_user_id=owner["user"]["id"],
            task_id="index-case-owner",
            knowledge_base_id=document.knowledge_base_id,
            document_id=document.id,
        )
        case = await repositories.diagnostics.create_case(
            owner_user_id=owner["user"]["id"],
            case_id="case-owner",
            task_id=task.id,
            report_id=report.id,
            document_id=document.id,
            index_task_id=index_task.id,
            alert_name="CheckoutTimeout",
            service="checkout",
            keywords=["checkout", "timeout"],
            root_cause="",
            remediation="",
            summary="Evidence-backed report.",
            evidence_ids=[],
        )
        owner_list = await client.get(
            "/aiops/diagnostic-cases",
            headers=_auth_headers(owner["accessToken"]),
        )
        owner_detail = await client.get(
            f"/aiops/diagnostic-cases/{case.id}",
            headers=_auth_headers(owner["accessToken"]),
        )
        denied_detail = await client.get(
            f"/aiops/diagnostic-cases/{case.id}",
            headers=_auth_headers(other["accessToken"]),
        )

    assert owner_list.status_code == 200
    assert [item["id"] for item in owner_list.json()["data"]["items"]] == [case.id]
    assert owner_list.json()["data"]["items"][0]["knowledgeBaseId"] == base_id
    assert owner_detail.status_code == 200
    assert owner_detail.json()["data"]["id"] == case.id
    assert owner_detail.json()["data"]["knowledgeBaseId"] == base_id
    assert denied_detail.status_code == 403
    assert case.id not in denied_detail.text


@pytest.mark.asyncio
async def test_diagnostic_evidence_repository_rejects_cross_tenant_step(
    migrated_database_url: str,
) -> None:
    engine = create_memory_engine(migrated_database_url)
    try:
        repositories = create_sqlite_memory_repositories(create_memory_session_factory(engine))
        task_a = await repositories.diagnostics.create_task(
            owner_user_id="user-a",
            task_id="diagnostic-owner-a",
            status="accepted",
            query="Owner A",
        )
        task_b = await repositories.diagnostics.create_task(
            owner_user_id="user-b",
            task_id="diagnostic-owner-b",
            status="accepted",
            query="Owner B",
        )
        step_a = await repositories.diagnostics.create_step(
            owner_user_id="user-a",
            step_id="step-owner-a",
            task_id=task_a.id,
            sequence=1,
            phase="planner",
            status="completed",
        )

        with pytest.raises(TenantScopeError):
            await repositories.diagnostics.create_evidence(
                owner_user_id="user-b",
                evidence_id="evidence-cross-tenant",
                task_id=task_b.id,
                step_id=step_a.id,
                kind="ticket",
                source="ticket-system",
                summary="Must not be written.",
            )
    finally:
        await engine.dispose()


def _sop_hit(owner_user_id: str) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id="chunk_sop",
        document_id="document_sop",
        knowledge_base_id=f"kb_{owner_user_id}",
        owner_user_id=owner_user_id,
        tenant_id=owner_user_id,
        content="Check CPU saturation and query worker logs before mitigation.",
        source="worker-cpu-sop.md",
        created_at=1,
        metadata={"kind": "sop"},
        score=0.92,
    )


async def _register(client: httpx.AsyncClient, email: str) -> dict[str, Any]:
    response = await client.post(
        "/auth/register",
        json={
            "email": email,
            "displayName": email.split("@")[0],
            "password": "correct horse battery staple",
        },
    )
    return cast(dict[str, Any], response.json()["data"])


def _auth_headers(token: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _is_failed_tool_event(event: dict[str, object]) -> bool:
    tool_call = event.get("toolCall")
    if not isinstance(tool_call, Mapping):
        return False
    tool_call_payload = cast(Mapping[str, object], tool_call)
    return bool(
        event.get("type") == "tool.call" and tool_call_payload.get("status") == "failed"
    )


@pytest.fixture
def migrated_database_url(tmp_path: Path) -> str:
    database_path = tmp_path / "aiops-diagnostics.sqlite3"
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    command.upgrade(config, "head")
    return f"sqlite+aiosqlite:///{database_path}"
