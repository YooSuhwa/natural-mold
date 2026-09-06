"""Evaluate and baseline the backend runtime dependency policy."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, assert_never

from pydantic import BaseModel, ConfigDict, ValidationError

if __package__:
    from .runtime_dependency_graph import DependencyGraph, ImportEdge
else:
    from runtime_dependency_graph import DependencyGraph, ImportEdge

COMMIT_PATTERN: Final = re.compile(r"[0-9a-f]{40}\Z")
MODULE_PATTERN: Final = re.compile(r"app(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+\Z")


class RuleId(StrEnum):
    ROUTERS_TO_MODELS = "routers_to_models"
    SERVICES_TO_ROUTERS = "services_to_routers"
    MODELS_TO_UPPER_LAYERS = "models_to_upper_layers"
    RUNTIME_TO_SERVICES = "runtime_to_services"
    RUNTIME_INTERNAL_TO_FACADE = "runtime_internal_to_facade"


class ViolationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: RuleId
    importer: str
    imported: str


class CyclicEdgeRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    importer: str
    imported: str


class DynamicExceptionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    module: str
    function: str
    callee: str


class RuntimeDependencyBaseline(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
    reviewed_from: str
    historical_base: str
    legacy_violations: tuple[ViolationRecord, ...]
    legacy_cyclic_edges: tuple[CyclicEdgeRecord, ...]
    dynamic_import_exceptions: tuple[DynamicExceptionRecord, ...]


@dataclass(frozen=True, slots=True)
class DependencyPolicyError(Exception):
    """A malformed or noncanonical dependency baseline."""

    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    violations: tuple[ViolationRecord, ...]
    cyclic_edges: tuple[CyclicEdgeRecord, ...]
    dynamic_imports: tuple[DynamicExceptionRecord, ...]


@dataclass(frozen=True, slots=True)
class GuardResult:
    current: PolicySnapshot
    diagnostics: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        return not self.diagnostics


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DependencyPolicyError(f"baseline contains duplicate key: {key}")
        result[key] = value
    return result


def _safe_module(value: str) -> bool:
    return MODULE_PATTERN.fullmatch(value) is not None and "*" not in value


def _safe_function_scope(value: str) -> bool:
    return value == "<module>" or all(part.isidentifier() for part in value.split("."))


type PolicyRecord = ViolationRecord | CyclicEdgeRecord | DynamicExceptionRecord


def _canonical_keys(record: PolicyRecord) -> tuple[str, ...]:
    match record:
        case ViolationRecord(rule=rule, importer=importer, imported=imported):
            return (rule.value, importer, imported)
        case CyclicEdgeRecord(importer=importer, imported=imported):
            return (importer, imported)
        case DynamicExceptionRecord(module=module, function=function, callee=callee):
            return (module, function, callee)
        case unreachable:
            assert_never(unreachable)


def _validate_baseline(baseline: RuntimeDependencyBaseline) -> None:
    if baseline.schema_version != 1:
        raise DependencyPolicyError("baseline schema_version must be 1")
    if COMMIT_PATTERN.fullmatch(baseline.reviewed_from) is None:
        raise DependencyPolicyError("baseline reviewed_from must be a full commit id")
    if COMMIT_PATTERN.fullmatch(baseline.historical_base) is None:
        raise DependencyPolicyError("baseline historical_base must be a full commit id")
    collections = (
        baseline.legacy_violations,
        baseline.legacy_cyclic_edges,
        baseline.dynamic_import_exceptions,
    )
    for records in collections:
        keys = [_canonical_keys(record) for record in records]
        if keys != sorted(set(keys)):
            raise DependencyPolicyError("baseline records must be sorted and unique")
    for record in baseline.legacy_violations:
        if not _safe_module(record.importer) or not _safe_module(record.imported):
            raise DependencyPolicyError("baseline violation contains an unsafe module")
    for record in baseline.legacy_cyclic_edges:
        if not _safe_module(record.importer) or not _safe_module(record.imported):
            raise DependencyPolicyError("baseline cycle contains an unsafe module")
    for record in baseline.dynamic_import_exceptions:
        if (
            not _safe_module(record.module)
            or not _safe_function_scope(record.function)
            or record.callee not in {"__import__", "importlib.import_module"}
        ):
            raise DependencyPolicyError("baseline dynamic exception is unsafe")


def load_baseline(path: Path) -> RuntimeDependencyBaseline:
    """Parse a strict, duplicate-key-safe baseline document."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise DependencyPolicyError("baseline is not readable UTF-8") from error
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object)
        baseline = RuntimeDependencyBaseline.model_validate(data)
    except json.JSONDecodeError as error:
        raise DependencyPolicyError("baseline is not valid JSON") from error
    except ValidationError as error:
        raise DependencyPolicyError("baseline does not match the strict schema") from error
    _validate_baseline(baseline)
    expected = (
        json.dumps(
            baseline.model_dump(mode="json"), ensure_ascii=False, indent=2, separators=(",", ": ")
        )
        + "\n"
    )
    if raw != expected:
        raise DependencyPolicyError("baseline JSON is not canonical")
    return baseline


