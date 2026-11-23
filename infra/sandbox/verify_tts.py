"""Fixed offline TTS experiments run by the trusted sandbox image."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from math import ceil
from time import monotonic

MAX_EXPORT_BYTES = 128 * 1024
MAX_EXPORT_RECORDS = 500


def log_statistics(raw: bytes) -> dict[str, object]:
    lines = raw.splitlines()
    if len(raw) > MAX_EXPORT_BYTES or len(lines) > MAX_EXPORT_RECORDS:
        raise ValueError("The observation snapshot exceeded its input limit.")
    queue: list[int] = []
    synthesis: list[int] = []
    for line in lines:
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(record, dict) or record.get("service") != "tts-demo":
            continue
        trace = record.get("trace_id")
        if record.get("environment") != "test" or not isinstance(trace, str) or not re.fullmatch(r"[0-9a-f]{32}", trace):
            continue
        wait_ms, synth_ms = record.get("queue_ms"), record.get("synthesis_ms")
        if type(wait_ms) is int and type(synth_ms) is int and all(0 <= value <= 120_000 for value in (wait_ms, synth_ms)):
            queue.append(wait_ms)
            synthesis.append(synth_ms)
    return {
        "template": "tts-log-statistics", "samples": len(queue),
        "ignored": len(lines) - len(queue), "queue": _latency_stats(queue),
        "synthesis": _latency_stats(synthesis),
    }


def _latency_stats(values: list[int]) -> dict[str, int] | None:
    if not values:
        return None
    values.sort()
    return {
        "meanMs": round(sum(values) / len(values)),
        "p95Ms": values[ceil(0.95 * len(values)) - 1],
        "maxMs": values[-1],
    }


async def measure_queue(concurrency: int, sample_size: int) -> int:
    workers = asyncio.Semaphore(concurrency)

    async def request() -> float:
        submitted = monotonic()
        async with workers:
            wait_ms = (monotonic() - submitted) * 1000
            await asyncio.sleep(.04)
            return wait_ms

    waits = await asyncio.gather(*(request() for _ in range(sample_size)))
    return round(max(waits))


async def measure_synthesis(delay: float, sample_size: int) -> int:
    timings: list[float] = []
    for _ in range(sample_size):
        started = monotonic()
        await asyncio.sleep(delay)
        timings.append((monotonic() - started) * 1000)
    return round(max(timings))


async def run(template: str) -> dict[str, str | int]:
    if template == "tts-queue-concurrency":
        return {
            "template": template, "phase": "queue", "samples": 8,
            "control_ms": await measure_queue(1, 8), "comparison_ms": await measure_queue(3, 8),
        }
    if template == "tts-synthesis-baseline":
        return {
            "template": template, "phase": "synthesis", "samples": 4,
            "control_ms": await measure_synthesis(.12, 4),
            "comparison_ms": await measure_synthesis(.012, 4),
        }
    raise ValueError("Unsupported verification template.")


if __name__ == "__main__":
    if sys.argv[1] == "tts-log-statistics":
        result = log_statistics(sys.stdin.buffer.read(MAX_EXPORT_BYTES + 1))
    else:
        result = asyncio.run(run(sys.argv[1]))
    print(json.dumps(result, separators=(",", ":")))
