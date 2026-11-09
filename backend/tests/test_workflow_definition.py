"""Workflow W1: the ``moldy.workflow.v1`` shape and the pre-publish graph checks."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from app.workflows.checks import graph_issues
from app.workflows.definition import MAX_NODES, WorkflowDefinition, empty_definition
from tests.workflow_fixtures import inquiry_workflow, node, ref, text

AGENT = str(uuid.uuid4())


def _codes(raw: dict[str, Any]) -> list[str]:
    return sorted(issue.code for issue in graph_issues(WorkflowDefinition.model_validate(raw)))


def test_the_inquiry_workflow_is_clean() -> None:
    definition = WorkflowDefinition.model_validate(inquiry_workflow(AGENT))
    assert graph_issues(definition) == []
    # The stored form round-trips (``from`` keys, segment lists).
    assert WorkflowDefinition.model_validate(definition.dump()) == definition
    assert definition.dump()["edges"][0] == {"from": "start", "to": "classify", "when": None}


def test_a_new_draft_parses_and_lists_what_is_missing() -> None:
    assert _codes(empty_definition()) == ["needs_one_next", "no_end"]


def _drop_edge(src: str, when: str | None = None) -> Callable[[dict[str, Any]], None]:
    def change(raw: dict[str, Any]) -> None:
        raw["edges"] = [e for e in raw["edges"] if not (e["from"] == src and e.get("when") == when)]

    return change


def _set(node_id: str, **values: Any) -> Callable[[dict[str, Any]], None]:
    return lambda raw: node(raw, node_id).update(values)


def _many_starts(raw: dict[str, Any]) -> None:
    raw["nodes"].append({"id": "start2", "type": "start"})
    raw["edges"].append({"from": "start2", "to": "classify"})


def _cycle(raw: dict[str, Any]) -> None:
    raw["edges"].append({"from": "draft", "to": "classify"})


def _orphan(raw: dict[str, Any]) -> None:
    raw["nodes"].append({"id": "orphan", "type": "fail"})


def _sibling_ref(raw: dict[str, Any]) -> None:
    # ``fail`` is on no path to ``done``.
    node(raw, "done")["fields"]["why"] = [ref("fail")]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda raw: raw["nodes"].pop(0), ["edge_unknown_node", "no_start"]),
        (_many_starts, ["many_starts", "many_starts"]),
        (
            lambda raw: raw["nodes"].__delitem__(slice(5, 7)),
            ["branch_missing", "edge_unknown_node", "edge_unknown_node"]
            + ["needs_one_next", "no_end"],
        ),
        # A step that leads back is a loop only from a decision exit (workflow-loop-plan 3.2).
        (_cycle, ["loop_from_not_decision", "needs_one_next"]),
        (_orphan, ["unreachable"]),
        (_drop_edge("is_urgent", "false"), ["branch_missing"]),
        (_drop_edge("draft"), ["needs_one_next", "unreachable"]),
        (lambda raw: raw["edges"].append({"from": "done", "to": "fail"}), ["end_has_outgoing"]),
        (
            lambda raw: raw["edges"].append({"from": "draft", "to": "start"}),
            ["loop_from_not_decision", "needs_one_next", "start_has_incoming"],
        ),
        (_set("review", timeout_minutes=None), ["approval_no_timeout"]),
        (_set("review", title=[]), ["empty_title"]),
        (_set("classify", prompt=[]), ["empty_prompt"]),
        (_set("done", fields={}), ["empty_output"]),
        (_set("classify", prompt=[ref("input", "/missing")]), ["ref_unknown_input"]),
        (_set("draft", template=[ref("ghost", "/x")]), ["ref_unknown_node"]),
        (_set("classify", prompt=[ref("draft", "/text")]), ["ref_not_upstream"]),
        (_set("draft", template=[ref("classify", "/nope")]), ["ref_bad_path"]),
        (_set("draft", template=[ref("is_urgent", "/value")]), []),
        # ``review`` runs only on the urgent path; ``draft`` also runs without it.
        (_set("draft", template=[ref("review", "/decision")]), ["ref_not_upstream"]),
        (_set("done", fields={"x": [ref("review", "/decision")]}), ["ref_not_upstream"]),
        (_set("fail", reason="x"), []),
        (_sibling_ref, ["ref_not_upstream"]),
    ],
)
def test_each_check(change: Callable[[dict[str, Any]], Any], expected: list[str]) -> None:
    raw = inquiry_workflow(AGENT)
    change(raw)
    assert _codes(raw) == sorted(expected)


def test_decision_conditions_check_their_references() -> None:
    raw = inquiry_workflow(AGENT)
    node(raw, "is_urgent")["condition"] = {
        "any": [{"source": "classify", "path": "/missing", "op": "exists"}]
    }
    assert _codes(raw) == ["ref_bad_path"]


@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw["nodes"].append({"id": "input", "type": "fail"}),  # reserved id
        lambda raw: raw["nodes"].append({"id": "start", "type": "fail"}),  # duplicate id
        lambda raw: raw["nodes"].append({"id": "x", "type": "loop"}),  # unknown kind
        lambda raw: node(raw, "classify").update(output_schema={"type": "string"}),
        lambda raw: node(raw, "classify").update(extra=1),
        lambda raw: node(raw, "classify").update(agent_id="not-a-uuid"),
        lambda raw: node(raw, "review").update(timeout_minutes=0),
        lambda raw: node(raw, "is_urgent").update(condition={"all": [], "any": []}),
        lambda raw: node(raw, "is_urgent").update(
            condition={"all": [{"source": "classify", "path": "/urgent", "op": "gt", "value": "x"}]}
        ),
        lambda raw: node(raw, "is_urgent").update(
            condition={"all": [{"source": "classify", "op": "exists", "value": 1}]}
        ),
        lambda raw: node(raw, "draft").update(template=[{"text": "x", "ref": {"node": "a"}}]),
        lambda raw: node(raw, "draft").update(template="{{classify.category}}"),
        lambda raw: raw["edges"].append({"from": "start", "to": "classify"}),  # duplicate edge
        lambda raw: raw["edges"].append({"from": "a", "to": "b", "when": "may be"}),
        lambda raw: raw["inputs"].append({"name": "n", "type": "integer", "max_length": 3}),
        lambda raw: raw.update(schema_version="moldy.workflow.v2"),
        lambda raw: raw["nodes"].extend({"id": f"n{i}", "type": "fail"} for i in range(MAX_NODES)),
    ],
)
def test_the_shape_rejects(change: Callable[[dict[str, Any]], Any]) -> None:
    raw = inquiry_workflow(AGENT)
    change(raw)
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(raw)


def test_templates_are_segments_not_expressions() -> None:
    raw = inquiry_workflow(AGENT)
    node(raw, "draft")["template"] = [text("{{classify.category}} stays literal")]
    assert _codes(raw) == []
