from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from super_ai.aiops.local_observability import (
    JsonlTtsObservationSource,
    ObservationQuery,
    TtsObservation,
)
from super_ai.aiops.log_exports import MAX_EXPORT_RECORDS
from super_ai.organization import OrganizationAccessDenied


async def test_log_export_source_keeps_window_tenant_and_record_limit(tmp_path: Path) -> None:
    log = tmp_path / "observations.jsonl"
    source = JsonlTtsObservationSource(log, "tenant")
    now = datetime(2026, 11, 23, tzinfo=timezone.utc)
    record = TtsObservation(
        tenant_key="tenant",
        timestamp=now,
        trace_id="c" * 32,
        queue_ms=90,
        synthesis_ms=14,
    )
    outside_window = record.model_copy(update={"timestamp": now - timedelta(hours=1)})
    other_tenant = record.model_copy(update={"tenant_key": "other"})
    log.write_text(
        "\n".join(
            [record.model_dump_json()] * (MAX_EXPORT_RECORDS + 1)
            + [outside_window.model_dump_json(), other_tenant.model_dump_json(), "invalid"]
        )
        + "\n",
        encoding="utf-8",
    )

    scope = ObservationQuery("tenant", now - timedelta(minutes=1), now, None)
    assert await source.read_for_export(scope, MAX_EXPORT_RECORDS) == [record] * MAX_EXPORT_RECORDS
    with pytest.raises(OrganizationAccessDenied):
        await source.read_for_export(
            ObservationQuery("other", scope.from_at, scope.to_at, None),
            MAX_EXPORT_RECORDS,
        )


def test_packaged_statistics_uses_nearest_rank_and_skips_invalid_rows() -> None:
    script = Path(__file__).resolve().parents[3] / "infra/sandbox/verify_tts.py"
    valid = [
        json.dumps(
            {
                "service": "tts-demo",
                "environment": "test",
                "trace_id": "f" * 32,
                "queue_ms": value * 2,
                "synthesis_ms": value * 4,
            }
        )
        for value in range(1, 21)
    ]
    rows = [*reversed(valid), "{broken", json.dumps({"service": "other"})]
    completed = subprocess.run(
        [sys.executable, str(script), "tts-log-statistics"],
        input="\n".join(rows),
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["samples"] == 20 and result["ignored"] == 2
    assert result["queue"] == {"meanMs": 21, "p95Ms": 38, "maxMs": 40}
    assert result["synthesis"] == {"meanMs": 42, "p95Ms": 76, "maxMs": 80}
