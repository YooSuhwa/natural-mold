"""Pure decoded structural-contract validation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

type JsonValue = str | int | bool | None | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]

_CONTRACT_KEYS = frozenset(
    {
        "schema_version",
        "manifest_kind",
        "lane_id",
        "candidate",
        "baseline",
        "candidate_contract",
        "approved_paths",
        "moves",
        "ordered_blocks",
        "facades",
        "expected_guarded",
    }
)
_GUARDED = (
    "imports",
    "exports",
    "routes",
    "locks",
    "lock_edges",
    "with_for_update",
    "hydration_aliases",
)


class StageAuthorityError(ValueError):
    """Report a closed structural authority contract violation."""


def parse_structural_contract(value: JsonValue) -> tuple[JsonObject, str, str]:
    """Parse the decoded structural contract."""
    contract = _record(value, "structural_contract_schema")
    if set(contract) != _CONTRACT_KEYS:
        raise StageAuthorityError("structural_contract_schema")
    version, kind = contract.get("schema_version"), contract.get("manifest_kind")
    lane, candidate = contract.get("lane_id"), contract.get("candidate")
    if type(version) is not int or version != 1 or kind != "structural_contract":
        raise StageAuthorityError("structural_contract_schema")
    if not isinstance(lane, str) or re.fullmatch(r"I[1-9][0-9]*", lane) is None:
        raise StageAuthorityError("structural_lane_invalid")
    if not isinstance(candidate, str) or re.fullmatch(r"[0-9a-f]{40}", candidate) is None:
        raise StageAuthorityError("structural_candidate_invalid")
    return contract, lane, candidate


def verify_structural_delta(
    before: JsonObject,
    after: JsonObject,
    contract: JsonObject,
) -> None:
    """Verify a decoded source delta against its structural contract."""
    approved = _strings(contract.get("approved_paths"), "stage_paths_invalid", nonempty=True)
    if approved != tuple(sorted(approved)):
        raise StageAuthorityError("stage_paths_invalid")
    for relative in approved:
        _canonical_relative(relative)
    used: set[str] = set()
    moves = _pairs(contract.get("moves"), used)
    ordered, block_orders = _blocks(contract.get("ordered_blocks"), used)
    facades = _record(contract.get("facades"), "stage_facades_invalid")
    if tuple(facades) != tuple(sorted(facades)):
        raise StageAuthorityError("stage_facades_invalid")
    for identity, digest in facades.items():
        if not isinstance(identity, str) or not isinstance(digest, str):
            raise StageAuthorityError("stage_facades_invalid")
        _identity(identity, "stage_facades_invalid")
        if identity in used or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise StageAuthorityError("stage_identity_reused")
        used.add(identity)
    if not moves and not ordered and not facades:
        raise StageAuthorityError("stage_delta_required")
    guarded = _record(contract.get("expected_guarded"), "stage_guarded_invalid")
    changed = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
    if (
        not changed
        or changed != set(approved)
        or changed != set(guarded)
        or tuple(guarded) != approved
    ):
        raise StageAuthorityError("stage_paths_mismatch")
    for relative in changed:
        _verify_guarded(
            _record(after.get(relative), "stage_source_invalid"),
            _record(guarded.get(relative), "stage_guarded_invalid"),
        )
    old_nodes = _flatten(before, "nodes")
    new_nodes = _flatten(after, "nodes")
    unchanged = {key for key in set(old_nodes) & set(new_nodes) if old_nodes[key] == new_nodes[key]}
    node_facades = {key: value for key, value in facades.items() if key in new_nodes}
    if set(old_nodes) - unchanged != set(moves):
        raise StageAuthorityError("stage_move_set_mismatch")
    if set(new_nodes) - unchanged != set(moves.values()) | set(node_facades):
        raise StageAuthorityError("stage_move_set_mismatch")
    if any(old_nodes[source] != new_nodes[target] for source, target in moves.items()):
        raise StageAuthorityError("stage_node_declaration_changed")
    old_rows = _flatten(before, "statements")
    new_rows = _flatten(after, "statements")
    row_facades = {key: value for key, value in facades.items() if key in new_rows}
    same = {key for key in set(old_rows) & set(new_rows) if old_rows[key] == new_rows[key]}
    old_stable = tuple(key for key in old_rows if key in same)
    new_stable = tuple(key for key in new_rows if key in same)
    if old_stable != new_stable:
        raise StageAuthorityError("stage_block_order_changed")
    if set(old_rows) - same != set(ordered):
        raise StageAuthorityError("stage_statement_set_mismatch")
    if set(new_rows) - same != set(ordered.values()) | set(row_facades):
        raise StageAuthorityError("stage_statement_set_mismatch")
    if any(old_rows[left] != new_rows[right] for left, right in ordered.items()):
        raise StageAuthorityError("stage_block_order_changed")
    for left, right in block_orders:
        if tuple(key for key in old_rows if key in left) != left:
            raise StageAuthorityError("stage_block_order_changed")
        if tuple(key for key in new_rows if key in right) != right:
            raise StageAuthorityError("stage_block_order_changed")
    if set(node_facades) | set(row_facades) != set(facades):
        raise StageAuthorityError("stage_facade_changed")
    if any(new_nodes.get(key, new_rows.get(key)) != digest for key, digest in facades.items()):
        raise StageAuthorityError("stage_facade_changed")


def _record(value: JsonValue, code: str) -> JsonObject:
    if not isinstance(value, dict):
        raise StageAuthorityError(code)
    return value


def _strings(value: JsonValue, code: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise StageAuthorityError(code)
    result = tuple(item for item in value if isinstance(item, str))
    if (nonempty and not result) or len(set(result)) != len(result):
        raise StageAuthorityError(code)
    return result


def _canonical_relative(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or str(path) != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise StageAuthorityError("path_not_canonical")


def _identity(value: str, code: str) -> None:
    relative, separator, identity = value.partition("::")
    if separator != "::" or not identity or "::" in identity:
        raise StageAuthorityError(code)
    _canonical_relative(relative)


def _pairs(value: JsonValue, used: set[str]) -> dict[str, str]:
    if not isinstance(value, list):
        raise StageAuthorityError("stage_moves_required")
    result: dict[str, str] = {}
    for row in value:
        item = _record(row, "stage_move_invalid")
        before, after = item.get("from"), item.get("to")
        if set(item) != {"from", "to"} or not isinstance(before, str) or not isinstance(after, str):
            raise StageAuthorityError("stage_move_invalid")
        _identity(before, "stage_move_invalid")
        _identity(after, "stage_move_invalid")
        if before in used or after in used or before == after:
            raise StageAuthorityError("stage_identity_reused")
        used.update((before, after))
        result[before] = after
    return result


def _blocks(
    value: JsonValue,
    used: set[str],
) -> tuple[dict[str, str], tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]]:
    if not isinstance(value, list):
        raise StageAuthorityError("stage_blocks_required")
    result: dict[str, str] = {}
    orders: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for row in value:
        item = _record(row, "stage_block_invalid")
        if set(item) != {"from", "to"}:
            raise StageAuthorityError("stage_block_invalid")
        before = _strings(item.get("from"), "stage_block_invalid", nonempty=True)
        after = _strings(item.get("to"), "stage_block_invalid", nonempty=True)
        identities = (*before, *after)
        if len(before) != len(after) or len(set(identities)) != len(identities):
            raise StageAuthorityError("stage_block_invalid")
        if any(identity in used for identity in identities):
            raise StageAuthorityError("stage_block_invalid")
        for identity in identities:
            _identity(identity, "stage_block_invalid")
        used.update(identities)
        result.update(zip(before, after, strict=True))
        orders.append((before, after))
    return result, tuple(orders)


def _verify_guarded(source: JsonObject, expected: JsonObject) -> None:
    if set(expected) != set(_GUARDED):
        raise StageAuthorityError("stage_guarded_changed")
    for key in _GUARDED:
        actual, planned = source.get(key), expected.get(key)
        if not isinstance(actual, list) or not isinstance(planned, list):
            raise StageAuthorityError("stage_guarded_changed")
        if any(not isinstance(item, str) for item in actual + planned) or actual != planned:
            raise StageAuthorityError("stage_guarded_changed")


def _flatten(sources: JsonObject, field: str) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for relative, source in sources.items():
        item = _record(source, "stage_source_invalid")
        if field == "nodes":
            for identity, digest in _record(item.get(field), "stage_nodes_required").items():
                key = f"{relative}::{identity}"
                if key in result:
                    raise StageAuthorityError("stage_identity_reused")
                result[key] = digest
            continue
        rows = item.get(field)
        if not isinstance(rows, list):
            raise StageAuthorityError("stage_statements_required")
        for row in rows:
            statement = _record(row, "stage_statement_invalid")
            identity = statement.get("identity")
            if not isinstance(identity, str) or set(statement) != {"identity", "sha256"}:
                raise StageAuthorityError("stage_statement_invalid")
            key = f"{relative}::{identity}"
            if key in result:
                raise StageAuthorityError("stage_identity_reused")
            result[key] = statement.get("sha256")
    return result
