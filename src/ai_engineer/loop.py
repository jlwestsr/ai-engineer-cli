"""Model ↔ tools turn runner with budgets and session metadata."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from ai_engineer.budget import Budget
from ai_engineer.instructions import build_system_message, load_skill_snippets
from ai_engineer.provider import ModelResponse, Provider, ToolCall
from ai_engineer.session import SessionStore
from ai_engineer.tools.registry import ToolRegistry
from ai_engineer.workspace import WorkspaceTools

ProgressFn = Callable[[str], None]
MAX_TOOL_RESULT_CHARS = 16_000
MAX_REQUEST_CHARS = 120_000
DEFAULT_MAX_TOOLS_PER_TURN = 6
COMPACT_TOOL_CHARS = 1_200


@dataclass
class RunResult:
    answer: str
    status: str  # done | failed | blocked
    budget: dict[str, int]


class AgentLoop:
    """Own conversation state and dispatch registered tools under a budget."""

    def __init__(
        self,
        provider: Provider,
        workspace: WorkspaceTools,
        registry: ToolRegistry,
        budget: Budget,
        session: SessionStore | None = None,
        progress: ProgressFn | None = None,
        max_tools_per_turn: int = DEFAULT_MAX_TOOLS_PER_TURN,
    ) -> None:
        if max_tools_per_turn < 1:
            raise ValueError("max_tools_per_turn must be at least 1")
        self.provider = provider
        self.workspace = workspace
        self.registry = registry
        self.budget = budget
        self.session = session
        self.progress = progress or (lambda _msg: None)
        self.max_tools_per_turn = max_tools_per_turn
        self.messages: list[dict[str, Any]] = []
        self._skills = load_skill_snippets(workspace.root)
        self._reset_system()

    def _reset_system(self) -> None:
        system = build_system_message(
            self.workspace.context(),
            reflect=self.budget.half_steps_consumed,
            skills=self._skills,
        )
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = {"role": "system", "content": system}
        else:
            self.messages.insert(0, {"role": "system", "content": system})

    def refresh_tools(self) -> None:
        self.workspace.refresh_registry(self.registry)
        self._reset_system()

    def _truncate(self, payload: Any) -> str:
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) <= MAX_TOOL_RESULT_CHARS:
            return text
        return json.dumps(
            {
                "truncated": True,
                "preview": text[: MAX_TOOL_RESULT_CHARS - 80],
            },
            ensure_ascii=False,
        )

    def _message_chars(self) -> int:
        return len(json.dumps(self.messages, ensure_ascii=False))

    def _compact_messages(self) -> None:
        """Shrink oversized tool payloads when the transcript exceeds the budget."""
        if self._message_chars() <= MAX_REQUEST_CHARS:
            return
        for message in self.messages:
            if message.get("role") != "tool":
                continue
            content = message.get("content")
            if not isinstance(content, str) or len(content) <= COMPACT_TOOL_CHARS:
                continue
            if content.lstrip().startswith('{"compacted":'):
                continue
            message["content"] = json.dumps(
                {
                    "compacted": True,
                    "preview": content[:COMPACT_TOOL_CHARS],
                },
                ensure_ascii=False,
            )
        if self.session is not None:
            self.session.write({"type": "compact", "chars": self._message_chars()})

    def _ensure_request_budget(self) -> None:
        self._compact_messages()
        if self._message_chars() > MAX_REQUEST_CHARS:
            raise RuntimeError(
                f"Conversation exceeds the {MAX_REQUEST_CHARS} character request budget"
            )

    def _tool_definitions(self, *, allow_tools: bool) -> list[dict[str, Any]]:
        if not allow_tools or self.budget.tool_calls_remaining <= 0:
            return []
        return self.registry.definitions()

    def _execute_tools(self, calls: list[ToolCall]) -> None:
        """Run tools under per-turn and session budgets; defer the rest with errors."""
        accept_limit = min(self.max_tools_per_turn, self.budget.tool_calls_remaining)
        if accept_limit <= 0:
            accepted: list[ToolCall] = []
            deferred = calls
            defer_reason = (
                "Tool-call budget exhausted. Synthesize an answer from evidence already "
                "gathered; do not request more tools."
            )
        else:
            accepted = calls[:accept_limit]
            deferred = calls[accept_limit:]
            defer_reason = (
                f"Deferred: at most {self.max_tools_per_turn} tools run per model turn "
                f"({self.budget.tool_calls_remaining} remain in this message budget). "
                "Prioritize fewer, higher-value reads next, or answer with current evidence."
            )

        if accepted:
            self.budget.consume_tools(len(accepted))

        assistant_tool_calls = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in calls
        ]
        self.messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": assistant_tool_calls,
            }
        )

        for call in accepted:
            started = time.monotonic()
            self.progress(f"tool {call.name}")
            result = self.registry.execute(call.name, call.arguments)
            duration_ms = int((time.monotonic() - started) * 1000)
            error = isinstance(result, dict) and "error" in result
            if self.session is not None:
                self.session.write(
                    {
                        "type": "tool",
                        "name": call.name,
                        "duration_ms": duration_ms,
                        "error": error,
                    }
                )
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": self._truncate(result),
                }
            )

        for call in deferred:
            self.progress(f"defer {call.name}")
            if self.session is not None:
                self.session.write({"type": "tool_deferred", "name": call.name})
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": self._truncate({"error": defer_reason}),
                }
            )

    def _finalize_content(self, content: str | None, *, status: str) -> RunResult:
        answer = content or (
            "Stopped before a final answer. Raise AIE_MAX_STEPS / AIE_MAX_TOOL_CALLS, "
            "or ask a narrower question."
        )
        self.messages.append({"role": "assistant", "content": answer})
        if self.session is not None:
            self.session.write({"type": "completed", "status": status})
        return RunResult(answer=answer, status=status, budget=self.budget.snapshot())

    def _wrap_up(self, *, status: str = "done") -> RunResult:
        """One answer-only completion that does not consume the step budget."""
        self._compact_messages()
        self.messages.append(
            {
                "role": "user",
                "content": (
                    "Tools are disabled for this final turn. Using only evidence already "
                    "gathered, write the complete answer now (findings, strengths, risks, "
                    "and concrete next steps). Do not request tools."
                ),
            }
        )
        self._reset_system()
        self._ensure_request_budget()
        self.progress("wrap-up (answer only)")
        if self.session is not None:
            self.session.write({"type": "wrap_up", "status": status})
        response = self.provider.complete(self.messages, [])
        if response.tool_calls and not response.content:
            return self._finalize_content(
                "Could not obtain a final narrative after the tool phase. "
                "Ask a narrower follow-up, or raise AIE_MAX_STEPS.",
                status="blocked",
            )
        if response.content is None:
            return self._finalize_content(None, status="failed")
        return self._finalize_content(response.content, status=status)

    def run_turn(self, user_text: str) -> RunResult:
        """Run until a final answer or budget/error stop for one user message.

        The last budgeted step is answer-only (no tools). If tool rounds use every
        step, a free wrap-up completion still asks for a text answer.
        """
        self.messages.append({"role": "user", "content": user_text})
        try:
            while True:
                if self.budget.steps_remaining <= 0:
                    return self._wrap_up(status="blocked")

                # Reserve the final budgeted step for a text answer.
                answer_only = self.budget.steps_remaining == 1
                self.budget.consume_step()
                self._reset_system()
                self._ensure_request_budget()
                if self.session is not None:
                    self.session.write(
                        {
                            "type": "model_turn",
                            "steps_used": self.budget.steps_used,
                            "tool_calls_used": self.budget.tool_calls_used,
                            "answer_only": answer_only,
                        }
                    )
                self.progress(
                    f"model turn {self.budget.steps_used}/{self.budget.max_steps} "
                    f"(tools {self.budget.tool_calls_used}/{self.budget.max_tool_calls})"
                    + (" [answer only]" if answer_only else "")
                )
                tools = self._tool_definitions(allow_tools=not answer_only)
                response: ModelResponse = self.provider.complete(self.messages, tools)
                calls = response.tool_calls or []
                if calls:
                    if not tools:
                        return self._finalize_content(
                            response.content
                            or "Turn budget reached before more tools could run. "
                            "Ask a follow-up to continue, or raise AIE_MAX_STEPS.",
                            status="blocked",
                        )
                    self._execute_tools(calls)
                    self._compact_messages()
                    if self.budget.steps_remaining <= 0:
                        return self._wrap_up(status="done")
                    # Context getting heavy: stop tool looping and answer.
                    if self._message_chars() > int(MAX_REQUEST_CHARS * 0.9):
                        self.progress("context high — forcing wrap-up")
                        return self._wrap_up(status="done")
                    continue
                if response.content is None:
                    raise RuntimeError("Provider returned neither content nor tool calls")
                return self._finalize_content(response.content, status="done")
        except RuntimeError as error:
            if "request budget" in str(error).lower():
                try:
                    self._compact_messages()
                    return self._wrap_up(status="blocked")
                except RuntimeError:
                    pass
            status = "blocked" if "budget" in str(error).lower() else "failed"
            if self.session is not None:
                self.session.write({"type": "completed", "status": status, "error": True})
            return RunResult(answer=str(error), status=status, budget=self.budget.snapshot())
