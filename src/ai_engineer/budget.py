"""Track turn and tool-call budgets for a single harness run."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Budget:
    """Mutable remaining budgets for model turns and tool invocations."""

    max_steps: int = 5
    max_tool_calls: int = 10
    steps_used: int = 0
    tool_calls_used: int = 0

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if self.max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")

    @property
    def steps_remaining(self) -> int:
        return max(0, self.max_steps - self.steps_used)

    @property
    def tool_calls_remaining(self) -> int:
        return max(0, self.max_tool_calls - self.tool_calls_used)

    @property
    def half_steps_consumed(self) -> bool:
        return self.steps_used >= max(1, self.max_steps // 2)

    def consume_step(self) -> None:
        if self.steps_remaining <= 0:
            raise RuntimeError("Model turn budget exhausted")
        self.steps_used += 1

    def can_run_tools(self, count: int) -> bool:
        return count <= self.tool_calls_remaining

    def consume_tools(self, count: int) -> None:
        if count < 0 or not self.can_run_tools(count):
            raise RuntimeError("Tool-call budget exhausted or insufficient for this batch")
        self.tool_calls_used += count

    def snapshot(self) -> dict[str, int]:
        return {
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "steps_used": self.steps_used,
            "tool_calls_used": self.tool_calls_used,
            "steps_remaining": self.steps_remaining,
            "tool_calls_remaining": self.tool_calls_remaining,
        }
