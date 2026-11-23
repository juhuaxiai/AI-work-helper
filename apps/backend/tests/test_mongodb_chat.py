from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pymongo import AsyncMongoClient
from pymongo.errors import BulkWriteError, DuplicateKeyError

from super_ai.api.app import create_app
from super_ai.chat.streaming import (
    ChatAgentContentDelta,
    ChatAgentEvent,
    ChatAgentRequest,
    ChatAgentToolCall,
)
from super_ai.memory.database import create_memory_engine, create_memory_session_factory
from super_ai.memory.migrate_chat import migrate_chat
from super_ai.memory.mongodb import MongoChatMemoryRepository, load_mongo_chat_settings
from super_ai.memory.repositories import ChatMessageRecord, TenantScopeError
from super_ai.memory.sqlite import SQLiteChatMemoryRepository, SQLiteToolCallAuditRepository
from super_ai.project_config import ProjectConfigurationError


def test_mongo_storage_configuration_is_opt_in_and_preserves_uri_privacy(tmp_path: Path) -> None:
    path = tmp_path / "project.json"
    path.write_text("{}", encoding="utf-8")
    assert load_mongo_chat_settings(path) is None
    path.write_text('{"chatStorage":{"driver":"mongodb"}}', encoding="utf-8")
    with pytest.raises(ProjectConfigurationError, match="mongodbUri"):
        load_mongo_chat_settings(path)
    override = tmp_path / "user.project.json"
    override.write_text(
        '{"chatStorage":{"mongodbUri":"mongodb://user:fixture-value@localhost"}}',
        encoding="utf-8",
    )
    settings = load_mongo_chat_settings(path)
    assert settings is not None
    assert "fixture-value" not in repr(settings)


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    uri = os.getenv("AGENT_PY_TEST_MONGO_URI")
    if uri is None:
        pytest.skip("Set AGENT_PY_TEST_MONGO_URI to a dedicated MongoDB replica set.")
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    url = f"sqlite+aiosqlite:///{tmp_path / 'chat.sqlite3'}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    path = tmp_path / "project.json"
    values = cast(
        dict[str, Any],
        json.loads(
            Path("../../config/project.template.json").read_text(encoding="utf-8"),
        ),
    )
    values["backend"]["memoryDatabaseUrl"] = url
    values["llm"]["apiKey"] = "fixture-placeholder"
    values["llm"]["embeddingModel"] = "test-embedding"
    values["llm"]["chatModel"] = "test-model"
    values["llm"]["modelCapabilities"] = {"test-model": {"contextWindowTokens": 8192}}
    values["chatStorage"] = {
        "driver": "mongodb",
        "mongodbUri": uri,
        "mongodbDatabase": f"agent_py_test_{uuid4().hex}",
    }
    path.write_text(json.dumps(values), encoding="utf-8")
    return path, values


