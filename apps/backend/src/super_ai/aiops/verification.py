"""Immutable sandbox plans and durable approval transitions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from super_ai.aiops.local_observability import TRACE_ID, TTS_ALERT_THRESHOLD_MS
from super_ai.memory.models import (
    BackgroundJobModel,
    DiagnosticEvidenceModel,
    DiagnosticReportModel,
    DiagnosticTaskModel,
    ReportEvidenceLinkModel,
    VerificationPlanModel,
)
from super_ai.memory.repositories import DiagnosticEvidenceRecord, JsonDict
from super_ai.project_config import ProjectConfigurationError, load_project_config

VerificationTemplate = Literal["tts-queue-concurrency", "tts-synthesis-baseline"]
VerificationAction = Literal["approve", "reject", "cancel"]
VERIFICATION_TEMPLATES = ("tts-queue-concurrency", "tts-synthesis-baseline")
VERIFICATION_VERSION = 1
VERIFICATION_JOB_KIND = "sandbox_verification"
VERIFICATION_INTERPRET_KIND = "verification_interpret"
VERIFY_IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
FINAL_STATES = ("succeeded", "failed", "rejected", "expired", "cancelled")


class VerificationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenantKey: str = ""
    imageId: str = ""
    approvalSeconds: int = Field(default=1800, ge=30, le=86400)
    useAgent: bool = False
    checkpointPath: str = "var/verification-checkpoints.sqlite3"

    def allows(self, tenant_key: str) -> bool:
        return self.tenantKey == tenant_key and VERIFY_IMAGE.fullmatch(self.imageId) is not None


class VerificationConflict(Exception):
    """The persisted plan does not allow the requested transition."""


@dataclass(frozen=True, slots=True)
class VerificationCandidate:
    template: VerificationTemplate
    trace_id: str
    evidence_id: str
    queue_ms: int = 0
    synthesis_ms: int = 0
    agent_thread_id: str | None = None
    hypothesis: str | None = None
    selection_reason: str | None = None


def load_verification_settings(config_path: str | Path | None) -> VerificationSettings:
    try:
        return VerificationSettings.model_validate(
            load_project_config(config_path).get("sandboxVerification", {})
        )
    except (ValidationError, ProjectConfigurationError) as exc:
        raise ProjectConfigurationError("沙箱验证配置无法加载。") from exc


def verification_candidate(
    evidence: list[DiagnosticEvidenceRecord],
) -> VerificationCandidate | None:
    for item in reversed(evidence):
        if item.kind != "trace" or item.source != "query_traces":
            continue
        raw_output = item.payload.get("output")
        if not isinstance(raw_output, dict):
            continue
        output = cast(dict[str, object], raw_output)
        if output.get("source") != "local-tts-jsonl":
            continue
        records = output.get("records")
        if not isinstance(records, list) or not records or not isinstance(records[0], dict):
            continue
        record = cast(dict[str, object], records[0])
        trace_id = record.get("request_id")
        queue_ms, synthesis_ms = record.get("queue_ms"), record.get("synthesis_ms")
        if (
            record.get("environment") != "test"
            or not isinstance(trace_id, str)
            or TRACE_ID.fullmatch(trace_id) is None
            or not isinstance(queue_ms, int)
            or not isinstance(synthesis_ms, int)
            or max(queue_ms, synthesis_ms) < TTS_ALERT_THRESHOLD_MS
            or queue_ms == synthesis_ms
        ):
            continue
        template = (
            "tts-queue-concurrency" if queue_ms > synthesis_ms else "tts-synthesis-baseline"
        )
        return VerificationCandidate(template, trace_id, item.id, queue_ms, synthesis_ms)
    return None


class VerificationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def request(
        self,
        *,
        diagnostic_id: str,
        tenant_key: str,
        requester_id: str,
        candidate: VerificationCandidate,
        settings: VerificationSettings,
    ) -> VerificationPlanModel:
        now = datetime.now(timezone.utc)
        async with self._sessions.begin() as session:
            await session.execute(
                insert(VerificationPlanModel).values(
                    id=f"verify_{uuid4().hex}", diagnostic_id=diagnostic_id, tenant_key=tenant_key,
                    requester_id=requester_id, template=candidate.template,
                    template_version=VERIFICATION_VERSION, trace_id=candidate.trace_id,
                    evidence_id=candidate.evidence_id, image_id=settings.imageId,
                    execution_id=uuid4().hex, status="awaiting_approval",
                    expires_at=now + timedelta(seconds=settings.approvalSeconds), updated_at=now,
                    result={}, container_cleaned=False,
                    agent_thread_id=candidate.agent_thread_id, hypothesis=candidate.hypothesis,
                    selection_reason=candidate.selection_reason,
                ).on_conflict_do_nothing(index_elements=["diagnostic_id"])
            )
            row = await session.scalar(select(VerificationPlanModel).where(
                VerificationPlanModel.diagnostic_id == diagnostic_id,
                VerificationPlanModel.tenant_key == tenant_key,
                VerificationPlanModel.requester_id == requester_id,
            ))
            if row is None:
                raise VerificationConflict("当前诊断没有可访问的验证计划。")
            return row

    async def list(self, tenant_key: str) -> list[VerificationPlanModel]:
        async with self._sessions.begin() as session:
            await _expire_plans(session, tenant_key)
            result = await session.scalars(
                select(VerificationPlanModel)
                .where(VerificationPlanModel.tenant_key == tenant_key)
                .order_by(VerificationPlanModel.updated_at.desc())
                .limit(50)
            )
            return list(result)

    async def decide(
        self, *, tenant_key: str, plan_id: str, actor_id: str,
        version: int, action: VerificationAction,
    ) -> VerificationPlanModel | None:
        now = datetime.now(timezone.utc)
        async with self._sessions.begin() as session:
            await _expire_plans(session, tenant_key)
            plan = await session.scalar(select(VerificationPlanModel).where(
                VerificationPlanModel.id == plan_id,
                VerificationPlanModel.tenant_key == tenant_key,
            ))
            if plan is None:
                return None
            if plan.template_version != version:
                raise VerificationConflict("验证计划版本已变更。")
            if plan.status in FINAL_STATES:
                return plan
            if action == "approve":
                if plan.status == "awaiting_approval":
                    _approve(session, plan, actor_id, now)
                return plan
            if action == "reject":
                if plan.status != "awaiting_approval":
                    raise VerificationConflict("当前计划已开始执行。")
                plan.status = "rejected"
                plan.approval_actor_id = actor_id
                plan.decided_at = now
            else:
                plan.status = "cancelled"
                if plan.job_id is not None:
                    await _cancel_job(session, plan.job_id, now)
            plan.updated_at = now
            await _enqueue_interpretation(session, plan, now)
            return plan

    async def claim(self, plan_id: str, job_id: str) -> tuple[VerificationPlanModel | None, bool]:
        async with self._sessions.begin() as session:
            changed = await session.scalar(
                update(VerificationPlanModel)
                .where(
                    VerificationPlanModel.id == plan_id, VerificationPlanModel.job_id == job_id,
                    VerificationPlanModel.status == "queued",
                )
                .values(status="running", updated_at=datetime.now(timezone.utc))
                .returning(VerificationPlanModel.id)
            )
            plan = await session.scalar(select(VerificationPlanModel).where(
                VerificationPlanModel.id == plan_id, VerificationPlanModel.job_id == job_id,
            ))
            return plan, changed is not None

    async def finish(self, plan_id: str, *, succeeded: bool, result: JsonDict) -> None:
        now = datetime.now(timezone.utc)
        async with self._sessions.begin() as session:
            plan = await session.scalar(
                update(VerificationPlanModel)
                .where(
                    VerificationPlanModel.id == plan_id, VerificationPlanModel.status == "running",
                )
                .values(
                    status="succeeded" if succeeded else "failed", result=result, updated_at=now,
                )
                .returning(VerificationPlanModel)
            )
            if plan is not None:
                await _persist_verification_evidence(session, plan, result, now)
                await _enqueue_interpretation(session, plan, now)

    async def record_decision(
        self, *, owner_id: str, diagnostic_id: str, decision: JsonDict,
    ) -> None:
        async with self._sessions.begin() as session:
            task = await session.scalar(select(DiagnosticTaskModel).where(
                DiagnosticTaskModel.owner_user_id == owner_id,
                DiagnosticTaskModel.id == diagnostic_id,
            ))
            if task is None or "verificationDecision" in task.result_payload:
                return
            task.result_payload = {**task.result_payload, "verificationDecision": decision}
            report = await _latest_report(session, owner_id, diagnostic_id)
            if report is not None:
                report.payload = {**report.payload, "verificationDecision": decision}
                report.content += f"\n\n## 验证委派\n\n{decision.get('reason', '')}"

    async def interpreted_plan(
        self, *, plan_id: str, owner_id: str, interpretation: JsonDict | None = None,
    ) -> VerificationPlanModel | None:
        async with self._sessions.begin() as session:
            plan = await session.scalar(select(VerificationPlanModel).where(
                VerificationPlanModel.id == plan_id, VerificationPlanModel.requester_id == owner_id,
            ))
            if plan is None or plan.status not in FINAL_STATES:
                return None
            if interpretation is not None and "agentInterpretation" not in plan.result:
                plan.result = {**plan.result, "agentInterpretation": interpretation}
                plan.updated_at = datetime.now(timezone.utc)
                report = await _latest_report(session, owner_id, plan.diagnostic_id)
                if report is not None:
                    report.payload = {
                        **report.payload, "verificationInterpretation": interpretation,
                    }
                    summary = str(interpretation.get("summary", ""))
                    report.content += f"\n\n## 验证 Agent 结论\n\n{summary}"
            return plan

    async def pending_cleanup(self) -> list[str]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(VerificationPlanModel.execution_id).where(
                    VerificationPlanModel.job_id.is_not(None),
                    VerificationPlanModel.status.in_(FINAL_STATES),
                    VerificationPlanModel.container_cleaned.is_(False),
                ).limit(20)
            )
            return list(rows)

    async def cleaned(self, execution_id: str) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(VerificationPlanModel)
                .where(VerificationPlanModel.execution_id == execution_id)
                .values(container_cleaned=True)
            )


async def _expire_plans(session: AsyncSession, tenant_key: str) -> None:
    now = datetime.now(timezone.utc)
    expired = await session.scalars(
        update(VerificationPlanModel)
        .where(
            VerificationPlanModel.tenant_key == tenant_key,
            VerificationPlanModel.status == "awaiting_approval",
            VerificationPlanModel.expires_at <= now,
        )
        .values(status="expired", updated_at=now)
        .returning(VerificationPlanModel)
    )
    for plan in expired:
        await _enqueue_interpretation(session, plan, now)


def _approve(
    session: AsyncSession, plan: VerificationPlanModel, actor_id: str, now: datetime,
) -> None:
    plan.status = "queued"
    plan.approval_actor_id = actor_id
    plan.decided_at = now
    plan.updated_at = now
    plan.job_id = f"job_{uuid4().hex}"
    session.add(BackgroundJobModel(
        id=plan.job_id, owner_user_id=plan.requester_id,
        kind=VERIFICATION_JOB_KIND, resource_type=VERIFICATION_JOB_KIND,
        resource_id=plan.id, status="queued",
        payload={"planId": plan.id, "tenantKey": plan.tenant_key},
        attempt=0, max_attempts=1, timeout_seconds=75,
        available_at=now, created_at=now, updated_at=now,
    ))


async def _cancel_job(session: AsyncSession, job_id: str, now: datetime) -> None:
    job = await session.get(BackgroundJobModel, job_id)
    if job is None:
        return
    if job.status == "queued":
        job.status = "cancelled"
        job.completed_at = now
    job.cancel_requested_at = now
    job.updated_at = now


async def _persist_verification_evidence(
    session: AsyncSession, plan: VerificationPlanModel, result: JsonDict, now: datetime,
) -> None:
    evidence_id = f"verification_{plan.execution_id}"
    metadata = {
        "executionId": plan.execution_id, "template": plan.template,
        "templateVersion": plan.template_version, "imageId": plan.image_id,
        "inputEvidenceId": plan.evidence_id, "environment": "test", **result,
    }
    output = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
    session.add(DiagnosticEvidenceModel(
        id=evidence_id, owner_user_id=plan.requester_id, task_id=plan.diagnostic_id,
        kind="metric", source="docker-sandbox", summary=output, payload=metadata, created_at=now,
    ))
    await session.flush()
    report = await _latest_report(session, plan.requester_id, plan.diagnostic_id)
    if report is None:
        return
    report.content += (
        f"\n\n## 沙箱验证\n\n"
        f"测试环境执行编号 `{plan.execution_id}`，证据 `{evidence_id}`。"
        f"\n\n```json\n{output}\n```"
    )
    previous = report.payload.get("evidenceIds", [])
    previous_ids = cast(list[object], previous) if isinstance(previous, list) else []
    report.payload = {**report.payload, "evidenceIds": [*previous_ids, evidence_id]}
    session.add(ReportEvidenceLinkModel(
        id=f"link_{plan.execution_id}", owner_user_id=plan.requester_id,
        task_id=plan.diagnostic_id, report_id=report.id, evidence_id=evidence_id, created_at=now,
    ))


def verification_payload(plan: VerificationPlanModel) -> JsonDict:
    return {
        "id": plan.id, "diagnosticId": plan.diagnostic_id, "template": plan.template,
        "templateVersion": plan.template_version, "status": plan.status,
        "traceId": plan.trace_id, "evidenceId": plan.evidence_id, "imageId": plan.image_id,
        "executionId": plan.execution_id, "result": plan.result,
        "expiresAt": plan.expires_at.replace(tzinfo=timezone.utc).isoformat(),
        "updatedAt": plan.updated_at.replace(tzinfo=timezone.utc).isoformat(),
        **({"hypothesis": plan.hypothesis} if plan.hypothesis else {}),
        **({"selectionReason": plan.selection_reason} if plan.selection_reason else {}),
    }


async def _latest_report(
    session: AsyncSession, owner_id: str, diagnostic_id: str,
) -> DiagnosticReportModel | None:
    return await session.scalar(
        select(DiagnosticReportModel)
        .where(
            DiagnosticReportModel.owner_user_id == owner_id,
            DiagnosticReportModel.task_id == diagnostic_id,
        )
        .order_by(DiagnosticReportModel.created_at.desc())
        .limit(1)
    )


async def _enqueue_interpretation(
    session: AsyncSession, plan: VerificationPlanModel, now: datetime,
) -> None:
    if plan.agent_thread_id is None:
        return
    await session.execute(
        insert(BackgroundJobModel).values(
            id=f"interpret_{plan.execution_id}", owner_user_id=plan.requester_id,
            kind=VERIFICATION_INTERPRET_KIND, resource_type=VERIFICATION_INTERPRET_KIND,
            resource_id=plan.id, status="queued",
            payload={"planId": plan.id, "tenantKey": plan.tenant_key},
            attempt=0, max_attempts=3, timeout_seconds=75,
            available_at=now, created_at=now, updated_at=now,
        ).on_conflict_do_nothing(index_elements=["id"])
    )
