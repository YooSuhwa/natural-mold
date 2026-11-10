"""``i29_workflow_approvals`` and ``i38_registration_review``: chain and checks vs the model."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from app.models.approval_request import (
    APPROVAL_KINDS,
    APPROVAL_SOURCES,
    KIND_CHECK,
    SHAPE_CHECK,
    _in,
)

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_test", VERSIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chain_and_checks_match_the_model() -> None:
    i29, i38 = _load("i29_workflow_approvals"), _load("i38_registration_review")
    assert (i29.revision, i29.down_revision) == ("i29_workflow_approvals", "i28_workflow_runs")
    assert (i38.revision, i38.down_revision) == ("i38_registration_review", "i37_resource_policies")
    # i38 starts from what i29 left and ends at what the model declares.
    assert i38._SOURCES_BEFORE == i29._SOURCES_AFTER
    assert i38._SHAPE_BEFORE == i29._SHAPE
    assert i38._SOURCES_AFTER == APPROVAL_SOURCES
    assert i38._SHAPE_AFTER == SHAPE_CHECK
    assert i38._in("source", i38._SOURCES_AFTER) == _in("source", APPROVAL_SOURCES)
    assert APPROVAL_KINDS == ("tool", "business", "registration")
    assert KIND_CHECK == "kind IN ('tool','business','registration')"
