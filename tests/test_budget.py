"""Budget enforcement tests."""

from __future__ import annotations

import pytest

from ai_engineer.budget import Budget


def test_consume_step_and_tools() -> None:
    budget = Budget(max_steps=2, max_tool_calls=3)
    budget.consume_step()
    budget.consume_tools(2)
    assert budget.steps_remaining == 1
    assert budget.tool_calls_remaining == 1
    assert budget.half_steps_consumed is True


def test_step_exhaustion() -> None:
    budget = Budget(max_steps=1, max_tool_calls=5)
    budget.consume_step()
    with pytest.raises(RuntimeError, match="turn budget"):
        budget.consume_step()


def test_tool_batch_rejected_when_insufficient() -> None:
    budget = Budget(max_steps=5, max_tool_calls=2)
    assert budget.can_run_tools(3) is False
    with pytest.raises(RuntimeError, match="Tool-call"):
        budget.consume_tools(3)
