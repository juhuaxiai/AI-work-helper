"""Durable, offline statistics for authorized local diagnostic log snapshots."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from super_ai.aiops.docker_verifier import DockerVerificationError, DockerVerificationExecutor
from super_ai.aiops.log_exports import DiagnosticLogExportRepository
from super_ai.aiops.verification import load_verification_settings
from super_ai.jobs import BackgroundJobContext, JobCancelled
from super_ai.memory.models import (
    BackgroundJobModel,
    DiagnosticEvidenceModel,
    DiagnosticLogExportModel,
    DiagnosticReportModel,
    ReportEvidenceLinkModel,
    utc_now,
)
from super_ai.memory.repositories import BackgroundJobRecord, BackgroundJobRepository, JsonDict
from super_ai.organization import load_organization_directory

LOG_STATISTICS_JOB_KIND = "sandbox_log_statistics"


async def queue_log_statistics(
    jobs: BackgroundJobRepository,
    export: DiagnosticLogExportModel,
    image_id: str,
) -> BackgroundJobRecord:
    export_key = export.id.removeprefix("export_")
    job_id = f"job_stats_{export_key}"
    existing = await jobs.get(owner_user_id=export.owner_user_id, job_id=job_id)
    if existing is not None:
        return existing
    try:
        return await jobs.enqueue(
            owner_user_id=export.owner_user_id,
            job_id=job_id,
            kind=LOG_STATISTICS_JOB_KIND,
            resource_type=LOG_STATISTICS_JOB_KIND,
            resource_id=export.id,
            payload={"tenantKey": export.tenant_key, "taskId": export.task_id, "imageId": image_id},
            max_attempts=2,
            timeout_seconds=45,
        )
    except IntegrityError:
        reused = await jobs.get(owner_user_id=export.owner_user_id, job_id=job_id)
        if reused is None:
            raise
        return reused


def log_statistics_job_handler(
    sessions: async_sessionmaker[AsyncSession],
    executor: DockerVerificationExecutor,
    config_path: str | Path | None,
) -> Callable[[BackgroundJobContext], Awaitable[None]]:
    exports = DiagnosticLogExportRepository(sessions)

    async def handle(context: BackgroundJobContext) -> None:
        job = context.job
        tenant, task_id, image_id = (
            job.payload.get(key) for key in ("tenantKey", "taskId", "imageId")
        )
        if (
            not isinstance(tenant, str)
            or not isinstance(task_id, str)
            or not isinstance(image_id, str)
        ):
            raise DockerVerificationError("日志统计任务的范围无效。")

        async def authorize() -> None:
            await context.raise_if_cancelled()
            current = await context.repository.get(owner_user_id=job.owner_user_id, job_id=job.id)
            if (
                current is None
                or current.status != "running"
                or current.lease_owner != job.lease_owner
            ):
                raise JobCancelled("The log statistics worker no longer holds the lease.")
            member = load_organization_directory(config_path).resolve(job.owner_user_id)
            settings = load_verification_settings(config_path)
            if (
                member is None
                or not member.may_access_diagnosis(tenant)
                or not settings.allows(tenant)
                or settings.imageId != image_id
            ):
                raise DockerVerificationError("日志统计的组织权限或镜像配置已变更。")

        await authorize()
        export = await exports.get(
            export_id=job.resource_id,
            task_id=task_id,
            owner_user_id=job.owner_user_id,
            tenant_key=tenant,
        )
        if export is None:
            raise DockerVerificationError("日志快照已过期或不可用。")
        result = await executor.analyze_log_snapshot(
            execution_id=export.id.removeprefix("export_"),
            image_id=image_id,
            snapshot=export.content,
            record_count=export.record_count,
        )
        await authorize()
        await _publish_statistics(sessions, job, export, result)

    return handle


async def _publish_statistics(
    sessions: async_sessionmaker[AsyncSession],
    job: BackgroundJobRecord,
    export: DiagnosticLogExportModel,
    result: JsonDict,
) -> None:
    now = utc_now()
    key = export.id.removeprefix("export_")
    evidence_id = f"log_stats_{key}"
    payload = {"exportId": export.id, "environment": "test", "traceId": export.trace_id, **result}
    summary = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    async with sessions.begin() as session:
        finished = await session.scalar(
            update(BackgroundJobModel)
            .where(
                BackgroundJobModel.id == job.id,
                BackgroundJobModel.lease_owner == job.lease_owner,
                BackgroundJobModel.status == "running",
                BackgroundJobModel.cancel_requested_at.is_(None),
            )
            .values(
                status="succeeded",
                completed_at=now,
                updated_at=now,
                lease_owner=None,
                lease_expires_at=None,
            )
            .returning(BackgroundJobModel.id)
        )
        if finished is None:
            raise JobCancelled("The log statistics job was cancelled or reassigned.")
        referenced = await session.scalar(
            select(DiagnosticLogExportModel.id).where(
                DiagnosticLogExportModel.id == export.id,
                DiagnosticLogExportModel.expires_at > now,
            )
        )
        if referenced is None:
            raise DockerVerificationError("日志快照已过期或不可用。")
        session.add(
            DiagnosticEvidenceModel(
                id=evidence_id,
                owner_user_id=export.owner_user_id,
                task_id=export.task_id,
                kind="metric",
                source="docker-log-statistics",
                summary=summary,
                payload=payload,
                created_at=now,
            )
        )
        report = await session.scalar(
            select(DiagnosticReportModel)
            .where(
                DiagnosticReportModel.owner_user_id == export.owner_user_id,
                DiagnosticReportModel.task_id == export.task_id,
            )
            .order_by(DiagnosticReportModel.created_at.desc(), DiagnosticReportModel.id.desc())
            .limit(1)
        )
        if report is not None:
            old = report.payload.get("evidenceIds")
            ids = cast(list[object], old) if isinstance(old, list) else []
            report.payload = {**report.payload, "evidenceIds": [*ids, evidence_id]}
            report.content += (
                f"\n\n## 沙箱日志统计\n\n本地测试环境，证据 `{evidence_id}`。"
                f"\n\n```json\n{summary}\n```"
            )
            await session.flush()
            session.add(
                ReportEvidenceLinkModel(
                    id=f"stats_link_{key}",
                    owner_user_id=export.owner_user_id,
                    task_id=export.task_id,
                    report_id=report.id,
                    evidence_id=evidence_id,
                    created_at=now,
                )
            )
