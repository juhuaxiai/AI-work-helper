"""Offline migration of existing SQLite chat records to configured MongoDB."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pymongo.errors import PyMongoError
from sqlalchemy.exc import SQLAlchemyError

from super_ai.memory.database import create_memory_engine, create_memory_session_factory
from super_ai.memory.mongodb import (
    ChatImportConflict,
    MongoChatMemoryRepository,
    MongoChatUnavailable,
    load_mongo_chat_settings,
)
from super_ai.memory.sqlite import SQLiteChatMemoryRepository, SQLiteToolCallAuditRepository


async def migrate_chat(config_path: Path | None = None) -> tuple[int, int]:
    settings = load_mongo_chat_settings(config_path)
    if settings is None:
        raise MongoChatUnavailable("MongoDB must be selected to import chats.")
    destination = MongoChatMemoryRepository(settings)
    engine = create_memory_engine(config_path=config_path)
    sessions = create_memory_session_factory(engine)
    source = SQLiteChatMemoryRepository(sessions)
    source_audits = SQLiteToolCallAuditRepository(sessions)
    imported = skipped = 0
    try:
        await destination.initialize()
        async for record in source.iter_migration_sessions():
            messages = await source.list_messages(
                owner_user_id=record.owner_user_id, session_id=record.id
            )
            audits = await source_audits.list_for_chat_session(
                owner_user_id=record.owner_user_id, chat_session_id=record.id
            )
            if await destination.import_session(record, messages, audits):
                imported += 1
            else:
                skipped += 1
    finally:
        await destination.close()
        await engine.dispose()
    return imported, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to a local project.json")
    args = parser.parse_args()
    config_path: Path | None = args.config
    try:
        imported, skipped = asyncio.run(migrate_chat(config_path))
    except ChatImportConflict:
        print(
            "Migration stopped because a chat id conflicts with the destination.", file=sys.stderr
        )
        return 1
    except (RuntimeError, ValueError, PyMongoError, SQLAlchemyError):
        print(
            "Migration stopped. Check the local chat storage and source database.", file=sys.stderr
        )
        return 1
    print(f"Imported {imported} chat sessions. Skipped {skipped} previously imported sessions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