@pytest.mark.asyncio
async def test_chat_migrates_paginates_and_keeps_scope_after_restart(
    project: tuple[Path, dict[str, Any]],
) -> None:
    path, values = project
    settings = load_mongo_chat_settings(path)
    assert settings is not None
    sqlite_settings = {**values, "chatStorage": {"driver": "sqlite"}}
    path.write_text(json.dumps(sqlite_settings), encoding="utf-8")
    app = create_app(project_config_path=path)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            owner, headers = await _register(client, "owner")
            member, other_headers = await _register(client, "member")

        values["organizationAccess"] = {
            "tenants": [
                {"tenant_key": key, "oncall_department_id": "oncall", "alert_sources": []}
                for key in ("company-a", "company-b")
            ],
            "members": [
                {
                    "user_id": user,
                    "tenant_key": "company-a",
                    "open_id": f"open-{user}",
                    "department_ids": ["oncall"],
                }
                for user in (owner, member)
            ],
        }
        path.write_text(json.dumps(values), encoding="utf-8")
        engine = create_memory_engine(config_path=path)
        factory = create_memory_session_factory(engine)
        source = SQLiteChatMemoryRepository(factory)
        audits = SQLiteToolCallAuditRepository(factory)
        source_date = datetime(2026, 11, 23, 9, 0, 0, 123456, tzinfo=timezone.utc)
        try:
            await source.create_session(
                owner_user_id=owner, session_id="legacy", title="旧会话", created_at=source_date
            )
            for message_id in ("m3", "m1", "m4", "m2", "m5"):
                await source.append_message(
                    owner_user_id=owner,
                    session_id="legacy",
                    message_id=message_id,
                    role="assistant",
                    content=message_id,
                    metadata={"citations": [{"id": "chunk", "title": "值班手册"}]},
                    created_at=source_date,
                )
            await source.update_memory_state(
                owner_user_id=owner,
                session_id="legacy",
                memory_summary="保留的摘要",
                memory_mode="manual",
                last_compacted_at=source_date,
                compacted_message_count=4,
            )
            await audits.create_for_chat_session(
                owner_user_id=owner,
                audit_id="audit",
                chat_session_id="legacy",
                tool_name="current_time",
                arguments={"timezone": "Asia/Shanghai"},
                started_at=source_date,
            )
            await audits.finalize(owner_user_id=owner, audit_id="audit", status="completed")
            assert await migrate_chat(path) == (1, 0)
            assert await migrate_chat(path) == (0, 1)

            mongo_app = create_app(project_config_path=path, chat_agent_runner=LocalChatRunner())
            async with (
                mongo_app.router.lifespan_context(mongo_app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=mongo_app),
                    base_url="http://testserver",
                ) as client,
            ):
                first = (await client.get("/chat/sessions/legacy?limit=2", headers=headers)).json()[
                    "data"
                ]
                assert [row["id"] for row in first["messages"]] == ["m4", "m5"]
                assert first["nextBefore"] == "m4"
                assert first["messages"][0]["metadata"]["citations"][0]["title"] == "值班手册"
                second = (
                    await client.get("/chat/sessions/legacy?limit=2&before=m4", headers=headers)
                ).json()["data"]
                assert [row["id"] for row in second["messages"]] == ["m2", "m3"]
                last = (
                    await client.get("/chat/sessions/legacy?limit=2&before=m2", headers=headers)
                ).json()["data"]
                assert [row["id"] for row in last["messages"]] == ["m1"]
                assert last["nextBefore"] is None
                assert (
                    await client.get("/chat/sessions/legacy", headers=other_headers)
                ).status_code == 403
                audit_items = (
                    await client.get("/chat/sessions/legacy/tool-call-audits", headers=headers)
                ).json()["data"]["items"]
                assert [(item["id"], item["status"]) for item in audit_items] == [
                    ("audit", "completed")
                ]

                created = await client.post(
                    "/chat/sessions", headers=headers, json={"title": "企业会话"}
                )
                assert created.status_code == 201
                company_session = cast(str, created.json()["data"]["id"])
                company_route = f"/chat/sessions/{company_session}"
                message = await client.post(
                    f"{company_route}/messages",
                    headers=headers,
                    json={"role": "assistant", "content": "测试回复"},
                )
                assert message.status_code == 201
                foreign_cursor = await client.get(f"{company_route}?before=m1", headers=headers)
                assert foreign_cursor.status_code == 400
                stream = await client.post(
                    f"{company_route}/messages:stream",
                    headers=headers,
                    json={"content": "现在几点？"},
                )
                assert '"type":"complete"' in stream.text

            resume = create_app(project_config_path=path)
            async with (
                resume.router.lifespan_context(resume),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=resume),
                    base_url="http://testserver",
                ) as client,
            ):
                assert (await client.get(company_route, headers=headers)).json()["data"][
                    "messages"
                ][0]["content"] == "测试回复"
                persisted_tools = (
                    await client.get(f"{company_route}/tool-call-audits", headers=headers)
                ).json()["data"]["items"]
                assert [(item["id"], item["status"]) for item in persisted_tools] == [
                    ("live-audit", "completed"),
                ]
                repo = cast(MongoChatMemoryRepository, resume.state.mongo_chat)
                assert await repo.readiness()
                legacy = await repo.get_session(owner_user_id=owner, session_id="legacy")
                assert legacy is not None
                assert (
                    legacy.organization_key,
                    legacy.memory_summary,
                    legacy.last_compacted_at,
                ) == (
                    None,
                    "保留的摘要",
                    source_date,
                )
                values["organizationAccess"]["members"][0]["tenant_key"] = "company-b"
                path.write_text(json.dumps(values), encoding="utf-8")
                assert (await client.get(company_route, headers=headers)).status_code == 403
                assert (await client.delete(company_route, headers=headers)).status_code == 403
                assert (
                    await client.get("/chat/sessions/legacy", headers=headers)
                ).status_code == 200
                assert (
                    await client.delete("/chat/sessions/legacy", headers=headers)
                ).status_code == 200
                assert await migrate_chat(path) == (0, 1)
                assert (
                    await client.get("/chat/sessions/legacy", headers=headers)
                ).status_code == 403
                assert (
                    await source.get_session(owner_user_id=owner, session_id="legacy") is not None
                )
        finally:
            await engine.dispose()
    finally:
        await _drop_test_database(settings.uri, settings.database)


