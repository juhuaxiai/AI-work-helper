"""Read the measurements emitted by the permitted local TTS test workload."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from super_ai.memory.repositories import JsonDict
from super_ai.organization import OrganizationAccessDenied
from super_ai.project_config import ProjectConfigurationError, load_project_config

TTS_SERVICE = "tts-demo"
TTS_ENVIRONMENT = "test"
TTS_ALERT_THRESHOLD_MS = 80
TTS_READING_WINDOW = timedelta(minutes=20)
MAX_LOG_READ = 1_048_576
TRACE_ID = re.compile(r"[0-9a-f]{32}")
LocalTool = Literal["query_traces", "search_logs", "query_metrics"]


class LocalObservabilitySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenantKey: str = ""
    logPath: str = "var/tts-observations.jsonl"
    useModel: bool = False


class TtsObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_key: str
    service: Literal["tts-demo"] = TTS_SERVICE
    environment: Literal["test"] = TTS_ENVIRONMENT
    timestamp: Annotated[datetime, AwareDatetime]
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    queue_ms: int = Field(ge=0, le=120_000)
    synthesis_ms: int = Field(ge=0, le=120_000)


@dataclass(frozen=True, slots=True)
class ObservationQuery:
    tenant_key: str
    from_at: datetime
    to_at: datetime
    trace_id: str | None

    def arguments(self) -> JsonDict:
        return {
            "service": TTS_SERVICE,
            "environment": TTS_ENVIRONMENT,
            "from": self.from_at.isoformat(),
            "to": self.to_at.isoformat(),
            **({"traceId": self.trace_id} if self.trace_id is not None else {}),
        }


class DiagnosticObservationSource(Protocol):
    async def query(self, tool: LocalTool, scope: ObservationQuery) -> JsonDict: ...


class JsonlTtsObservationSource:
    def __init__(self, path: Path, tenant_key: str) -> None:
        self._path = path
        self._tenant_key = tenant_key

    def append(self, record: TtsObservation) -> None:
        if record.tenant_key != self._tenant_key:
            raise OrganizationAccessDenied("TTS observation tenant does not match its source.")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("ab") as file:
            file.write(record.model_dump_json().encode("utf-8") + b"\n")

    async def query(self, tool: LocalTool, scope: ObservationQuery) -> JsonDict:
        if scope.tenant_key != self._tenant_key:
            raise OrganizationAccessDenied("TTS observation source is outside the task scope.")
        try:
            observations = await asyncio.to_thread(self._read, scope, 10)
        except OSError:
            raise RuntimeError("本地 TTS 观测文件不可读取。") from None
        records: list[JsonDict] = []
        for record in observations:
            event, latency = {
                "query_traces": ("tts_trace_observed", record.queue_ms + record.synthesis_ms),
                "search_logs": ("tts_queue_wait", record.queue_ms),
                "query_metrics": ("tts_synthesis_duration_ms", record.synthesis_ms),
            }[tool]
            records.append({
                "timestamp": record.timestamp.isoformat(),
                "service": record.service,
                "environment": record.environment,
                "request_id": record.trace_id,
                "event": event,
                "latency_ms": latency,
                "queue_ms": record.queue_ms,
                "synthesis_ms": record.synthesis_ms,
            })
        return {"recordCount": len(records), "records": records, "source": "local-tts-jsonl"}

    async def read_for_export(self, scope: ObservationQuery, limit: int) -> list[TtsObservation]:
        if scope.tenant_key != self._tenant_key:
            raise OrganizationAccessDenied("TTS observation source is outside the task scope.")
        return await asyncio.to_thread(self._read, scope, limit)

    def _read(self, scope: ObservationQuery, limit: int) -> list[TtsObservation]:
        try:
            with self._path.open("rb") as file:
                length = file.seek(0, 2)
                start = max(0, length - MAX_LOG_READ)
                file.seek(start)
                lines = file.read(MAX_LOG_READ).splitlines()
        except FileNotFoundError:
            return []
        matches: list[TtsObservation] = []
        for line in reversed(lines[1:] if start else lines):
            try:
                record = TtsObservation.model_validate_json(line)
            except ValidationError:
                continue
            if (
                record.tenant_key == scope.tenant_key
                and scope.from_at <= record.timestamp <= scope.to_at
                and (scope.trace_id is None or record.trace_id == scope.trace_id)
            ):
                matches.append(record)
                if len(matches) == limit:
                    break
        return matches


def observation_query(
    tenant_key: str, alert: Mapping[str, object], *, now: datetime,
) -> ObservationQuery:
    service = alert.get("service", TTS_SERVICE)
    labels = alert.get("labels")
    environment = alert.get("environment", TTS_ENVIRONMENT)
    trace_id = alert.get("traceId")
    if isinstance(labels, Mapping):
        scoped_labels = cast(Mapping[str, object], labels)
        environment = scoped_labels.get("environment", environment)
        trace_id = scoped_labels.get("trace_id", trace_id)
    if service != TTS_SERVICE or environment != TTS_ENVIRONMENT:
        raise OrganizationAccessDenied("本地观测源仅提供 tts-demo 测试环境的数据。")
    if trace_id is not None and (
        not isinstance(trace_id, str) or TRACE_ID.fullmatch(trace_id) is None
    ):
        raise OrganizationAccessDenied("本地观测 Trace ID 格式无效。")
    return ObservationQuery(tenant_key, now - TTS_READING_WINDOW, now, trace_id)


def load_local_observability(config_path: Path | str | None) -> LocalObservabilitySettings:
    raw = load_project_config(config_path).get("localObservability", {})
    try:
        return LocalObservabilitySettings.model_validate(raw)
    except ValidationError as exc:
        raise ProjectConfigurationError("本地 TTS 观测配置无效。") from exc


def build_local_source(settings: LocalObservabilitySettings) -> JsonlTtsObservationSource:
    backend_root = Path(__file__).resolve().parents[3]
    return JsonlTtsObservationSource(backend_root / settings.logPath, settings.tenantKey)
