"""Model ↔ tools turn runner with budgets and session metadata."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
# Soft target for newest non-system messages after eviction (atomic tool groups may exceed it).
MAX_HISTORY_MESSAGES = 24


class RequestBudgetError(RuntimeError):
    """Transcript exceeded the character budget after compaction/eviction."""


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

    def clear_history(self) -> None:
        """Drop conversation history and rebuild the system prompt."""
        self.messages = []
        self._skills = load_skill_snippets(self.workspace.root)
        self._reset_system()
        if self.session is not None:
            self.session.write({"type": "history_cleared"})

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

    @staticmethod
    def _history_groups(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """Group assistant tool_calls with their following tool results (atomic units)."""
        groups: list[list[dict[str, Any]]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            tool_calls = message.get("tool_calls")
            if message.get("role") == "assistant" and isinstance(tool_calls, list) and tool_calls:
                call_ids = {
                    call.get("id")
                    for call in tool_calls
                    if isinstance(call, dict) and call.get("id")
                }
                group = [message]
                index += 1
                while index < len(messages) and messages[index].get("role") == "tool":
                    tool_id = messages[index].get("tool_call_id")
                    if call_ids and tool_id not in call_ids:
                        break
                    group.append(messages[index])
                    index += 1
                groups.append(group)
                continue
            groups.append([message])
            index += 1
        return groups

    def _assert_transcript_invariants(self) -> None:
        """Fail loudly if tool_call pairing/order is broken before a provider call."""
        open_ids: set[str] | None = None
        for index, message in enumerate(self.messages):
            role = message.get("role")
            if open_ids is not None and role != "tool":
                raise RuntimeError(
                    f"Transcript invariant broken at {index}: expected tool results "
                    f"for {sorted(open_ids)}"
                )
            if role == "assistant" and isinstance(message.get("tool_calls"), list):
                tool_calls = message["tool_calls"]
                if tool_calls:
                    open_ids = {
                        call.get("id")
                        for call in tool_calls
                        if isinstance(call, dict) and call.get("id")
                    }
                    if not open_ids:
                        open_ids = None
                continue
            if role == "tool":
                tool_id = message.get("tool_call_id")
                if open_ids is None:
                    raise RuntimeError(
                        f"Transcript invariant broken at {index}: orphan tool message"
                    )
                if tool_id not in open_ids:
                    raise RuntimeError(
                        f"Transcript invariant broken at {index}: unexpected tool_call_id"
                    )
                open_ids.remove(tool_id)
                if not open_ids:
                    open_ids = None
                continue
        if open_ids:
            raise RuntimeError(
                f"Transcript invariant broken: unanswered tool_call_ids {sorted(open_ids)}"
            )

    def _backfill_pending_tool_results(self, error: str) -> None:
        """Insert missing tool messages immediately after each open assistant group."""
        content = self._truncate({"error": error})
        rebuilt: list[dict[str, Any]] = []
        for group in self._history_groups(self.messages):
            rebuilt.extend(group)
            head = group[0]
            tool_calls = head.get("tool_calls")
            if (
                head.get("role") != "assistant"
                or not isinstance(tool_calls, list)
                or not tool_calls
            ):
                continue
            answered = {
                message.get("tool_call_id")
                for message in group[1:]
                if message.get("role") == "tool"
            }
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                call_id = call.get("id")
                if not call_id or call_id in answered:
                    continue
                rebuilt.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": content,
                    }
                )
        self.messages = rebuilt

    def _safe_backfill_pending_tool_results(self, error: str) -> None:
        """Best-effort transcript repair; never raise out of recovery handlers."""
        try:
            self._backfill_pending_tool_results(error)
            self._assert_transcript_invariants()
            return
        except Exception:  # noqa: BLE001, S110 - recovery must never raise
            pass
        # Last resort: drop trailing incomplete assistant/tool tail.
        try:
            while self.messages:
                last = self.messages[-1]
                if last.get("role") == "tool":
                    self.messages.pop()
                    continue
                if last.get("role") == "assistant" and last.get("tool_calls"):
                    self.messages.pop()
                    continue
                break
            self._assert_transcript_invariants()
        except Exception:  # noqa: BLE001, S110 - last-resort repair must never raise
            pass

    def _evict_old_messages(self) -> None:
        """Drop oldest non-system turn groups until the transcript fits a soft budget."""
        target = int(MAX_REQUEST_CHARS * 0.75)
        if self._message_chars() <= target:
            return
        system = [message for message in self.messages if message.get("role") == "system"]
        rest = [message for message in self.messages if message.get("role") != "system"]
        floor = max(4, MAX_HISTORY_MESSAGES // 2)
        groups = self._history_groups(rest)
        pinned: list[list[dict[str, Any]]] = []
        # Keep the first user message so wrap-up still sees the initiating question.
        if groups and groups[0] and groups[0][0].get("role") == "user":
            pinned.append(groups.pop(0))

        def flatten(items: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
            return [message for group in items for message in group]

        def sized(messages: list[dict[str, Any]]) -> int:
            return len(json.dumps(messages, ensure_ascii=False))

        dropped_roles: list[str] = []
        while (
            len(groups) > 1
            and len(flatten(pinned + groups)) > floor
            and sized(system + flatten(pinned + groups)) > target
        ):
            dropped = groups.pop(0)
            dropped_roles.extend(str(message.get("role")) for message in dropped)
        while len(groups) > 1 and len(flatten(pinned + groups)) > MAX_HISTORY_MESSAGES:
            if len(flatten(pinned + groups[1:])) < floor:
                break
            dropped = groups.pop(0)
            dropped_roles.extend(str(message.get("role")) for message in dropped)
        rest = flatten(pinned + groups)
        self.messages = system + rest
        self._assert_transcript_invariants()
        if self.session is not None:
            self.session.write(
                {
                    "type": "history_evicted",
                    "chars": self._message_chars(),
                    "kept": len(rest),
                    "dropped_roles": dropped_roles[:40],
                }
            )

    def _ensure_request_budget(self) -> None:
        self._compact_messages()
        if self._message_chars() > MAX_REQUEST_CHARS:
            self._evict_old_messages()
            self._compact_messages()
        if self._message_chars() > MAX_REQUEST_CHARS:
            raise RequestBudgetError(
                f"Conversation exceeds the {MAX_REQUEST_CHARS} character request budget. "
                "Run /clear in the REPL (or start a new one-shot) and try a narrower question."
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
        self._evict_old_messages()
        nudge = {
            "role": "user",
            "content": (
                "Tools are disabled for this final turn. Using only evidence already "
                "gathered, write the complete answer now (findings, strengths, risks, "
                "and concrete next steps). Do not request tools."
            ),
        }
        self._reset_system()
        self._ensure_request_budget()
        self._assert_transcript_invariants()
        self.progress("wrap-up (answer only)")
        if self.session is not None:
            self.session.write({"type": "wrap_up", "status": status})
        # Do not leave the synthetic nudge in REPL history.
        response = self.provider.complete([*self.messages, nudge], [])
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
                self._assert_transcript_invariants()
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
                        # Answer-only / tool-budget empty: still produce a real wrap-up.
                        if response.content:
                            return self._finalize_content(response.content, status="blocked")
                        return self._wrap_up(status="blocked")
                    self._execute_tools(calls)
                    self._assert_transcript_invariants()
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
        except KeyboardInterrupt:
            self._safe_backfill_pending_tool_results("Interrupted before the tool completed.")
            answer = (
                "Interrupted. The turn stopped early and the transcript was repaired so you "
                "can continue, or run /clear."
            )
            if self.session is not None:
                self.session.write({"type": "completed", "status": "blocked", "error": True})
            self.messages.append({"role": "assistant", "content": answer})
            return RunResult(answer=answer, status="blocked", budget=self.budget.snapshot())
        except RequestBudgetError as error:
            try:
                self._compact_messages()
                self._evict_old_messages()
                return self._wrap_up(status="blocked")
            except Exception as wrap_error:  # noqa: BLE001 - wrap-up is best effort
                if self.session is not None:
                    self.session.write(
                        {
                            "type": "wrap_up_failed",
                            "error": str(wrap_error),
                        }
                    )
            answer = str(error)
            if "/clear" not in answer:
                answer += " Run /clear in the REPL, then retry."
            self._safe_backfill_pending_tool_results(answer)
            if self.session is not None:
                self.session.write({"type": "completed", "status": "blocked", "error": True})
            if not self.messages or self.messages[-1].get("role") != "assistant":
                self.messages.append({"role": "assistant", "content": answer})
            return RunResult(answer=answer, status="blocked", budget=self.budget.snapshot())
        except Exception as error:  # noqa: BLE001 - surface any failure as a failed turn
            self._safe_backfill_pending_tool_results(f"Turn failed: {error}")
            answer = f"Provider or tool failure: {error}"
            if self.session is not None:
                self.session.write({"type": "completed", "status": "failed", "error": True})
            return RunResult(answer=answer, status="failed", budget=self.budget.snapshot())