@pytest.mark.asyncio
async def test_mongo_append_and_import_are_atomic_and_delete_cascades(
    project: tuple[Path, dict[str, Any]],
) -> None:
    settings = load_mongo_chat_settings(project[0])
    assert settings is not None
    repo = MongoChatMemoryRepository(settings)
    now = datetime(2026, 11, 23, tzinfo=timezone.utc)
    try:
        await repo.create_session(
            owner_user_id="owner", session_id="active", created_at=now, organization_key="org"
        )
        await asyncio.gather(
            *(
                repo.append_message(
                    owner_user_id="owner",
                    session_id="active",
                    message_id=f"m{index}",
                    role="user",
                    content="并发写入",
                    created_at=now,
                )
                for index in range(6)
            )
        )
        with pytest.raises(TenantScopeError):
            await repo.append_message(
                owner_user_id="other",
                session_id="active",
                message_id="no",
                role="user",
                content="越权写入",
            )
        row = await repo.get_session(owner_user_id="owner", session_id="active")
        assert row is not None
        with pytest.raises(DuplicateKeyError):
            await repo.append_message(
                owner_user_id="owner",
                session_id="active",
                message_id="m0",
                role="user",
                content="重复写入",
            )
        assert await repo.get_session(owner_user_id="owner", session_id="active") == row
        await repo.create_for_chat_session(
            owner_user_id="owner",
            chat_session_id="active",
            audit_id="activity",
            tool_name="current_time",
        )
        finalized = await repo.finalize(owner_user_id="owner", audit_id="activity", status="done")
        assert finalized is not None and finalized.status == "done"
        legacy = await repo.create_session(owner_user_id="owner", session_id="migration-source")
        assert await repo.delete_session(owner_user_id="owner", session_id="migration-source")
        conflict = ChatMessageRecord("m0", "owner", legacy.id, "user", "冲突", {}, now)
        with pytest.raises(BulkWriteError):
            await repo.import_session(legacy, [conflict], [])
        assert await repo.get_session(owner_user_id="owner", session_id=legacy.id) is None
        assert await repo.import_session(legacy, [], [])
        assert await repo.clear_messages(owner_user_id="owner", session_id="active") == 6
        assert await repo.delete_session(owner_user_id="owner", session_id="active")
        assert (
            await repo.finalize(owner_user_id="owner", audit_id="activity", status="done") is None
        )
    finally:
        await repo.close()
        await _drop_test_database(settings.uri, settings.database)


async def _register(client: httpx.AsyncClient, alias: str) -> tuple[str, dict[str, str]]:
    response = await client.post(
        "/auth/register",
        json={
            "email": f"{alias}@example.com",
            "password": "testing-only-password",
            "displayName": alias,
        },
    )
    assert response.status_code == 201
    payload = response.json()["data"]
    return cast(str, payload["user"]["id"]), {"Authorization": f"Bearer {payload['accessToken']}"}


async def _drop_test_database(uri: str, name: str) -> None:
    async with AsyncMongoClient[dict[str, Any]](uri) as client:
        await client.drop_database(name)


class LocalChatRunner:
    async def stream(self, request: ChatAgentRequest) -> AsyncIterator[ChatAgentEvent]:
        assert request.session_id
        yield ChatAgentToolCall(id="live-audit", name="current_time", status="started")
        yield ChatAgentToolCall(id="live-audit", name="current_time", status="completed")
        yield ChatAgentContentDelta(delta="测试流式回答")
