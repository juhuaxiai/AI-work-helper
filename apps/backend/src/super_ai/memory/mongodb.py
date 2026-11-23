"""MongoDB persistence for chat sessions, messages and their tool audits."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar, cast

from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import PyMongoError
from pymongo.write_concern import WriteConcern

from super_ai.memory.models import utc_now
from super_ai.memory.repositories import (
    AgentToolCallAuditRecord,
    ChatMessageRecord,
    ChatSessionRecord,
    JsonDict,
    TenantScopeError,
    TimeRangeFilter,
)
from super_ai.project_config import ProjectConfigurationError, load_project_config

Document = dict[str, Any]
Record = TypeVar("Record", ChatSessionRecord, ChatMessageRecord, AgentToolCallAuditRecord)
Result = TypeVar("Result")
_DATE_KEYS = ("created_at", "updated_at", "last_compacted_at", "started_at", "completed_at")
_JSON_KEYS = ("metadata", "arguments")
_RESET_MEMORY: Document = {
    "memory_summary": None,
    "compacted_message_count": 0,
    "context_tokens": 0,
    "last_compacted_at": None,
}


@dataclass(frozen=True, slots=True)
class MongoChatSettings:
    uri: str = field(repr=False)
    database: str


class MongoChatUnavailable(RuntimeError):
    """The configured MongoDB topology is unavailable for chat operations."""


class ChatImportConflict(RuntimeError):
    """An SQLite chat id already exists at the destination with different provenance."""


def load_mongo_chat_settings(config_path: str | Path | None) -> MongoChatSettings | None:
    raw = load_project_config(config_path).get("chatStorage", {})
    if not isinstance(raw, dict):
        raise ProjectConfigurationError("chatStorage must be an object.")
    values = cast(dict[str, object], raw)
    driver = values.get("driver", "sqlite")
    if driver == "sqlite":
        return None
    if driver != "mongodb":
        raise ProjectConfigurationError("chatStorage.driver must be sqlite or mongodb.")
    uri = values.get("mongodbUri")
    database = values.get("mongodbDatabase", "agent_py_chat")
    if not isinstance(uri, str) or not uri.startswith(("mongodb://", "mongodb+srv://")):
        raise ProjectConfigurationError("chatStorage.mongodbUri must be a MongoDB URI.")
    if (
        not isinstance(database, str)
        or not database
        or len(database.encode()) > 63
        or any(char in database for char in ' /\\."$\0')
        or database in {"admin", "local", "config"}
    ):
        raise ProjectConfigurationError(
            "chatStorage.mongodbDatabase must be a writable database name."
        )
    return MongoChatSettings(uri, database)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    # UTC strings retain the original microsecond precision for legacy history ordering.
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _document(record: ChatSessionRecord | ChatMessageRecord | AgentToolCallAuditRecord) -> Document:
    doc = asdict(record)
    doc["_id"] = doc.pop("id")
    for key in _DATE_KEYS:
        value = doc.get(key)
        if isinstance(value, datetime):
            doc[key] = _timestamp(value)
    for key in _JSON_KEYS:
        if key in doc:
            doc[key] = json.dumps(doc[key], ensure_ascii=False)
    return doc


def _record(kind: type[Record], row: Document) -> Record:
    values = {key: value for key, value in row.items() if key in kind.__dataclass_fields__}
    values["id"] = row["_id"]
    for key in _DATE_KEYS:
        value = values.get(key)
        if isinstance(value, str):
            values[key] = datetime.fromisoformat(value)
    for key in _JSON_KEYS:
        if key in values:
            values[key] = json.loads(values[key])
    return kind(**values)


def _session_filter(owner_user_id: str, session_id: str) -> Document:
    return {"_id": session_id, "owner_user_id": owner_user_id}


def _children(parent: Document) -> Document:
    return {
        "session_id": parent["_id"],
        "owner_user_id": parent["owner_user_id"],
        "organization_key": parent["organization_key"],
    }


def _date_filter(time_range: TimeRangeFilter | None) -> Document:
    bounds: dict[str, str] = {}
    if time_range is not None:
        if time_range.start_at is not None:
            bounds["$gte"] = _timestamp(time_range.start_at)
        if time_range.end_at is not None:
            bounds["$lte"] = _timestamp(time_range.end_at)
    return {"created_at": bounds} if bounds else {}


class MongoChatMemoryRepository:
    def __init__(self, settings: MongoChatSettings) -> None:
        try:
            self._client = AsyncMongoClient[Document](
                settings.uri,
                serverSelectionTimeoutMS=3000,
                timeoutMS=10000,
            )
        except (ValueError, PyMongoError):
            raise ProjectConfigurationError("Invalid chatStorage.mongodbUri.") from None
        database = self._client.get_database(
            settings.database, write_concern=WriteConcern("majority")
        )
        self._sessions = database["chat_sessions"]
        self._messages = database["chat_messages"]
        self._audits = database["chat_tool_audits"]
        self._imports = database["sqlite_chat_imports"]
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _check_topology(self) -> None:
        description = await self._client.admin.command("hello")
        if not (description.get("setName") or description.get("msg") == "isdbgrid"):
            raise MongoChatUnavailable(
                "MongoDB chat storage requires a transaction-capable topology."
            )

    async def initialize(self) -> None:
        async with self._init_lock:
            if self._initialized:
                return
            await self._check_topology()
            await self._sessions.create_index(
                [("owner_user_id", 1), ("updated_at", -1), ("_id", 1)]
            )
            index = [
                ("owner_user_id", 1),
                ("session_id", 1),
                ("organization_key", 1),
                ("created_at", 1),
                ("_id", 1),
            ]
            await self._messages.create_index(index)
            await self._audits.create_index(index)
            self._initialized = True

    async def readiness(self) -> bool:
        try:
            await self.initialize()
            await self._check_topology()
        except (MongoChatUnavailable, PyMongoError):
            return False
        return True

    async def close(self) -> None:
        await self._client.close()

    async def _transaction(
        self, callback: Callable[[AsyncClientSession], Awaitable[Result]]
    ) -> Result:
        session = self._client.start_session()
        async with session:
            return await session.with_transaction(callback, write_concern=WriteConcern("majority"))

    async def create_session(
        self,
        *,
        owner_user_id: str,
        session_id: str,
        title: str | None = None,
        created_at: datetime | None = None,
        organization_key: str | None = None,
    ) -> ChatSessionRecord:
        await self.initialize()
        now = created_at or utc_now()
        record = ChatSessionRecord(
            session_id,
            owner_user_id,
            title,
            now,
            now,
            organization_key=organization_key,
        )
        await self._sessions.insert_one(_document(record))
        return record

    async def get_session(self, *, owner_user_id: str, session_id: str) -> ChatSessionRecord | None:
        await self.initialize()
        row = await self._sessions.find_one(_session_filter(owner_user_id, session_id))
        return _record(ChatSessionRecord, row) if row is not None else None

    async def _update_session(
        self, owner_user_id: str, session_id: str, changes: Document
    ) -> ChatSessionRecord | None:
        await self.initialize()
        row = await self._sessions.find_one_and_update(
            _session_filter(owner_user_id, session_id),
            {"$set": changes},
            return_document=ReturnDocument.AFTER,
        )
        return _record(ChatSessionRecord, row) if row is not None else None

    async def update_session_title(
        self,
        *,
        owner_user_id: str,
        session_id: str,
        title: str,
        updated_at: datetime | None = None,
    ) -> ChatSessionRecord | None:
        return await self._update_session(
            owner_user_id,
            session_id,
            {
                "title": title,
                "updated_at": _timestamp(updated_at or utc_now()),
            },
        )

    async def update_memory_state(
        self,
        *,
        owner_user_id: str,
        session_id: str,
        memory_mode: str | None = None,
        memory_summary: str | None = None,
        compacted_message_count: int | None = None,
        context_tokens: int | None = None,
        last_compacted_at: datetime | None = None,
        clear_compaction: bool = False,
        updated_at: datetime | None = None,
    ) -> ChatSessionRecord | None:
        changes: Document = {"updated_at": _timestamp(updated_at or utc_now())}
        if memory_mode is not None:
            changes["memory_mode"] = memory_mode
        if clear_compaction:
            changes.update(_RESET_MEMORY)
        else:
            changes.update(
                {
                    key: value
                    for key, value in {
                        "memory_summary": memory_summary,
                        "compacted_message_count": compacted_message_count,
                        "context_tokens": context_tokens,
                        "last_compacted_at": _timestamp(last_compacted_at)
                        if last_compacted_at
                        else None,
                    }.items()
                    if value is not None
                }
            )
        return await self._update_session(owner_user_id, session_id, changes)

    async def list_sessions(
        self,
        *,
        owner_user_id: str,
        time_range: TimeRangeFilter | None = None,
    ) -> list[ChatSessionRecord]:
        await self.initialize()
        cursor = self._sessions.find(
            {"owner_user_id": owner_user_id, **_date_filter(time_range)},
            sort=[("updated_at", -1), ("_id", 1)],
        )
        return [_record(ChatSessionRecord, row) async for row in cursor]

    async def append_message(
        self,
        *,
        owner_user_id: str,
        message_id: str,
        session_id: str,
        role: str,
        content: str,
        metadata: JsonDict | None = None,
        created_at: datetime | None = None,
    ) -> ChatMessageRecord:
        await self.initialize()
        now = created_at or utc_now()
        record = ChatMessageRecord(
            message_id, owner_user_id, session_id, role, content, metadata or {}, now
        )

        async def append(transaction: AsyncClientSession) -> None:
            parent = await self._sessions.find_one_and_update(
                _session_filter(owner_user_id, session_id),
                {"$set": {"updated_at": _timestamp(now)}, "$inc": {"write_revision": 1}},
                session=transaction,
            )
            if parent is None:
                raise TenantScopeError("Chat session is not accessible.")
            await self._messages.insert_one(
                {**_document(record), **_children(parent)}, session=transaction
            )

        await self._transaction(append)
        return record

    async def clear_messages(
        self,
        *,
        owner_user_id: str,
        session_id: str,
        updated_at: datetime | None = None,
    ) -> int:
        await self.initialize()
        changes = {**_RESET_MEMORY, "updated_at": _timestamp(updated_at or utc_now())}

        async def clear(transaction: AsyncClientSession) -> int:
            parent = await self._sessions.find_one_and_update(
                _session_filter(owner_user_id, session_id),
                {"$set": changes, "$inc": {"write_revision": 1}},
                session=transaction,
            )
            if parent is None:
                return 0
            deleted = await self._messages.delete_many(_children(parent), session=transaction)
            return deleted.deleted_count

        return await self._transaction(clear)

    async def delete_session(self, *, owner_user_id: str, session_id: str) -> bool:
        await self.initialize()

        async def delete(transaction: AsyncClientSession) -> bool:
            parent = await self._sessions.find_one_and_delete(
                _session_filter(owner_user_id, session_id),
                session=transaction,
            )
            if parent is None:
                return False
            await self._messages.delete_many(_children(parent), session=transaction)
            await self._audits.delete_many(_children(parent), session=transaction)
            return True

        return await self._transaction(delete)

    async def list_messages(
        self,
        *,
        owner_user_id: str,
        session_id: str,
        time_range: TimeRangeFilter | None = None,
        before: ChatMessageRecord | None = None,
        limit: int | None = None,
    ) -> list[ChatMessageRecord]:
        await self.initialize()
        parent = await self._sessions.find_one(_session_filter(owner_user_id, session_id))
        if parent is None:
            return []
        query = {**_children(parent), **_date_filter(time_range)}
        if before is not None:
            key = _timestamp(before.created_at)
            query["$or"] = [
                {"created_at": {"$lt": key}},
                {"created_at": key, "_id": {"$lt": before.id}},
            ]
        direction = 1 if limit is None else -1
        cursor = self._messages.find(
            query,
            sort=[("created_at", direction), ("_id", direction)],
            limit=limit or 0,
        )
        records = [_record(ChatMessageRecord, row) async for row in cursor]
        if limit is not None:
            records.reverse()
        return records

    async def get_message(self, *, owner_user_id: str, message_id: str) -> ChatMessageRecord | None:
        await self.initialize()
        row = await self._messages.find_one({"_id": message_id, "owner_user_id": owner_user_id})
        if row is None:
            return None
        parent = await self._sessions.find_one(
            {
                **_session_filter(owner_user_id, row["session_id"]),
                "organization_key": row["organization_key"],
            }
        )
        return _record(ChatMessageRecord, row) if parent is not None else None

    async def import_session(
        self,
        record: ChatSessionRecord,
        messages: list[ChatMessageRecord],
        audits: list[AgentToolCallAuditRecord],
    ) -> bool:
        """Import an entire legacy session once, even across subsequent deletions."""
        await self.initialize()
        parent = _document(record)
        scope = _children(parent)

        async def copy(transaction: AsyncClientSession) -> bool:
            marker = await self._imports.find_one({"_id": record.id}, session=transaction)
            if marker is not None:
                if any(marker.get(key) != value for key, value in scope.items()):
                    raise ChatImportConflict("The imported chat id has a different scope.")
                return False
            if await self._sessions.find_one({"_id": record.id}, session=transaction):
                raise ChatImportConflict("The chat id already exists in MongoDB.")
            await self._sessions.insert_one(parent, session=transaction)
            if messages:
                await self._messages.insert_many(
                    [{**_document(item), **scope} for item in messages],
                    session=transaction,
                )
            if audits:
                await self._audits.insert_many(
                    [{**_document(item), **scope} for item in audits],
                    session=transaction,
                )
            await self._imports.insert_one({"_id": record.id, **scope}, session=transaction)
            return True

        return await self._transaction(copy)

    async def create_for_chat_session(
        self,
        *,
        owner_user_id: str,
        audit_id: str,
        chat_session_id: str,
        tool_name: str,
        arguments: JsonDict | None = None,
        started_at: datetime | None = None,
    ) -> AgentToolCallAuditRecord:
        await self.initialize()
        now = started_at or utc_now()
        record = AgentToolCallAuditRecord(
            audit_id,
            owner_user_id,
            chat_session_id,
            None,
            tool_name,
            "started",
            arguments or {},
            None,
            None,
            now,
            None,
            None,
            now,
        )

        async def create(transaction: AsyncClientSession) -> None:
            parent = await self._sessions.find_one_and_update(
                _session_filter(owner_user_id, chat_session_id),
                {"$inc": {"write_revision": 1}},
                session=transaction,
            )
            if parent is None:
                raise TenantScopeError("Chat session is not accessible.")
            await self._audits.insert_one(
                {**_document(record), **_children(parent)}, session=transaction
            )

        await self._transaction(create)
        return record

    async def list_for_chat_session(
        self,
        *,
        owner_user_id: str,
        chat_session_id: str,
    ) -> list[AgentToolCallAuditRecord]:
        await self.initialize()
        parent = await self._sessions.find_one(_session_filter(owner_user_id, chat_session_id))
        if parent is None:
            raise TenantScopeError("Chat session is not accessible.")
        cursor = self._audits.find(_children(parent), sort=[("created_at", 1), ("_id", 1)])
        return [_record(AgentToolCallAuditRecord, row) async for row in cursor]

    async def finalize(
        self,
        *,
        owner_user_id: str,
        audit_id: str,
        status: str,
        result_summary: str | None = None,
        error_message: str | None = None,
        completed_at: datetime | None = None,
    ) -> AgentToolCallAuditRecord | None:
        await self.initialize()
        where = {"_id": audit_id, "owner_user_id": owner_user_id}
        row = await self._audits.find_one(where)
        if row is None:
            return None
        now = completed_at or utc_now()
        started_at = datetime.fromisoformat(row["started_at"])
        completed = datetime.fromisoformat(_timestamp(now))
        updated = await self._audits.find_one_and_update(
            where,
            {
                "$set": {
                    "status": status,
                    "result_summary": result_summary,
                    "error_message": error_message,
                    "completed_at": _timestamp(now),
                    "duration_ms": max(0, round((completed - started_at).total_seconds() * 1000)),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return _record(AgentToolCallAuditRecord, updated) if updated is not None else None
