from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest

from super_ai.mcp_client import LocalMcpClient, McpClientError, McpServerConnection


@dataclass(frozen=True, slots=True)
class FakeMcpContent:
    type: str
    text: str


@dataclass(frozen=True, slots=True)
class FakeMcpResult:
    isError: bool
    content: list[FakeMcpContent]


class FakeLocalMcpClient(LocalMcpClient):
    async def _run(self, operation: Callable[[Any], Awaitable[Any]]) -> Any:
        del operation
        return FakeMcpResult(
            isError=False,
            content=[FakeMcpContent(type="text", text="mcp-output-secret")],
        )


@pytest.mark.parametrize("transport", ["sse", "streamable_http"])
async def test_mcp_handshake_stall_is_bounded_and_retried(
    transport: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    cleaned = 0

    async def stall(
        _connection: McpServerConnection, _operation: Callable[[Any], Awaitable[Any]],
    ) -> None:
        nonlocal attempts, cleaned
        attempts += 1
        try:
            await asyncio.Event().wait()
        finally:
            cleaned += 1

    client = LocalMcpClient(connections=[McpServerConnection(
        name="test", url="http://localhost/mcp", transport=transport,
        timeout_seconds=.05, retries=1,
    )])
    method = "_run_sse" if transport == "sse" else "_run_streamable_http"
    monkeypatch.setattr(client, method, stall)
    with pytest.raises(McpClientError, match="MCP server unavailable"):
        await asyncio.wait_for(client.discover_tools(), timeout=2)
    assert attempts == cleaned == 2


@pytest.mark.asyncio
async def test_mcp_tool_logs_lifecycle_without_argument_values_or_output(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logging.getLogger("super_ai.mcp_client").disabled = False
    caplog.set_level(logging.INFO, logger="super_ai.mcp_client")
    client = FakeLocalMcpClient("http://mcp.test/sse")

    result = await client.call_tool(
        "SearchLog",
        {"query": "query-secret", "topicId": "topic-secret"},
    )

    assert result == [{"type": "text", "text": "mcp-output-secret"}]
    events = [
        json.loads(record.message) for record in caplog.records if record.message.startswith("{")
    ]
    assert [event["event"] for event in events] == [
        "mcp.tool.started",
        "mcp.tool.completed",
    ]
    assert events[0]["argumentKeys"] == ["query", "topicId"]
    emitted = "\n".join(record.message for record in caplog.records)
    assert "query-secret" not in emitted
    assert "topic-secret" not in emitted
    assert "mcp-output-secret" not in emitted
