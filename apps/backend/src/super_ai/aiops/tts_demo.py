"""A loopback TTS workload used to exercise the local diagnostic workflow."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI
from starlette.responses import PlainTextResponse

from super_ai.aiops.local_observability import (
    TtsObservation,
    build_local_source,
    load_local_observability,
)


def create_app(*, config_path: Path | str | None = None) -> FastAPI:
    settings = load_local_observability(config_path)
    if not settings.tenantKey:
        raise ValueError("Set localObservability.tenantKey before running the TTS demo.")
    source = build_local_source(settings)
    write_lock = asyncio.Lock()
    app = FastAPI(title="Local TTS demo")
    latest: TtsObservation | None = None

    async def simulate(
        scenario: Literal["normal", "queue-slow", "synthesis-slow"],
    ) -> TtsObservation:
        nonlocal latest
        start = monotonic()
        await asyncio.sleep(0.15 if scenario == "queue-slow" else 0.01)
        dequeued = monotonic()
        await asyncio.sleep(0.15 if scenario == "synthesis-slow" else 0.01)
        recorded = TtsObservation(
            tenant_key=settings.tenantKey,
            timestamp=datetime.now(timezone.utc),
            trace_id=uuid4().hex,
            queue_ms=round((dequeued - start) * 1000),
            synthesis_ms=round((monotonic() - dequeued) * 1000),
        )
        async with write_lock:
            await asyncio.to_thread(source.append, recorded)
            latest = recorded
        return recorded

    async def metrics() -> PlainTextResponse:
        lines = [
            "# HELP agent_py_tts_phase_milliseconds Duration of the latest TTS demo phase.",
            "# TYPE agent_py_tts_phase_milliseconds gauge",
        ]
        if latest is not None:
            for phase, duration in (("queue", latest.queue_ms), ("synthesis", latest.synthesis_ms)):
                labels = (
                    f'service="{latest.service}",environment="{latest.environment}",'
                    f'trace_id="{latest.trace_id}",phase="{phase}"'
                )
                lines.append(f"agent_py_tts_phase_milliseconds{{{labels}}} {duration}")
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    app.add_api_route("/simulate/{scenario}", simulate, methods=["POST"])
    app.add_api_route("/metrics", metrics, methods=["GET"])
    return app
