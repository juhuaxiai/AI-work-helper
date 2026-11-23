"""Create scoped, redacted snapshots of the local TTS measurements."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from super_ai.aiops.local_observability import (
    TRACE_ID,
    TTS_ENVIRONMENT,
    TTS_READING_WINDOW,
    TTS_SERVICE,
    JsonlTtsObservationSource,
    ObservationQuery,
)
from super_ai.memory.models import DiagnosticLogExportModel, utc_now
from super_ai.memory.repositories import DiagnosticEvidenceRecord, JsonDict

MAX_EXPORT_RECORDS = 500
MAX_EXPORT_BYTES = 128 * 1024
EXPORT_LIFETIME = timedelta(minutes=10)


class LogExportUnavailable(RuntimeError):
    """The diagnostic has no exportable local observations."""


def local_evidence_scope(
    tenant_key: str, evidence: list[DiagnosticEvidenceRecord]
) -> ObservationQuery:
    for item in evidence:
        raw_arguments = item.payload.get("arguments")
        raw_output = item.payload.get("output")
        if not isinstance(raw_output, dict) or not isinstance(raw_arguments, dict):
            continue
        output = cast(JsonDict, raw_output)
        arguments = cast(JsonDict, raw_arguments)
        if output.get("source") != "local-tts-jsonl" or item.source != "query_traces":
            continue
        if (
            arguments.get("service") != TTS_SERVICE
            or arguments.get("environment") != TTS_ENVIRONMENT
        ):
            continue
        start, end, trace = arguments.get("from"), arguments.get("to"), arguments.get("traceId")
        if not isinstance(start, str) or not isinstance(end, str):
            continue
        if trace is not None and (not isinstance(trace, str) or TRACE_ID.fullmatch(trace) is None):
            continue
        try:
            from_at, to_at = datetime.fromisoformat(start), datetime.fromisoformat(end)
        except ValueError:
            continue
        if from_at.tzinfo is None or to_at.tzinfo is None:
            continue
        if timedelta(0) <= to_at - from_at <= TTS_READING_WINDOW:
            return ObservationQuery(tenant_key, from_at, to_at, trace)
    raise LogExportUnavailable("当前诊断没有可导出的本地测试日志范围。")


class DiagnosticLogExportRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self,
        *,
        owner_user_id: str,
        task_id: str,
        scope: ObservationQuery,
        source: JsonlTtsObservationSource,
    ) -> DiagnosticLogExportModel:
        selected = select(DiagnosticLogExportModel).where(
            DiagnosticLogExportModel.task_id == task_id,
            DiagnosticLogExportModel.owner_user_id == owner_user_id,
            DiagnosticLogExportModel.tenant_key == scope.tenant_key,
        )
        async with self._sessions() as session:
            existing = await session.scalar(
                selected.where(DiagnosticLogExportModel.expires_at > utc_now())
            )
        if existing is not None:
            return existing

        records = await source.read_for_export(scope, MAX_EXPORT_RECORDS)
        content = bytearray()
        count = 0
        for record in records:
            line = record.model_dump_json(exclude={"tenant_key"}).encode("utf-8") + b"\n"
            if len(content) + len(line) > MAX_EXPORT_BYTES:
                break
            content.extend(line)
            count += 1
        if not count:
            raise LogExportUnavailable("当前范围内没有可导出的本地测试日志。")

        now = utc_now()
        async with self._sessions.begin() as session:
            await session.execute(
                delete(DiagnosticLogExportModel).where(
                    DiagnosticLogExportModel.expires_at <= now,
                )
            )
            await session.execute(
                insert(DiagnosticLogExportModel)
                .values(
                    id=f"export_{uuid4().hex}",
                    task_id=task_id,
                    owner_user_id=owner_user_id,
                    tenant_key=scope.tenant_key,
                    from_at=scope.from_at.astimezone(timezone.utc),
                    to_at=scope.to_at.astimezone(timezone.utc),
                    trace_id=scope.trace_id,
                    content=bytes(content),
                    record_count=count,
                    expires_at=now + EXPORT_LIFETIME,
                )
                .on_conflict_do_nothing(index_elements=["task_id"])
            )
            model = await session.scalar(selected)
            if model is None:
                raise LogExportUnavailable("此诊断的测试日志导出不可用。")
            return model

    async def get(
        self,
        *,
        export_id: str,
        task_id: str,
        owner_user_id: str,
        tenant_key: str,
    ) -> DiagnosticLogExportModel | None:
        query = select(DiagnosticLogExportModel).where(
            DiagnosticLogExportModel.id == export_id,
            DiagnosticLogExportModel.task_id == task_id,
            DiagnosticLogExportModel.owner_user_id == owner_user_id,
            DiagnosticLogExportModel.tenant_key == tenant_key,
            DiagnosticLogExportModel.expires_at > utc_now(),
        )
        async with self._sessions() as session:
            return await session.scalar(query)


def export_payload(export: DiagnosticLogExportModel) -> JsonDict:
    return {
        "id": export.id,
        "diagnosticId": export.task_id,
        "recordCount": export.record_count,
        "sizeBytes": len(export.content),
        "from": _utc_iso(export.from_at),
        "to": _utc_iso(export.to_at),
        "traceId": export.trace_id,
        "expiresAt": _utc_iso(export.expires_at),
        "downloadPath": f"/aiops/diagnostics/{export.task_id}/log-exports/{export.id}",
    }


def _utc_iso(value: datetime) -> str:
    return value.replace(tzinfo=timezone.utc).isoformat()
