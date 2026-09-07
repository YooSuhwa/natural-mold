from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, overload

from app.schemas.skill_builder import JsonValue

type JsonObject = dict[str, JsonValue]


class SkillEvaluationKpis(dict[str, JsonValue]):
    @overload
    def __getitem__(
        self,
        key: Literal[
            "pass_rate",
            "trigger_accuracy",
            "average_duration_ms",
            "average_tokens",
            "error_count",
        ],
    ) -> JsonObject: ...

    @overload
    def __getitem__(self, key: str) -> JsonValue: ...

    def __getitem__(self, key: str) -> JsonValue:
        return super().__getitem__(key)


class SkillEvaluationSummary(dict[str, JsonValue]):
    def __init__(self, values: Mapping[str, JsonValue] | None = None) -> None:
        super().__init__(values or {})
        kpis = self.get("kpis")
        if isinstance(kpis, dict) and not isinstance(kpis, SkillEvaluationKpis):
            super().__setitem__("kpis", SkillEvaluationKpis(kpis))

    @overload
    def __getitem__(self, key: Literal["kpis"]) -> SkillEvaluationKpis: ...

    @overload
    def __getitem__(self, key: Literal["execution_metrics", "timing"]) -> JsonObject: ...

    @overload
    def __getitem__(self, key: str) -> JsonValue: ...

    def __getitem__(self, key: str) -> JsonValue:
        return super().__getitem__(key)


class SkillEvaluationBenchmarkComparison(dict[str, JsonValue]):
    @overload
    def __getitem__(
        self,
        key: Literal["pass_rate", "duration_ms", "tokens"],
    ) -> JsonObject: ...

    @overload
    def __getitem__(self, key: str) -> JsonValue: ...

    def __getitem__(self, key: str) -> JsonValue:
        return super().__getitem__(key)


class SkillEvaluationBenchmark(dict[str, JsonValue]):
    def __init__(self, values: Mapping[str, JsonValue] | None = None) -> None:
        super().__init__(values or {})
        comparison = self.get("comparison")
        if isinstance(comparison, dict) and not isinstance(
            comparison, SkillEvaluationBenchmarkComparison
        ):
            super().__setitem__(
                "comparison",
                SkillEvaluationBenchmarkComparison(comparison),
            )

    @overload
    def __getitem__(self, key: Literal["comparison"]) -> SkillEvaluationBenchmarkComparison: ...

    @overload
    def __getitem__(self, key: str) -> JsonValue: ...

    def __getitem__(self, key: str) -> JsonValue:
        return super().__getitem__(key)
