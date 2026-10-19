"""Registered structural-stage authority behavior."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.task10_source_contract_rules import JsonObject, JsonValue, compare_documents
from scripts.task10_stage_authority import StageAuthorityError, compare_stage
from scripts.task10_structural_contract import StageAuthorityError as StructuralAuthorityError
from scripts.task10_structural_contract import parse_structural_contract, verify_structural_delta
from tests.architecture.task10_authority_scenarios import (
    GUARDED_FIELDS,
    STRUCTURAL_KEYS,
    build_structural_case,
    registered_capacity_chain,
)


def _object(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


def _document(path: Path) -> JsonObject:
    return _object(json.loads(path.read_text(encoding="utf-8")))


def _delta_inputs(tmp_path: Path) -> tuple[JsonObject, JsonObject, JsonObject]:
    case = build_structural_case(tmp_path / "root")
    baseline = _object(_document(case.baseline)["sources"])
    candidate = _object(_document(case.candidate)["sources"])
    return baseline, candidate, _document(case.contract)


def test_registered_exact_capacity_move_block_facade_guarded_identity_roundtrip(
    tmp_path: Path,
) -> None:
    case = build_structural_case(tmp_path / "root", lane="I27")

    result = compare_stage(case.authority.root, case.authority.seal, case.baseline, case.candidate)

    assert result == {
        "status": "verified",
        "candidate": _document(case.contract)["candidate"],
        "task": "I27",
        "authority": {
            "path": str(case.contract.resolve()),
            "sha256": hashlib.sha256(case.contract.read_bytes()).hexdigest(),
            "bytes": case.contract.stat().st_size,
        },
    }


def test_registered_capacity_is_mandatory_and_error_identity_is_reexported(tmp_path: Path) -> None:
    capacity = registered_capacity_chain(tmp_path / "root")

    assert _document(capacity.record)["stage"] == "I1_AUTHORITY_CAPACITY_AMENDED"
    assert StageAuthorityError is StructuralAuthorityError


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"extra": True}, "structural_contract_schema"),
        ({"schema_version": 1.0}, "structural_contract_schema"),
        ({"lane_id": "*"}, "structural_lane_invalid"),
        ({"candidate": "6" * 40}, "structural_candidate_head_mismatch"),
        ({"approved_paths": ["backend/old.py"]}, "stage_paths_mismatch"),
        (
            {"facades": {"backend/old.py::0:FunctionDef:alpha": "a" * 64}},
            "stage_identity_reused",
        ),
    ],
)
def test_registered_contract_rejects_schema_lane_candidate_and_identity_drift(
    tmp_path: Path,
    override: JsonObject,
    error: str,
) -> None:
    case = build_structural_case(tmp_path / "root", contract_overrides=override)

    with pytest.raises(StageAuthorityError, match=f"^{error}$"):
        compare_stage(case.authority.root, case.authority.seal, case.baseline, case.candidate)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("nested_schema", "stage_guarded_changed"),
        ("guarded_type", "stage_guarded_changed"),
        ("reused_identity", "stage_identity_reused"),
        ("block_length", "stage_block_invalid"),
        ("facade_order", "stage_facades_invalid"),
        ("facade_digest", "stage_facade_changed"),
        ("declaration", "stage_node_declaration_changed"),
        ("order", "stage_block_order_changed"),
    ],
)
def test_decoded_structural_delta_rejects_malicious_variants(
    tmp_path: Path,
    mutation: str,
    error: str,
) -> None:
    before, after, contract = _delta_inputs(tmp_path)
    changed = deepcopy(contract)
    expected = _object(changed["expected_guarded"])
    new_guard = _object(expected["backend/new.py"])
    moves = changed["moves"]
    blocks = changed["ordered_blocks"]
    if mutation == "nested_schema":
        new_guard["extra"] = []
    elif mutation == "guarded_type":
        new_guard["imports"] = [True]
    elif mutation == "reused_identity":
        assert isinstance(moves, list)
        changed["moves"] = [*moves, deepcopy(moves[0])]
    elif mutation == "block_length":
        assert isinstance(blocks, list)
        _object(blocks[0])["to"] = []
    elif mutation == "facade_order":
        facades = _object(changed["facades"])
        changed["facades"] = {key: facades[key] for key in reversed(facades)}
    elif mutation == "facade_digest":
        facades = _object(changed["facades"])
        facades[next(iter(facades))] = "9" * 64
    elif mutation == "declaration":
        _object(_object(after["backend/new.py"])["nodes"])["0:FunctionDef:alpha"] = "9" * 64
    else:
        assert mutation == "order"
        rows = _object(after["backend/new.py"])["statements"]
        assert isinstance(rows, list)
        _object(rows[0])["sha256"] = "9" * 64

    with pytest.raises(StageAuthorityError, match=f"^{error}$"):
        verify_structural_delta(before, after, changed)


def test_decoded_ordered_block_preserves_distinct_statement_positions(tmp_path: Path) -> None:
    before, after, contract = _delta_inputs(tmp_path)
    old_rows = _object(before["backend/old.py"])["statements"]
    new_rows = _object(after["backend/new.py"])["statements"]
    blocks = contract["ordered_blocks"]
    assert isinstance(old_rows, list) and isinstance(new_rows, list) and isinstance(blocks, list)
    second = {"identity": "module/1:Expr:", "sha256": "8" * 64}
    old_rows.append(second)
    new_rows.append(deepcopy(second))
    block = _object(blocks[0])
    block_from, block_to = block["from"], block["to"]
    assert isinstance(block_from, list) and isinstance(block_to, list)
    block_from.append("backend/old.py::module/1:Expr:")
    block_to.append("backend/new.py::module/1:Expr:")

    verify_structural_delta(before, after, contract)
    new_rows.reverse()

    with pytest.raises(StageAuthorityError, match="^stage_block_order_changed$"):
        verify_structural_delta(before, after, contract)


def test_decoded_delta_preserves_unrelated_stable_statement_order(tmp_path: Path) -> None:
    before, after, contract = _delta_inputs(tmp_path)
    old_source = _object(before["backend/old.py"])
    stable_source = _object(after["backend/old.py"])
    old_rows, stable_rows = old_source["statements"], stable_source["statements"]
    old_nodes, stable_nodes = old_source["nodes"], stable_source["nodes"]
    assert isinstance(old_rows, list) and isinstance(stable_rows, list)
    assert isinstance(old_nodes, dict) and isinstance(stable_nodes, dict)
    keep = [
        {"identity": "module/2:Expr:keep_a", "sha256": "6" * 64},
        {"identity": "module/3:Expr:keep_b", "sha256": "7" * 64},
    ]
    old_rows.extend(deepcopy(keep))
    stable_rows.extend(deepcopy(keep))
    old_nodes.update({"2:Expr:keep_a": "6" * 64, "3:Expr:keep_b": "7" * 64})
    stable_nodes.update({"2:Expr:keep_a": "6" * 64, "3:Expr:keep_b": "7" * 64})

    verify_structural_delta(before, after, contract)
    stable_rows[1:] = reversed(stable_rows[1:])

    with pytest.raises(StageAuthorityError, match="^stage_block_order_changed$"):
        verify_structural_delta(before, after, contract)


@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize("location", ["moved", "stable"])
@pytest.mark.parametrize("same_digest", [True, False])
def test_decoded_delta_rejects_repeated_statement_identity_before_flattening(
    tmp_path: Path,
    side: str,
    location: str,
    same_digest: bool,
) -> None:
    before, after, contract = _delta_inputs(tmp_path)
    if location == "stable":
        stable = {"identity": "module/9:Expr:stable", "sha256": "7" * 64}
        for sources in (before, after):
            rows = _object(sources["backend/old.py"])["statements"]
            assert isinstance(rows, list)
            rows.append(deepcopy(stable))
        relative = "backend/old.py"
    else:
        relative = "backend/old.py" if side == "before" else "backend/new.py"
    selected = before if side == "before" else after
    rows = _object(selected[relative])["statements"]
    assert isinstance(rows, list)
    duplicate = deepcopy(rows[-1])
    if not same_digest:
        _object(duplicate)["sha256"] = "9" * 64
    rows.append(duplicate)

    with pytest.raises(StageAuthorityError, match="^stage_identity_reused$"):
        verify_structural_delta(before, after, contract)


def test_parse_structural_contract_exact_schema_and_alias_types(tmp_path: Path) -> None:
    _before, _after, contract = _delta_inputs(tmp_path)

    parsed, lane, candidate = parse_structural_contract(contract)

    assert set(parsed) == set(STRUCTURAL_KEYS)
    assert lane == "I2"
    assert candidate == contract["candidate"]


def test_registered_wrong_tuple_rejects_before_comparison(tmp_path: Path) -> None:
    case = build_structural_case(tmp_path / "root")
    wrong = tmp_path / "wrong.json"
    wrong.write_text("{}\n", encoding="utf-8")

    with pytest.raises(StageAuthorityError, match="^stage_baseline_path_mismatch$"):
        compare_stage(case.authority.root, case.authority.seal, wrong, case.candidate)


def test_registered_dirty_live_git_rejects_before_comparison(tmp_path: Path) -> None:
    case = build_structural_case(tmp_path / "root")
    (case.authority.root / "dirty.txt").write_text("drift\n", encoding="utf-8")

    with pytest.raises(ValueError, match="^live_git_state_drift$"):
        compare_stage(case.authority.root, case.authority.seal, case.baseline, case.candidate)


def test_direct_comparison_has_no_allow_authority_and_rejects_reorder() -> None:
    source = {
        "sha256": "1" * 64,
        "module_ast_sha256": "2" * 64,
        "nodes": {"alpha": "3" * 64},
        "statements": [{"identity": "one", "sha256": "4" * 64}],
        **dict.fromkeys(GUARDED_FIELDS, []),
    }
    baseline: JsonObject = {"candidate": "5" * 40, "sources": {"backend/old.py": source}}
    changed = deepcopy(source)
    changed["statements"] = [{"identity": "two", "sha256": "4" * 64}]
    candidate: JsonObject = {"candidate": "5" * 40, "sources": {"backend/old.py": changed}}

    assert compare_documents.__code__.co_argcount == 2
    with pytest.raises(ValueError, match="^statement_order_changed:backend/old.py$"):
        compare_documents(baseline, candidate)
