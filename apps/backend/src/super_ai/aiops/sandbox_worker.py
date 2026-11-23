"""Entry point for the separate, Docker-capable sandbox worker."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path

from super_ai.aiops.docker_verifier import DockerVerificationError, DockerVerificationExecutor
from super_ai.aiops.log_statistics import LOG_STATISTICS_JOB_KIND, log_statistics_job_handler
from super_ai.aiops.verification import (
    FINAL_STATES,
    VERIFICATION_JOB_KIND,
    VerificationRepository,
    load_verification_settings,
)
from super_ai.jobs import BackgroundJobContext, BackgroundJobRuntime, JobCancelled
from super_ai.memory.database import create_memory_engine, create_memory_session_factory
from super_ai.memory.extended_sqlite import SQLiteBackgroundJobRepository
from super_ai.observability import emit_event
from super_ai.organization import load_organization_directory
from super_ai.project_config import ProjectConfigurationError

logger = logging.getLogger(__name__)


class _SandboxLeaseLost(RuntimeError):
    """The caller no longer holds the execution lease."""


def sandbox_job_handler(
    plans: VerificationRepository,
    executor: DockerVerificationExecutor,
    config_path: str | Path | None,
) -> Callable[[BackgroundJobContext], Awaitable[None]]:
    async def handle(context: BackgroundJobContext) -> None:
        plan, fresh = await plans.claim(context.job.resource_id, context.job.id)
        if plan is None:
            raise DockerVerificationError("验证计划不可用。")
        if plan.status in FINAL_STATES:
            return
        if plan.status != "running":
            raise DockerVerificationError("验证计划未进入可执行状态。")

        async def authorize() -> None:
            await context.raise_if_cancelled()
            claimed = await context.repository.get(
                owner_user_id=context.job.owner_user_id, job_id=context.job.id,
            )
            if claimed is None or claimed.lease_owner != context.job.lease_owner:
                raise _SandboxLeaseLost("验证任务租约已转移。")
            directory = load_organization_directory(config_path)
            for user_id in (plan.requester_id, plan.approval_actor_id):
                member = directory.resolve(user_id) if user_id is not None else None
                if member is None or not member.may_access_diagnosis(plan.tenant_key):
                    raise DockerVerificationError("验证申请者或审批人的权限已失效。")
            try:
                settings = load_verification_settings(config_path)
            except ProjectConfigurationError:
                raise DockerVerificationError("沙箱验证配置不可用。") from None
            if not settings.allows(plan.tenant_key) or settings.imageId != plan.image_id:
                raise DockerVerificationError("已批准的镜像或试验范围发生变化。")
            expiry = plan.expires_at.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                raise DockerVerificationError("验证计划的有效期已结束。")

        try:
            result = await executor.execute(plan, may_create=fresh, authorize=authorize)
            await authorize()
        except JobCancelled:
            await _cleanup(plans, executor, plan.execution_id)
            raise
        except _SandboxLeaseLost:
            raise
        except Exception as exc:
            reason = (
                str(exc) if isinstance(exc, DockerVerificationError) else "沙箱操作失败或超时。"
            )
            await plans.finish(
                plan.id, succeeded=False, result={"outcome": "failed", "reason": reason},
            )
            await _cleanup(plans, executor, plan.execution_id)
            raise DockerVerificationError(reason) from None
        await plans.finish(plan.id, succeeded=True, result=result)
        await _cleanup(plans, executor, plan.execution_id)

    return handle


async def _cleanup(
    plans: VerificationRepository, executor: DockerVerificationExecutor, execution_id: str,
) -> None:
    try:
        await executor.cleanup(execution_id)
    except (DockerVerificationError, asyncio.TimeoutError):
        emit_event(logger, "verification.container.cleanup_deferred")
        return
    await plans.cleaned(execution_id)


async def run_worker(config_path: str | Path | None = None) -> None:
    engine = create_memory_engine(config_path=config_path)
    sessions = create_memory_session_factory(engine)
    plans = VerificationRepository(sessions)
    executor = DockerVerificationExecutor()
    worker = BackgroundJobRuntime(
        SQLiteBackgroundJobRepository(sessions), concurrency=1, poll_seconds=.5,
    )
    worker.register(VERIFICATION_JOB_KIND, sandbox_job_handler(plans, executor, config_path))
    worker.register(
        LOG_STATISTICS_JOB_KIND, log_statistics_job_handler(sessions, executor, config_path),
    )
    await worker.start()
    try:
        while True:
            for execution_id in await plans.pending_cleanup():
                await _cleanup(plans, executor, execution_id)
            await asyncio.sleep(2)
    finally:
        await worker.stop()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
