"""Run-local skill execution capability supplied by the conversation worker."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from app.agent_runtime.skill_sandbox import SkillRunResult
    from app.marketplace.skill_runtime import SkillRuntimeDescriptor, SkillToolContext


class GoalSandboxRunner(Protocol):
    async def __call__(
        self,
        ctx: SkillToolContext,
        descriptor: SkillRuntimeDescriptor,
        args: list[str],
        *,
        skill_dir: Path,
        env: dict[str, str],
        timeout_seconds: float,
    ) -> SkillRunResult | None: ...


@dataclass(slots=True)
class _RunScope:
    run_id: str
    runner: GoalSandboxRunner | None


_scope: ContextVar[_RunScope | None] = ContextVar("goal_sandbox_scope", default=None)


@contextmanager
def goal_sandbox_scope(run_id: str, runner: GoalSandboxRunner) -> Iterator[None]:
    scope = _RunScope(run_id, runner)
    token = _scope.set(scope)
    try:
        yield
    finally:
        # A copied task context may outlive the worker that authorized this callback.
        scope.runner = None
        _scope.reset(token)


def goal_sandbox_runner_for(run_id: str | None) -> GoalSandboxRunner | None:
    scope = _scope.get()
    return scope.runner if scope is not None and scope.run_id == run_id else None
