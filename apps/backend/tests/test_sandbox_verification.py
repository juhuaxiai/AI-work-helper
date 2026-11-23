from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from os import environ
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI

from super_ai.aiops.docker_verifier import DockerVerificationExecutor
from super_ai.aiops.log_statistics import LOG_STATISTICS_JOB_KIND, log_statistics_job_handler
from super_ai.aiops.sandbox_worker import sandbox_job_handler
from super_ai.aiops.tts_demo import create_app as create_tts_app
from super_ai.aiops.verification import (
    VERIFICATION_JOB_KIND,
    VerificationRepository,
)
from super_ai.api.app import create_app
from super_ai.jobs import BackgroundJobRuntime
from super_ai.llm import LlmProvider
from super_ai.memory.models import VerificationPlanModel
from super_ai.memory.repositories import JsonDict, MemoryRepositories
from super_ai.vector_store import MilvusVectorStore, MilvusVectorStoreSettings


async def _check_log_export(
    app: FastAPI, api: httpx.AsyncClient, diagnostic_path: str, trace_id: str,
    headers: dict[str, str], denied: list[dict[str, str]],
) -> None:
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from super_ai.memory.models import DiagnosticLogExportModel

    route = f"{diagnostic_path}/log-exports"
    created = await api.post(route, headers=headers)
    assert created.status_code == 201
    reference = created.json()["data"]
    assert reference["traceId"] == trace_id
    assert reference["recordCount"] == 1
    assert (await api.post(route, headers=headers)).json()["data"]["id"] == reference["id"]
    download = await api.get(reference["downloadPath"], headers=headers)
    assert download.status_code == 200
    assert download.headers["Content-Type"] == "application/x-ndjson"
    assert download.headers["Cache-Control"] == "no-store"
    assert len(download.content) == reference["sizeBytes"]
    [row] = [json.loads(line) for line in download.text.splitlines()]
    assert set(row) == {
        "timestamp", "trace_id", "queue_ms", "synthesis_ms", "service", "environment",
    }
    assert row["trace_id"] == trace_id and row["queue_ms"] >= 80
    for denied_headers in denied:
        assert (await api.get(reference["downloadPath"], headers=denied_headers)).status_code == 403
        assert (await api.post(route, headers=denied_headers)).status_code == 403

    async with app.state.memory_session_factory.begin() as session:
        await session.execute(update(DiagnosticLogExportModel).values(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
    assert (await api.get(reference["downloadPath"], headers=headers)).status_code == 404
    expired_analysis = await api.post(f"{reference['downloadPath']}:analyze", headers=headers)
    assert expired_analysis.status_code == 404
    chain = (await api.get(f"{diagnostic_path}/evidence-chain", headers=headers)).json()["data"]
    relevant = [
        audit for audit in chain["toolCalls"]
        if audit["arguments"].get("exportId") == reference["id"]
    ]
    outcomes = {(audit["toolName"], audit["status"]) for audit in relevant}
    assert outcomes == {
        ("export_local_logs", "completed"), ("read_local_log_export", "completed"),
        ("read_local_log_export", "failed"),
    }


class LogStatisticsExecutor(DockerVerificationExecutor):
    def __init__(self, *, real: bool, pending_cancel: bool) -> None:
        self.real = real
        self.pending_cancel = pending_cancel
        self.executions = 0
        self.started = asyncio.Event()
        self.released = asyncio.Event()

    async def analyze_log_snapshot(
        self, *, execution_id: str, image_id: str, snapshot: bytes, record_count: int,
    ) -> JsonDict:
        self.executions += 1
        self.started.set()
        if self.pending_cancel:
            await self.released.wait()
        if self.real and not self.pending_cancel:
            result = await super().analyze_log_snapshot(
                execution_id=execution_id, image_id=image_id, snapshot=snapshot,
                record_count=record_count,
            )
            assert await self._inspect(execution_id) is None
            return result
        record = json.loads(snapshot.splitlines()[0])
        return {
            "template": "tts-log-statistics", "samples": 1, "ignored": 0,
            "queue": dict.fromkeys(("meanMs", "p95Ms", "maxMs"), record["queue_ms"]),
            "synthesis": dict.fromkeys(("meanMs", "p95Ms", "maxMs"), record["synthesis_ms"]),
        }


async def _check_log_analysis(
    app: FastAPI, api: httpx.AsyncClient, *, diagnostic_path: str, config_path: Path,
    headers: dict[str, str], denied_headers: dict[str, str], real: bool, cancel: bool,
) -> None:
    created = await api.post(f"{diagnostic_path}/log-exports", headers=headers)
    assert created.status_code == 201
    export = created.json()["data"]
    snapshot = (await api.get(export["downloadPath"], headers=headers)).json()
    route = f"{export['downloadPath']}:analyze"
    assert (await api.post(route, headers=denied_headers)).status_code == 403
    first, second = await asyncio.gather(*(api.post(route, headers=headers) for _ in range(2)))
    assert first.status_code == second.status_code == 202
    job = first.json()["data"]
    assert job["id"] == second.json()["data"]["id"]
    job_route = f"/background-jobs/{job['id']}"
    assert (await api.get(job_route, headers=denied_headers)).status_code == 403

    repositories = cast(MemoryRepositories, app.state.memory_repositories)
    assert repositories.background_jobs is not None
    runtime = BackgroundJobRuntime(repositories.background_jobs, poll_seconds=.05)
    stats = LogStatisticsExecutor(real=real, pending_cancel=cancel)
    runtime.register(LOG_STATISTICS_JOB_KIND, log_statistics_job_handler(
        app.state.memory_session_factory, stats, config_path,
    ))
    await runtime.start()
    try:
        if cancel:
            await asyncio.wait_for(stats.started.wait(), 10)
            assert (await api.post(f"{job_route}:cancel", headers=headers)).status_code == 200
            stats.released.set()
        for _ in range(100):
            response = await api.get(job_route, headers=headers)
            job = response.json()["data"]
            if job["status"] in ("succeeded", "cancelled", "failed"):
                break
            await asyncio.sleep(.08)
        assert job["status"] == ("cancelled" if cancel else "succeeded"), job.get("errorMessage")
        assert stats.executions == 1
        chain = await api.get(f"{diagnostic_path}/evidence-chain", headers=headers)
        results = chain.json()["data"]
        records = [
            item for item in results["evidence"] if item["source"] == "docker-log-statistics"
        ]
        if cancel:
            assert records == []
        else:
            [record] = records
            assert record["payload"]["exportId"] == export["id"]
            assert record["payload"]["queue"]["p95Ms"] == snapshot["queue_ms"]
            assert record["id"] in [link["evidenceId"] for link in results["reportEvidenceLinks"]]
    finally:
        await runtime.stop()


class RecordingExecutor(DockerVerificationExecutor):
    def __init__(
        self, *, real: bool, wait_for_cancel: bool = False, reproduced: bool = True,
    ) -> None:
        self.real = real
        self.wait_for_cancel = wait_for_cancel
        self.reproduced = reproduced
        self.started = asyncio.Event()
        self.executions: list[str] = []
        self.cleaned: list[str] = []

    async def execute(
        self, plan: VerificationPlanModel, *, may_create: bool,
        authorize: Callable[[], Awaitable[None]],
    ) -> JsonDict:
        self.executions.append(plan.execution_id)
        if not self.real:
            await authorize()
            self.started.set()
            while self.wait_for_cancel:
                await asyncio.sleep(.03)
                await authorize()
            return {
                "controlMs": 250 if self.reproduced else 90,
                "comparisonMs": 80, "exitCode": 0,
                "outcome": "supported" if self.reproduced else "not_reproduced",
            }
        output = await super().execute(plan, may_create=may_create, authorize=authorize)
        inspection = await self._docker("inspect", f"agent-py-verification-{plan.execution_id}")
        container = json.loads(inspection or "")[0]
        isolation = container["HostConfig"]
        assert isolation["NetworkMode"] == "none"
        assert isolation["ReadonlyRootfs"] is True
        assert isolation["Memory"] == 96 * 1024 * 1024
        assert isolation["NanoCpus"] == 500_000_000
        assert isolation["PidsLimit"] == 32
        assert isolation["CapDrop"] == ["ALL"]
        assert container["Config"]["User"] == "65534:65534"
        assert all(mount["Type"] == "tmpfs" for mount in container["Mounts"])
        assert await super().execute(plan, may_create=False, authorize=authorize) == output
        return output

    async def cleanup(self, execution_id: str) -> None:
        if self.real:
            await super().cleanup(execution_id)
            assert await self._inspect(execution_id) is None
        self.cleaned.append(execution_id)


class TestVerificationModel:
    __test__ = False

    def __init__(self, *, incompatible: bool = False) -> None:
        self.incompatible = incompatible
        self.calls: list[str] = []

    def create_chat_model(self) -> TestVerificationModel:
        return self

    async def ainvoke(self, input: object) -> str:
        messages = cast(list[tuple[str, str]], input)
        fixture = json.loads(messages[1][1])
        if "eligibleTemplate" in fixture:
            self.calls.append("choose")
            template = (
                "tts-synthesis-baseline" if self.incompatible else fixture["eligibleTemplate"]
            )
            return json.dumps({
                "decision": "propose",
                "template": template,
                "hypothesis": "并发提高会降低排队时间。",
                "reason": "排队阶段持续时间高于合成阶段。",
            })
        self.calls.append("interpret")
        summary = "结果支持假设。" if fixture["outcome"] == "supported" else "本次未复现假设。"
        return json.dumps({
            "outcome": fixture["outcome"],
            "summary": summary,
        })


@pytest.mark.parametrize(
    "action",
    ["approve", "reject", "expire", "cancel", "cancel-running",
     "agent-approve", "agent-reject", "agent-illegal", "agent-normal", "agent-not-reproduced",
     "log-export", "log-analysis", "log-analysis-cancel"],
)
async def test_persisted_sandbox_plan_and_worker(
    tmp_path: Path, action: str,
) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'sandbox.sqlite3').as_posix()}"
    migration = Config("alembic.ini")
    migration.set_main_option("sqlalchemy.url", database_url)
    await asyncio.to_thread(command.upgrade, migration, "head")
    test_image = environ.get("AGENT_PY_TEST_DOCKER_IMAGE")
    use_agent = action.startswith("agent-")
    model = TestVerificationModel(incompatible=action == "agent-illegal")
    config_path = tmp_path / "project.json"
    config: dict[str, Any] = {
        "localObservability": {
            "tenantKey": "sandbox-tenant", "logPath": str(tmp_path / "tts.jsonl"),
        },
        "sandboxVerification": {
            "tenantKey": "sandbox-tenant", "imageId": test_image or ("sha256:" + "a" * 64),
            "useAgent": use_agent, "checkpointPath": str(tmp_path / "checkpoints.sqlite3"),
        },
    }

    def save_config() -> None:
        config_path.write_text(json.dumps(config), encoding="utf-8")

    def new_app() -> FastAPI:
        return create_app(
            database_url=database_url, project_config_path=config_path,
            vector_store=MilvusVectorStore(settings=MilvusVectorStoreSettings()),
            llm_provider=cast(LlmProvider, model) if use_agent else None,
        )

    save_config()
    demo_app = create_tts_app(config_path=config_path)
    async with _client(demo_app) as demo:
        if action == "log-export":
            assert (await demo.post("/simulate/normal")).status_code == 200
        simulation = "normal" if action == "agent-normal" else "queue-slow"
        data = (await demo.post(f"/simulate/{simulation}")).json()
    first_app = new_app()
    async with first_app.router.lifespan_context(first_app), _client(first_app) as api:
        owner = await _register(api, "owner")
        sales = await _register(api, "sales")
        other_tenant = await _register(api, "otherops")
        config["organizationAccess"] = {
            "tenants": [
                {"tenant_key": "sandbox-tenant", "oncall_department_id": "ops"},
                {"tenant_key": "different", "oncall_department_id": "ops"},
            ],
            "members": [
                {
                    "user_id": user["user"]["id"], "tenant_key": tenant,
                    "open_id": f"test-{index}", "department_ids": [department],
                }
                for index, (user, tenant, department) in enumerate((
                    (owner, "sandbox-tenant", "ops"), (sales, "sandbox-tenant", "sales"),
                    (other_tenant, "different", "ops"),
                ))
            ],
        }
        save_config()
        owner_headers = _headers(owner)
        accepted = await api.post("/aiops/diagnostics", headers=owner_headers, json={
            "query": "TTS 排队故障", "alert": {"traceId": data["trace_id"]},
        })
        assert accepted.status_code == 202
        diagnostic_path = f"/aiops/diagnostics/{accepted.json()['data']['id']}"
        stream = await api.post(f"{diagnostic_path}:stream", headers=owner_headers)
        assert stream.status_code == 200
        if action == "log-export":
            await _check_log_export(
                first_app, api, diagnostic_path, data["trace_id"], owner_headers,
                [_headers(sales), _headers(other_tenant)],
            )
            return
        if action.startswith("log-analysis"):
            await _check_log_analysis(
                first_app, api, diagnostic_path=diagnostic_path, config_path=config_path,
                headers=owner_headers, denied_headers=_headers(sales), real=test_image is not None,
                cancel=action == "log-analysis-cancel",
            )
            return
        if use_agent:
            response = await api.get("/aiops/verifications", headers=owner_headers)
            automatic_plans = response.json()["data"]["items"]
            if action in ("agent-illegal", "agent-normal"):
                assert automatic_plans == []
                detail = await api.get(diagnostic_path, headers=owner_headers)
                decision = detail.json()["data"]["resultPayload"]["verificationDecision"]
                assert decision["decision"] == "skip"
                assert model.calls == ([] if action == "agent-normal" else ["choose"])
                return
            assert automatic_plans[0]["hypothesis"] == "并发提高会降低排队时间。"
            assert Path(config["sandboxVerification"]["checkpointPath"]).exists()
        requested = await api.post(f"{diagnostic_path}/verification", headers=owner_headers)
        assert requested.status_code == 202
        plan = requested.json()["data"]
        assert plan["status"] == "awaiting_approval"

    app = new_app()
    repositories = cast(MemoryRepositories, app.state.memory_repositories)
    jobs = repositories.background_jobs
    assert jobs is not None
    if action == "expire":
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import update

        async with app.state.memory_session_factory.begin() as session:
            await session.execute(update(VerificationPlanModel).values(
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
            ))
    async with app.router.lifespan_context(app), _client(app) as api:
        restored = await api.get("/aiops/verifications", headers=owner_headers)
        [restored_plan] = restored.json()["data"]["items"]
        assert restored_plan["id"] == plan["id"]
        assert await jobs.find_for_resource(
            owner_user_id=owner["user"]["id"], resource_type=VERIFICATION_JOB_KIND,
            resource_id=plan["id"],
        ) is None
        target = f"/aiops/verifications/{plan['id']}:decide"
        body = {"action": "approve", "templateVersion": plan["templateVersion"]}
        for denied in (sales, other_tenant):
            response = await api.post(target, headers=_headers(denied), json=body)
            assert response.status_code == 403
        mismatch = await api.post(
            target, headers=owner_headers, json={**body, "templateVersion": 2},
        )
        assert mismatch.status_code == 409
        if action in ("reject", "expire", "agent-reject"):
            expected = "expired" if action == "expire" else "rejected"
            decision = await api.post(
                target, headers=owner_headers,
                json={**body, "action": "reject" if expected == "rejected" else "approve"},
            )
            assert decision.json()["data"]["status"] == expected
            assert await jobs.find_for_resource(
                owner_user_id=owner["user"]["id"], resource_type=VERIFICATION_JOB_KIND,
                resource_id=plan["id"],
            ) is None
            if use_agent:
                interpreted = await _wait_for_interpretation(api, owner_headers)
                assert interpreted["outcome"] == "rejected"
                assert model.calls == ["choose"]
            return

        replies = await asyncio.gather(*(
            api.post(target, headers=owner_headers, json=body) for _ in range(3)
        ))
        assert all(reply.json()["data"]["executionId"] == plan["executionId"] for reply in replies)
        assert all(reply.json()["data"]["status"] == "queued" for reply in replies)
        persisted = await jobs.list(owner_user_id=owner["user"]["id"])
        assert len([job for job in persisted if job.kind == VERIFICATION_JOB_KIND]) == 1
        if action == "cancel":
            cancelled = await api.post(
                target, headers=owner_headers, json={**body, "action": "cancel"},
            )
            assert cancelled.json()["data"]["status"] == "cancelled"
            persisted = await jobs.list(owner_user_id=owner["user"]["id"])
            verification_job = next(job for job in persisted if job.kind == VERIFICATION_JOB_KIND)
            assert verification_job.status == "cancelled"
            return

        executor = RecordingExecutor(
            real=test_image is not None and action in ("approve", "agent-approve"),
            wait_for_cancel=action == "cancel-running",
            reproduced=action != "agent-not-reproduced",
        )
        verification = VerificationRepository(app.state.memory_session_factory)
        worker = BackgroundJobRuntime(jobs, concurrency=1, poll_seconds=.05)
        handler = sandbox_job_handler(verification, executor, config_path)
        worker.register(VERIFICATION_JOB_KIND, handler)
        await worker.start()
        try:
            if action == "cancel-running":
                await asyncio.wait_for(executor.started.wait(), 3)
                cancelled = await api.post(
                    target, headers=owner_headers, json={**body, "action": "cancel"},
                )
                assert cancelled.json()["data"]["status"] == "cancelled"
                for _ in range(30):
                    if executor.cleaned:
                        break
                    await asyncio.sleep(.1)
                assert executor.cleaned == [plan["executionId"]]
                return
            for _ in range(120):
                listed = await api.get("/aiops/verifications", headers=owner_headers)
                [result] = listed.json()["data"]["items"]
                if result["status"] == "succeeded" and executor.cleaned:
                    break
                if result["status"] == "failed":
                    pytest.fail(str(result["result"]))
                await asyncio.sleep(.1)
            else:
                pytest.fail("Verification did not complete and clean its workspace.")
            assert result["result"]["controlMs"] > result["result"]["comparisonMs"]
            assert executor.executions == executor.cleaned == [plan["executionId"]]
            if use_agent:
                interpreted = await _wait_for_interpretation(api, owner_headers)
                expected = "not_reproduced" if action == "agent-not-reproduced" else "supported"
                assert interpreted["outcome"] == expected
                assert model.calls == ["choose", "interpret"]
            detail = await api.get(f"{diagnostic_path}/evidence-chain", headers=owner_headers)
            chain = detail.json()["data"]
            [sandbox] = [item for item in chain["evidence"] if item["source"] == "docker-sandbox"]
            assert sandbox["payload"]["inputEvidenceId"] == plan["evidenceId"]
            assert sandbox["id"] in chain["reports"][0]["evidenceIds"]
            assert "沙箱验证" in chain["reports"][0]["content"]
            if use_agent:
                assert "验证 Agent 结论" in chain["reports"][0]["content"]
        finally:
            await worker.stop()


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _wait_for_interpretation(
    api: httpx.AsyncClient, headers: dict[str, str],
) -> JsonDict:
    for _ in range(50):
        listed = await api.get("/aiops/verifications", headers=headers)
        [item] = listed.json()["data"]["items"]
        if "agentInterpretation" in item["result"]:
            return cast(JsonDict, item["result"]["agentInterpretation"])
        await asyncio.sleep(.1)
    pytest.fail("The persisted verification graph did not resume.")


async def _register(client: httpx.AsyncClient, name: str) -> dict[str, Any]:
    response = await client.post("/auth/register", json={
        "email": f"{name}@example.com", "displayName": name, "password": "Local-test-password",
    })
    response.raise_for_status()
    return cast(dict[str, Any], response.json()["data"])


def _headers(account: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {account['accessToken']}"}
