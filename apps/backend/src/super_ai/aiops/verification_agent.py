"""A separate, constrained Verification Agent with a durable pause and resume."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, TypedDict, TypeVar, cast

import aiosqlite
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field

from super_ai.aiops.verification import VerificationCandidate, VerificationTemplate
from super_ai.llm import LlmProvider
from super_ai.memory.repositories import JsonDict

_ModelResult = TypeVar("_ModelResult", bound=BaseModel)


class ExperimentSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["propose", "skip"]
    template: VerificationTemplate | None
    hypothesis: str = Field(max_length=240)
    reason: str = Field(min_length=1, max_length=240)


class ExperimentInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["supported", "not_reproduced"]
    summary: str = Field(min_length=1, max_length=320)


class VerificationAgentState(TypedDict, total=False):
    evidence: JsonDict
    decision: JsonDict
    experiment: JsonDict
    interpretation: JsonDict


class VerificationAgent:
    def __init__(self, checkpoint_file: Path, provider: LlmProvider | None) -> None:
        self._checkpoint_file = checkpoint_file
        self._provider = provider

    async def choose(
        self, *, thread_id: str, candidate: VerificationCandidate,
    ) -> tuple[VerificationCandidate | None, JsonDict]:
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        async with self._graph() as graph:
            snapshot = await graph.aget_state(config)
            values = snapshot.values
            if not values:
                values = await graph.ainvoke({
                    "evidence": {
                        "evidenceId": candidate.evidence_id, "traceId": candidate.trace_id,
                        "queueMs": candidate.queue_ms, "synthesisMs": candidate.synthesis_ms,
                        "eligibleTemplate": candidate.template, "environment": "test",
                    },
                }, config)
        state = cast(VerificationAgentState, values)
        decision = state.get("decision", {})
        if decision.get("decision") != "propose":
            return None, decision
        return replace(
            candidate,
            agent_thread_id=thread_id,
            hypothesis=str(decision["hypothesis"]),
            selection_reason=str(decision["reason"]),
        ), decision

    async def interpret(self, *, thread_id: str, experiment: JsonDict) -> JsonDict:
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        async with self._graph() as graph:
            snapshot = await graph.aget_state(config)
            values = cast(VerificationAgentState, snapshot.values)
            if "interpretation" in values:
                return values["interpretation"]
            if not snapshot.interrupts:
                return {
                    "outcome": "inconclusive",
                    "summary": "原验证线程不可用。已保留审批记录和可用的实测证据。",
                }
            resumed = await graph.ainvoke(Command(resume=experiment), config)
            result = cast(VerificationAgentState, resumed)
            return result.get("interpretation", {
                "outcome": "inconclusive", "summary": "未取得验证 Agent 的解释。",
            })

    @asynccontextmanager
    async def _graph(self) -> AsyncGenerator[Any, None]:
        await asyncio.to_thread(self._checkpoint_file.parent.mkdir, parents=True, exist_ok=True)
        async with aiosqlite.connect(self._checkpoint_file) as connection:
            saver = AsyncSqliteSaver(
                connection,
                serde=JsonPlusSerializer(allowed_msgpack_modules=[]),
            )
            builder: Any = StateGraph(VerificationAgentState)
            builder.add_node("choose_template", self._select_template)
            builder.add_node("await_approved_result", _await_approved_result)
            builder.add_node("interpret_result", self._interpret_result)
            builder.add_edge(START, "choose_template")
            builder.add_conditional_edges(
                "choose_template", _next_after_selection,
                {"await": "await_approved_result", "report": END},
            )
            builder.add_edge("await_approved_result", "interpret_result")
            builder.add_edge("interpret_result", END)
            yield builder.compile(checkpointer=saver)

    async def _select_template(self, state: VerificationAgentState) -> VerificationAgentState:
        evidence = state.get("evidence", {})
        selection = await self._invoke(
            ExperimentSelection,
            "你是验证 Agent。根据已授权的测试观察判断是否需要控制实验。"
            "模板仅可为 tts-queue-concurrency（消费并发对照）或 "
            "tts-synthesis-baseline（合成耗时基线）。只能选择观察中允许的模板。"
            "输出 JSON，字段包含 decision（propose 或 skip）、template（或 null）、"
            "hypothesis 和 reason。测试结论只适用于同等测试条件。",
            evidence,
        )
        if selection is None:
            return {"decision": {"decision": "skip", "reason": "验证 Agent 未返回可用的模板决策。"}}
        if selection.decision == "skip":
            return {"decision": {"decision": "skip", "reason": _plain(selection.reason)}}
        if (
            selection.template != evidence.get("eligibleTemplate")
            or not selection.hypothesis.strip()
        ):
            return {"decision": {"decision": "skip", "reason": "建议的实验未通过模板策略校验。"}}
        return {"decision": {
            "decision": "propose", "reason": _plain(selection.reason),
            "template": selection.template, "hypothesis": _plain(selection.hypothesis),
        }}

    async def _interpret_result(self, state: VerificationAgentState) -> VerificationAgentState:
        experiment = state.get("experiment", {})
        status = experiment.get("status")
        if status != "succeeded":
            summaries = {
                "rejected": "审批未通过，保留基于现有证据的结论。",
                "expired": "审批已过期，未取得实验结果。",
                "cancelled": "验证已取消，未取得完整实验结果。",
                "failed": "实验执行失败，当前假设仍需验证。",
            }
            key = status if isinstance(status, str) else ""
            return {"interpretation": {
                "outcome": key or "inconclusive",
                "summary": summaries.get(key, "未取得可用的实验结果。"),
            }}
        outcome = experiment.get("outcome")
        if outcome not in ("supported", "not_reproduced"):
            return {"interpretation": {
                "outcome": "inconclusive", "summary": "对照实验缺少可用于判断的信号。",
            }}
        explanation = await self._invoke(
            ExperimentInterpretation,
            "你是验证 Agent。只解读给定对照实验的实际输出和假设，保留限制。"
            "输出 JSON，字段 outcome 必须等于输入的 outcome，summary 为中文。"
            "supported 只代表相同测试条件下的证据，not_reproduced 表示未复现。"
            "不要联系生产环境或补充未经观察的数据。",
            {"hypothesis": state.get("decision", {}).get("hypothesis"), **experiment},
        )
        fallback = (
            "实测对照支持该测试假设。" if outcome == "supported" else "本次对照实验未复现假设。"
        )
        summary = (
            _plain(explanation.summary) if explanation is not None
            and explanation.outcome == outcome else fallback
        )
        return {"interpretation": {
            "outcome": outcome, "summary": f"{summary} 结论适用于本次测试条件。",
        }}

    async def _invoke(
        self, schema: type[_ModelResult], instruction: str, context: JsonDict,
    ) -> _ModelResult | None:
        if self._provider is None:
            return None
        try:
            response = await asyncio.wait_for(
                self._provider.create_chat_model().ainvoke([
                    ("system", instruction), ("human", json.dumps(context, ensure_ascii=False)),
                ]), timeout=20,
            )
            content = response if isinstance(response, str) else getattr(response, "content", None)
            if not isinstance(content, str):
                return None
            content = content.strip()
            if content.startswith("```json\n") and content.endswith("```"):
                content = content[8:-3].strip()
            return schema.model_validate_json(content)
        except Exception:
            return None


def _await_approved_result(state: VerificationAgentState) -> VerificationAgentState:
    response = interrupt({
        "kind": "approved-sandbox-result", "traceId": state.get("evidence", {}).get("traceId"),
    })
    return {"experiment": cast(JsonDict, response)}


def _next_after_selection(state: VerificationAgentState) -> Literal["await", "report"]:
    return "await" if state.get("decision", {}).get("decision") == "propose" else "report"


def _plain(value: str) -> str:
    return " ".join(value.split())
