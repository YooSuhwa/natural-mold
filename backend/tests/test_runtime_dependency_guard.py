"""Public behavior contracts for the backend dependency-direction guard."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_dependency_graph import ModuleSource, scan_sources  # noqa: E402
from runtime_dependency_policy import (  # noqa: E402
    CyclicEdgeRecord,
    DynamicExceptionRecord,
    RuleId,
    RuntimeDependencyBaseline,
    ViolationRecord,
    check,
)


def _source(module: str, source: str = "") -> ModuleSource:
    return ModuleSource(module, f"{module.replace('.', '/')}.py", source)


def _baseline(
    *,
    violations: tuple[ViolationRecord, ...] = (),
    cycles: tuple[CyclicEdgeRecord, ...] = (),
    dynamic: tuple[DynamicExceptionRecord, ...] = (),
) -> RuntimeDependencyBaseline:
    return RuntimeDependencyBaseline(
        schema_version=1,
        reviewed_from="0" * 40,
        historical_base="1" * 40,
        legacy_violations=violations,
        legacy_cyclic_edges=cycles,
        dynamic_import_exceptions=dynamic,
    )


def _result(*sources: ModuleSource, baseline: RuntimeDependencyBaseline | None = None):
    return check(scan_sources(tuple(sources)), baseline or _baseline())


def test_real_checkout_matches_reviewed_baseline_without_mutating_it() -> None:
    from check_runtime_dependencies import DEFAULT_BACKEND_ROOT, DEFAULT_BASELINE, CliOptions, run

    before = DEFAULT_BASELINE.read_bytes()
    exit_code, messages = run(CliOptions(DEFAULT_BACKEND_ROOT, DEFAULT_BASELINE))

    assert exit_code == 0, "\n".join(messages)
    assert DEFAULT_BASELINE.read_bytes() == before


@pytest.mark.parametrize(
    ("importer", "target", "rule"),
    [
        ("app.routers.handler", "app.models.user", RuleId.ROUTERS_TO_MODELS),
        ("app.services.worker", "app.routers.api", RuleId.SERVICES_TO_ROUTERS),
        ("app.models.user", "app.routers.api", RuleId.MODELS_TO_UPPER_LAYERS),
        ("app.models.user", "app.services.worker", RuleId.MODELS_TO_UPPER_LAYERS),
        ("app.models.user", "app.agent_runtime.runner", RuleId.MODELS_TO_UPPER_LAYERS),
        ("app.agent_runtime.runner", "app.services.worker", RuleId.RUNTIME_TO_SERVICES),
        (
            "app.agent_runtime.runner",
            "app.agent_runtime.executor",
            RuleId.RUNTIME_INTERNAL_TO_FACADE,
        ),
    ],
)
def test_guard_rejects_each_forbidden_direction(importer: str, target: str, rule: RuleId) -> None:
    result = _result(_source(target), _source(importer, f"import {target}\n"))

    assert not result.is_clean
    assert result.current.violations == (
        ViolationRecord(rule=rule, importer=importer, imported=target),
    )


def test_guard_allows_service_to_runtime_dependency() -> None:
    result = _result(
        _source("app.agent_runtime.identity"),
        _source("app.services.worker", "import app.agent_runtime.identity\n"),
    )

    assert result.is_clean


def test_guard_rejects_unreviewed_dynamic_internal_import() -> None:
    result = _result(
        _source("app.agent_runtime.dynamic", "def load(name: str):\n    return __import__(name)\n")
    )

    assert result.diagnostics == (
        "new dynamic import: ('app.agent_runtime.dynamic', 'load', '__import__')",
    )


def test_reviewed_class_dynamic_site_cannot_mask_same_named_method() -> None:
    source = _source(
        "app.agent_runtime.dynamic",
        "class A:\n    def load(self, name: str):\n        return __import__(name)\n\n"
        "class B:\n    def load(self, name: str):\n        return __import__(name)\n",
    )
    reviewed = DynamicExceptionRecord(
        module="app.agent_runtime.dynamic", function="A.load", callee="__import__"
    )

    result = _result(source, baseline=_baseline(dynamic=(reviewed,)))

    assert result.diagnostics == (
        "new dynamic import: ('app.agent_runtime.dynamic', 'B.load', '__import__')",
    )


def test_guard_rejects_new_two_node_and_self_runtime_cycles() -> None:
    two_node = _result(
        _source("app.agent_runtime.first", "import app.agent_runtime.second\n"),
        _source("app.agent_runtime.second", "import app.agent_runtime.first\n"),
    )
    self_cycle = _result(_source("app.agent_runtime.loop", "import app.agent_runtime.loop\n"))

    assert sum(item.startswith("new cyclic edge") for item in two_node.diagnostics) == 2
    assert sum(item.startswith("new cyclic edge") for item in self_cycle.diagnostics) == 1


def test_guard_rejects_new_edge_inside_reviewed_runtime_cycle() -> None:
    reviewed_sources = (
        _source("app.agent_runtime.first", "import app.agent_runtime.second\n"),
        _source("app.agent_runtime.second", "import app.agent_runtime.first\n"),
    )
    reviewed = _result(*reviewed_sources).current.cyclic_edges
    expanded = _result(
        _source(
            "app.agent_runtime.first",
            "import app.agent_runtime.second\nimport app.agent_runtime.third\n",
        ),
        _source("app.agent_runtime.second", "import app.agent_runtime.first\n"),
        _source("app.agent_runtime.third", "import app.agent_runtime.first\n"),
        baseline=_baseline(cycles=reviewed),
    )

    assert any(item.startswith("new cyclic edge") for item in expanded.diagnostics)


def test_violation_delete_and_rename_require_explicit_baseline_review() -> None:
    old = ViolationRecord(
        rule=RuleId.ROUTERS_TO_MODELS,
        importer="app.routers.old",
        imported="app.models.user",
    )
    baseline = _baseline(violations=(old,))
    deleted = _result(_source("app.models.user"), baseline=baseline)
    renamed = _result(
        _source("app.models.user"),
        _source("app.routers.new", "import app.models.user\n"),
        baseline=baseline,
    )

    expected = f"stale violation: {('routers_to_models', old.importer, old.imported)}"
    assert deleted.diagnostics == (expected,)
    assert len(renamed.diagnostics) == 2
    assert renamed.diagnostics[0].startswith("new violation")
    assert renamed.diagnostics[1].startswith("stale violation")


def test_clean_file_delete_or_rename_does_not_require_baseline_edit() -> None:
    deleted = _result(_source("app.models.user"))
    renamed = _result(
        _source("app.models.user"),
        _source("app.routers.new", "import typing\n"),
    )

    assert deleted.is_clean
    assert renamed.is_clean