def _in_package(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def _rule_for(edge: ImportEdge) -> RuleId | None:
    importer = edge.importer
    imported = edge.imported
    if _in_package(importer, "app.routers") and _in_package(imported, "app.models"):
        return RuleId.ROUTERS_TO_MODELS
    if _in_package(importer, "app.services") and _in_package(imported, "app.routers"):
        return RuleId.SERVICES_TO_ROUTERS
    upper_layer = any(
        _in_package(imported, package)
        for package in ("app.routers", "app.services", "app.agent_runtime")
    )
    if _in_package(importer, "app.models") and upper_layer:
        return RuleId.MODELS_TO_UPPER_LAYERS
    if _in_package(importer, "app.agent_runtime") and _in_package(imported, "app.services"):
        return RuleId.RUNTIME_TO_SERVICES
    if (
        _in_package(importer, "app.agent_runtime")
        and importer != "app.agent_runtime.executor"
        and imported == "app.agent_runtime.executor"
    ):
        return RuleId.RUNTIME_INTERNAL_TO_FACADE
    return None


def _runtime_cyclic_edges(graph: DependencyGraph) -> tuple[CyclicEdgeRecord, ...]:
    modules = {
        source.module for source in graph.sources if source.module.startswith("app.agent_runtime")
    }
    edges = {
        (edge.importer, edge.imported)
        for edge in graph.edges
        if edge.importer in modules and edge.imported in modules
    }
    adjacency = {module: set() for module in modules}
    for importer, imported in edges:
        adjacency[importer].add(imported)
    cyclic: set[tuple[str, str]] = set()
    for importer, imported in edges:
        pending = [imported]
        visited: set[str] = set()
        while pending:
            candidate = pending.pop()
            if candidate == importer:
                cyclic.add((importer, imported))
                break
            if candidate not in visited:
                visited.add(candidate)
                pending.extend(adjacency[candidate])
    return tuple(CyclicEdgeRecord(importer=a, imported=b) for a, b in sorted(cyclic))


def analyze(graph: DependencyGraph) -> PolicySnapshot:
    """Return the deterministic set of policy violations and runtime cycles."""
    violations = tuple(
        sorted(
            (
                ViolationRecord(rule=rule, importer=edge.importer, imported=edge.imported)
                for edge in graph.edges
                if (rule := _rule_for(edge)) is not None
            ),
            key=_canonical_keys,
        )
    )
    dynamic = tuple(
        DynamicExceptionRecord(module=site.module, function=site.function, callee=site.callee)
        for site in graph.unresolved_dynamic_imports
    )
    return PolicySnapshot(violations, _runtime_cyclic_edges(graph), dynamic)


def _difference_messages[T: PolicyRecord](
    label: str, observed: set[T], reviewed: set[T]
) -> tuple[str, ...]:
    new = (
        f"new {label}: {_canonical_keys(item)}"
        for item in sorted(observed - reviewed, key=_canonical_keys)
    )
    stale = (
        f"stale {label}: {_canonical_keys(item)}"
        for item in sorted(reviewed - observed, key=_canonical_keys)
    )
    return (*new, *stale)


def check(graph: DependencyGraph, baseline: RuntimeDependencyBaseline) -> GuardResult:
    """Compare the current graph to the exact reviewed legacy baseline."""
    current = analyze(graph)
    diagnostics: list[str] = []
    diagnostics.extend(
        _difference_messages("violation", set(current.violations), set(baseline.legacy_violations))
    )
    diagnostics.extend(
        _difference_messages(
            "cyclic edge", set(current.cyclic_edges), set(baseline.legacy_cyclic_edges)
        )
    )
    diagnostics.extend(
        _difference_messages(
            "dynamic import",
            set(current.dynamic_imports),
            set(baseline.dynamic_import_exceptions),
        )
    )
    return GuardResult(current, tuple(diagnostics))
