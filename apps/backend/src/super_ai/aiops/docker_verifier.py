"""Worker-only Docker access for approved, fixed verification templates."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from super_ai.aiops.log_exports import MAX_EXPORT_BYTES, MAX_EXPORT_RECORDS
from super_ai.aiops.verification import (
    VERIFICATION_TEMPLATES,
    VERIFY_IMAGE,
    VerificationTemplate,
)
from super_ai.memory.models import VerificationPlanModel
from super_ai.memory.repositories import JsonDict

_EXECUTION_PATTERN = re.compile(r"[0-9a-f]{32}")
_EXECUTION_LABEL = "com.agent-py.verification"
_CONTAINER_SECONDS = 35.0
_ISOLATION_ARGS = (
    "--pull", "never", "--network", "none", "--read-only", "--user", "65534:65534",
    "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--cpus", "0.5",
    "--memory", "96m", "--memory-swap", "96m", "--pids-limit", "32",
    "--tmpfs", "/tmp:rw,nosuid,noexec,size=8m",
)


class DockerVerificationError(RuntimeError):
    """A controlled, operator-safe verification failure."""


class SandboxMeasurements(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    template: VerificationTemplate
    phase: Literal["queue", "synthesis"]
    samples: int = Field(ge=1, le=8)
    control_ms: int = Field(ge=0, le=35_000)
    comparison_ms: int = Field(ge=0, le=35_000)


class LatencyStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    meanMs: int = Field(ge=0, le=120_000)
    p95Ms: int = Field(ge=0, le=120_000)
    maxMs: int = Field(ge=0, le=120_000)


class SandboxLogStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    template: Literal["tts-log-statistics"]
    samples: int = Field(ge=1, le=MAX_EXPORT_RECORDS)
    ignored: int = Field(ge=0, le=MAX_EXPORT_RECORDS)
    queue: LatencyStatistics
    synthesis: LatencyStatistics


@dataclass(frozen=True, slots=True)
class _Container:
    status: str
    exit_code: int


class DockerVerificationExecutor:
    async def execute(
        self,
        plan: VerificationPlanModel,
        *,
        may_create: bool,
        authorize: Callable[[], Awaitable[None]],
    ) -> JsonDict:
        name = _container_name(plan.execution_id)
        if plan.template not in VERIFICATION_TEMPLATES or not VERIFY_IMAGE.fullmatch(plan.image_id):
            raise DockerVerificationError("验证执行的固定模板或镜像无效。")
        await authorize()
        current = await self._inspect(plan.execution_id)
        if current is None:
            if not may_create:
                raise DockerVerificationError("恢复时未找到原容器，执行结果待确认。")
            await self._docker(
                "create", "--name", name, "--label", f"{_EXECUTION_LABEL}={plan.execution_id}",
                *_ISOLATION_ARGS, "--log-driver", "local",
                "--log-opt", "max-size=32k", "--log-opt", "max-file=1",
                "--log-opt", "compress=false",
                plan.image_id, plan.template,
            )
            current = _Container("created", 0)
        if current.status == "created":
            await authorize()
            await self._docker("start", name)
        deadline = monotonic() + _CONTAINER_SECONDS
        while monotonic() < deadline:
            await authorize()
            current = await self._inspect(plan.execution_id)
            if current is None:
                raise DockerVerificationError("找不到原验证容器。")
            if current.status in ("exited", "dead"):
                if current.exit_code != 0:
                    raise DockerVerificationError(f"验证容器退出码为 {current.exit_code}。")
                return await self._measurements(name, plan.template)
            await asyncio.sleep(.2)
        raise DockerVerificationError("沙箱执行超时。")

    async def analyze_log_snapshot(
        self, *, execution_id: str, image_id: str, snapshot: bytes, record_count: int,
    ) -> JsonDict:
        name = _container_name(execution_id)
        if not VERIFY_IMAGE.fullmatch(image_id) or not 1 <= len(snapshot) <= MAX_EXPORT_BYTES:
            raise DockerVerificationError("日志统计的固定镜像或输入大小无效。")
        await self.cleanup(execution_id)
        try:
            output = await self._docker(
                "run", "--rm", "-i", "--name", name,
                "--label", f"{_EXECUTION_LABEL}={execution_id}", *_ISOLATION_ARGS,
                "--log-driver", "none", image_id, "tts-log-statistics", stdin=snapshot,
            )
        finally:
            try:
                await self.cleanup(execution_id)
            except (DockerVerificationError, asyncio.TimeoutError):
                # Docker's --rm still removes an exited container after client disconnect.
                pass
        try:
            result = SandboxLogStatistics.model_validate_json(output or "")
        except ValidationError:
            raise DockerVerificationError("沙箱未返回有效的日志统计结果。") from None
        if result.samples + result.ignored != record_count:
            raise DockerVerificationError("沙箱未完整读取日志快照。")
        return result.model_dump()

    async def cleanup(self, execution_id: str) -> None:
        if await self._inspect(execution_id) is not None:
            await self._docker("rm", "-f", _container_name(execution_id))

    async def _inspect(self, execution_id: str) -> _Container | None:
        output = await self._docker(
            "container", "inspect", _container_name(execution_id), missing=True,
        )
        if output is None:
            return None
        try:
            rows = cast(list[dict[str, object]], json.loads(output))
            labels = cast(dict[str, object], cast(dict[str, object], rows[0]["Config"])["Labels"])
            if labels.get(_EXECUTION_LABEL) != execution_id:
                raise DockerVerificationError("容器标签与验证编号不一致。")
            state = cast(dict[str, object], rows[0]["State"])
            status, exit_code = state["Status"], state["ExitCode"]
            if isinstance(status, str) and isinstance(exit_code, int):
                return _Container(status, exit_code)
        except (IndexError, KeyError, ValueError, TypeError, AttributeError) as exc:
            raise DockerVerificationError("无法识别验证容器状态。") from exc
        raise DockerVerificationError("无法识别验证容器状态。")

    async def _measurements(self, name: str, template: str) -> JsonDict:
        output = await self._docker("logs", "--tail", "1", name)
        try:
            measured = SandboxMeasurements.model_validate_json(output or "")
        except ValidationError as exc:
            raise DockerVerificationError("沙箱没有返回可验证的结构化测量。") from exc
        if measured.template != template:
            raise DockerVerificationError("测试结果的模板与审批内容不一致。")
        difference = measured.control_ms - measured.comparison_ms
        return {
            "phase": measured.phase, "samples": measured.samples,
            "controlMs": measured.control_ms, "comparisonMs": measured.comparison_ms,
            "outcome": "supported" if difference >= 50 else "not_reproduced",
            "exitCode": 0,
        }

    async def _docker(
        self, *args: str, missing: bool = False, stdin: bytes | None = None,
    ) -> str | None:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker", *args, stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise DockerVerificationError("Docker 执行器不可用。") from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(stdin), 7)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if process.returncode:
            if missing and b"No such" in stderr:
                return None
            raise DockerVerificationError("Docker 无法完成受限验证操作。")
        return stdout.decode("utf-8", errors="replace")


def _container_name(execution_id: str) -> str:
    if _EXECUTION_PATTERN.fullmatch(execution_id) is None:
        raise DockerVerificationError("验证执行编号无效。")
    return f"agent-py-verification-{execution_id}"
