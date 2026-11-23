"""Authenticated Alertmanager input and atomic notification persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
)
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from super_ai.aiops.local_observability import TTS_ENVIRONMENT, TTS_SERVICE
from super_ai.memory.models import AlertNotificationModel, BackgroundJobModel, DiagnosticTaskModel
from super_ai.memory.repositories import JsonDict
from super_ai.organization import OrganizationDirectory, OrganizationMember
from super_ai.project_config import ProjectConfigurationError, load_project_config


class AlertmanagerAlert(BaseModel):
    status: Literal["firing", "resolved"]
    starts_at: Annotated[datetime, AwareDatetime] = Field(alias="startsAt")
    fingerprint: str = Field(min_length=1, max_length=128)
    labels: dict[str, str] = Field(max_length=100)


class AlertmanagerDelivery(BaseModel):
    alerts: list[AlertmanagerAlert] = Field(max_length=100)


class AlertDeliverySource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sourceId: str = Field(min_length=1, max_length=160)
    webhookToken: SecretStr
    autoDiagnosticOwnerUserId: str = ""
    autoDiagnosticAlertNames: tuple[str, ...] = ()


class AlertDeliverySettings(BaseModel):
    sources: tuple[AlertDeliverySource, ...] = ()


def authenticate_delivery(
    config_path: Path | str | None, source_id: str, bearer: str,
) -> AlertDeliverySource | None:
    raw = load_project_config(config_path).get("alertDelivery", {})
    try:
        sources = AlertDeliverySettings.model_validate(raw).sources
    except ValidationError as exc:
        raise ProjectConfigurationError("告警推送配置无效。") from exc
    matching = [entry for entry in sources if entry.sourceId == source_id]
    if len(matching) != 1:
        return None
    source = matching[0]
    expected = source.webhookToken.get_secret_value()
    if len(expected) < 16 or not compare_digest(expected.encode(), bearer.encode()):
        return None
    return source


class AlertNotificationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def receive(
        self,
        *,
        tenant_key: str,
        source: AlertDeliverySource,
        delivery: AlertmanagerDelivery,
        directory: OrganizationDirectory,
    ) -> bool:
        member = directory.resolve(source.autoDiagnosticOwnerUserId)
        valid_actor = (
            member if member is not None and member.may_access_diagnosis(tenant_key) else None
        )
        now = datetime.now(timezone.utc)
        queued = False
        async with self._session_factory.begin() as session:
            for alert in delivery.alerts:
                labels = {
                    key: alert.labels[key].strip()[:200]
                    for key in ("alertname", "service", "environment", "severity", "trace_id")
                    if key in alert.labels
                }
                starts_at = alert.starts_at.astimezone(timezone.utc)
                notification_id = _notification_id(
                    tenant_key, source.sourceId, alert.fingerprint, starts_at,
                )
                owner_id = _auto_diagnostic_owner(source, alert.status, labels, valid_actor)
                diagnostic_id = f"diagnostic_{uuid4().hex}" if owner_id else None
                statement = (
                    insert(AlertNotificationModel)
                    .values(
                        id=notification_id, tenant_key=tenant_key, source_id=source.sourceId,
                        starts_at=starts_at, received_at=now, status=alert.status, labels=labels,
                        diagnostic_id=diagnostic_id, diagnostic_owner_id=owner_id,
                    )
                    .on_conflict_do_nothing(index_elements=["id"])
                    .returning(AlertNotificationModel.id)
                )
                inserted = (await session.execute(statement)).scalar_one_or_none()
                if inserted is None and alert.status == "resolved":
                    await session.execute(
                        update(AlertNotificationModel)
                        .where(
                            AlertNotificationModel.id == notification_id,
                            AlertNotificationModel.status == "firing",
                        )
                        .values(status="resolved", received_at=now)
                    )
                if inserted is not None and diagnostic_id is not None and owner_id is not None:
                    _enqueue_diagnostic(
                        session,
                        owner_id=owner_id, tenant_key=tenant_key,
                        diagnostic_id=diagnostic_id, source_id=source.sourceId,
                        alert_id=notification_id, labels=labels, starts_at=starts_at, now=now,
                    )
                    queued = True
        return queued

    async def list_for(self, member: OrganizationMember) -> list[JsonDict]:
        statement = (
            select(AlertNotificationModel)
            .where(
                AlertNotificationModel.tenant_key == member.tenant_key,
                AlertNotificationModel.source_id.in_(member.alert_sources),
            )
            .order_by(AlertNotificationModel.received_at.desc(), AlertNotificationModel.id)
            .limit(50)
        )
        async with self._session_factory() as session:
            rows = (await session.scalars(statement)).all()
            return [_notification_payload(row, member.id) for row in rows]


def _notification_id(tenant: str, source: str, fingerprint: str, starts_at: datetime) -> str:
    identity = json.dumps((tenant, source, fingerprint, starts_at.isoformat())).encode()
    return f"alert_{sha256(identity).hexdigest()}"


def _auto_diagnostic_owner(
    source: AlertDeliverySource, status: str, labels: dict[str, str],
    member: OrganizationMember | None,
) -> str | None:
    if (
        member is not None
        and status == "firing"
        and labels.get("alertname") in source.autoDiagnosticAlertNames
        and labels.get("service") == TTS_SERVICE
        and labels.get("environment") == TTS_ENVIRONMENT
    ):
        return member.id
    return None


def _enqueue_diagnostic(
    session: AsyncSession,
    *,
    owner_id: str,
    tenant_key: str,
    diagnostic_id: str,
    source_id: str,
    alert_id: str,
    labels: dict[str, str],
    starts_at: datetime,
    now: datetime,
) -> None:
    alert_name = labels["alertname"]
    query = f"排查本地测试告警 {alert_name}"
    session.add(DiagnosticTaskModel(
        id=diagnostic_id, owner_user_id=owner_id, status="accepted", query=query,
        input_payload={
            "tenantKey": tenant_key,
            "query": query,
            "alert": {
                "id": alert_id, "source": source_id, "alertName": alert_name,
                "startsAt": starts_at.isoformat(), "status": "firing",
                "service": labels["service"], "severity": labels.get("severity", ""),
                "labels": labels,
            },
        },
        result_payload={}, created_at=now, updated_at=now,
    ))
    session.add(BackgroundJobModel(
        id=f"job_{uuid4().hex}", owner_user_id=owner_id,
        kind="aiops_diagnosis", resource_type="aiops_diagnostic", resource_id=diagnostic_id,
        status="queued", payload={"diagnosticId": diagnostic_id, "tenantKey": tenant_key},
        attempt=0, max_attempts=1, timeout_seconds=1800,
        available_at=now, created_at=now, updated_at=now,
    ))


def _notification_payload(row: AlertNotificationModel, user_id: str) -> JsonDict:
    return {
        "id": row.id, "source": row.source_id, "alertName": row.labels.get("alertname", ""),
        "service": row.labels.get("service", ""), "environment": row.labels.get("environment", ""),
        "startsAt": row.starts_at.replace(tzinfo=timezone.utc).isoformat(),
        "receivedAt": row.received_at.replace(tzinfo=timezone.utc).isoformat(),
        "status": row.status,
        **({"diagnosticId": row.diagnostic_id} if row.diagnostic_owner_id == user_id else {}),
    }
