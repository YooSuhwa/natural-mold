"""``i29_workflow_approvals``: chain and the checks matching the model."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models.approval_request import APPROVAL_KINDS, APPROVAL_SOURCES, SHAPE_CHECK, _in

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def test_chain_and_checks_match_the_model() -> None:
    spec = importlib.util.spec_from_file_location(
        "i29_test", VERSIONS / "i29_workflow_approvals.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == (
        "i29_workflow_approvals",
        "i28_workflow_runs",
    )
    assert module._SOURCES_AFTER == APPROVAL_SOURCES
    assert module._SHAPE == SHAPE_CHECK
    assert module._in("source", module._SOURCES_AFTER) == _in("source", APPROVAL_SOURCES)
    assert APPROVAL_KINDS == ("tool", "business")
