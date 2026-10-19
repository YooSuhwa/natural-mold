"""Collapse tuples sharing a (user, relation, object) key; OpenFGA stores one per key.

Merge rules (deterministic, most permissive of what the rows grant):
* any unconditioned tuple wins over conditioned ones;
* ``non_expired_grant``: the latest expiry wins (ties: smallest context encoding);
* ``allowed_run_source``: the union of ``allowed_sources``;
* identical conditions collapse; any other mix raises ``TupleRuleError``.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta

from pydantic import JsonValue

from app.authz.tuple_types import (
    EXPIRY_CONDITION,
    RUN_SOURCE_CONDITION,
    RUN_SOURCES,
    RelationTuple,
    TupleRuleError,
)


def _expiry_end(item: RelationTuple) -> datetime:
    context = item.condition.context if item.condition else {}
    start = datetime.fromisoformat(str(context["grant_time"]))
    duration = str(context["grant_duration"])
    if duration.endswith("us"):
        return start + timedelta(microseconds=int(duration[:-2]))
    return start + timedelta(seconds=int(duration[:-1]))


def _sources(item: RelationTuple) -> set[str]:
    raw = item.condition.context.get("allowed_sources") if item.condition else None
    return {str(source) for source in raw} if isinstance(raw, list) else set()


def _merge_key(items: list[RelationTuple]) -> RelationTuple:
    distinct = sorted(set(items), key=lambda item: item.sort_key)
    if len(distinct) == 1:
        return distinct[0]
    first = distinct[0]
    if any(item.condition is None for item in distinct):
        return RelationTuple(first.user, first.relation, first.object)
    names = {item.condition.name for item in distinct if item.condition}
    if names == {EXPIRY_CONDITION}:
        latest = max(_expiry_end(item) for item in distinct)
        return next(item for item in distinct if _expiry_end(item) == latest)
    if names == {RUN_SOURCE_CONDITION}:
        union = set().union(*(_sources(item) for item in distinct))
        ordered = [s for s in RUN_SOURCES if s in union] + sorted(union - set(RUN_SOURCES))
        context: dict[str, JsonValue] = {"allowed_sources": list(ordered)}
        return RelationTuple.of(
            first.user, first.relation, first.object, RUN_SOURCE_CONDITION, context
        )
    raise TupleRuleError(
        f"incompatible conditions {sorted(names)} for {first.user} {first.relation} {first.object}"
    )


def merge_tuples(items: Iterable[RelationTuple]) -> tuple[RelationTuple, ...]:
    grouped: defaultdict[tuple[str, str, str], list[RelationTuple]] = defaultdict(list)
    for item in items:
        grouped[(item.user, item.relation, item.object)].append(item)
    merged = (_merge_key(group) for group in grouped.values())
    return tuple(sorted(merged, key=lambda item: item.sort_key))
