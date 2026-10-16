"""Shared ids and a model.json validator for the C-02 tuple rule tables."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import JsonValue

from app.authz.tuples import RelationTuple

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
GRANTED = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)

# Short keys used in tuple specs ("user:U member tenant:T"); values are fixed UUIDs.
ID = {
    key: UUID(int=value)
    for key, value in {
        "T": 0x10, "T2": 0x11, "O": 0x20, "O2": 0x21, "U": 0x30, "U2": 0x31, "G": 0x40,
        "A": 0x50, "A2": 0x51, "R": 0x60, "S": 0x70, "M": 0x80, "GRANT": 0x90,
    }.items()
}  # fmt: skip

# Runtime parameters injected per query by AuthzService (plan 7.3), never stored on a tuple.
RUNTIME_PARAMETERS = frozenset({"current_time", "run_source", "user_ip"})

_DOCUMENT = json.loads((Path(__file__).parents[2] / "app" / "authz" / "model.json").read_text())
_TYPES = {entry["type"]: entry for entry in _DOCUMENT["type_definitions"]}


def ref(kind: str, value: UUID, relation: str = "") -> str:
    return f"{kind}:{value}" + (f"#{relation}" if relation else "")


def _resolve(spec: str) -> str:
    kind, _, rest = spec.partition(":")
    key, _, relation = rest.partition("#")
    return ref(kind, ID[key], relation)


def spec(text: str, condition: str | None = None, **context: JsonValue) -> RelationTuple:
    """``"user:U member tenant:T"`` -> the tuple with the fixed ids substituted."""
    user, relation, obj = text.split()
    return tup(_resolve(user), relation, _resolve(obj), condition, **context)


def specs(*texts: str) -> set[RelationTuple]:
    return {spec(text) for text in texts}


def tup(
    user: str, relation: str, obj: str, condition: str | None = None, **context: JsonValue
) -> RelationTuple:
    return RelationTuple.of(user, relation, obj, condition, context if condition else None)


def model_violation(item: RelationTuple) -> str | None:
    """Mirror OpenFGA's write validation: type restriction, condition name and parameters."""
    object_type = item.object.split(":", 1)[0]
    relation = _TYPES[object_type].get("metadata", {}).get("relations", {}).get(item.relation)
    if relation is None:
        return f"{object_type} has no relation {item.relation}"
    subject, _, subject_relation = item.user.partition("#")
    subject_type = subject.split(":", 1)[0]
    condition = item.condition.name if item.condition else ""
    allowed = relation.get("directly_related_user_types", [])
    if not any(
        entry["type"] == subject_type
        and entry.get("relation", "") == subject_relation
        and entry.get("condition", "") == condition
        for entry in allowed
    ):
        return f"{item.user} not assignable to {object_type}#{item.relation} [{condition}]"
    if item.condition is not None:
        parameters = set(_DOCUMENT["conditions"][condition]["parameters"])
        stored = set(item.condition.context)
        if stored - parameters or parameters - stored - RUNTIME_PARAMETERS:
            return f"{condition} context {sorted(stored)} does not match {sorted(parameters)}"
    return None
