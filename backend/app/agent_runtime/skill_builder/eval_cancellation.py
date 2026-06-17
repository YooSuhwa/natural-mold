from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class EvalCancellationPhase(StrEnum):
    START = "start"
    WITH_SKILL_CASE = "with_skill_case"
    SUBPROCESS_TIMEOUT = "subprocess_timeout"
    BASELINE_CASE = "baseline_case"
    GRADING = "grading"
    AGGREGATION = "aggregation"


@dataclass(frozen=True, slots=True)
class EvalCancellationCheckpoint:
    phase: EvalCancellationPhase
    case_index: int | None = None


@dataclass(frozen=True, slots=True)
class EvalRunCancelled(RuntimeError):
    checkpoint: EvalCancellationCheckpoint

    def __str__(self) -> str:
        return f"evaluation cancelled at {self.checkpoint.phase.value}"


class EvalCancellationProbe(Protocol):
    async def raise_if_cancelled(self, checkpoint: EvalCancellationCheckpoint) -> None: ...


@dataclass(frozen=True, slots=True)
class NoopEvalCancellationProbe:
    async def raise_if_cancelled(self, checkpoint: EvalCancellationCheckpoint) -> None:
        return None
