"""Pure diff of expected vs stored tuples, partition claiming and hold rules (C-07, 8.6).

A tuple key is ``(user, relation, object)``; OpenFGA stores one tuple per key, so a key that
exists on both sides with a different condition is ``changed`` (rewritten as delete + write).

Partitions: a company (or organization) claims the stored tuples whose key it expects, or
whose object, or user's object part (``group:g#member`` -> ``group:g``), is one of its own
refs. In a store-wide run the platform partition comes last and claims every stored tuple no
company claimed, so tuples of rows deleted from Postgres are found only store-wide.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.authz import tuple_rows as rows
from app.authz.outbox import tuple_key
from app.authz.revocations import ORG_WIDE_SCOPES
from app.authz.tuple_types import RelationTuple

_ACTOR_PREFIXES = ("user:", "agent:")
Key = tuple[str, str, str]
Tuples = Mapping[Key, RelationTuple]


@dataclass(frozen=True, slots=True)
class TupleDiff:
    missing: tuple[RelationTuple, ...] = ()  # expected, not stored
    extra: tuple[RelationTuple, ...] = ()  # stored, not expected (stored form)
    changed: tuple[RelationTuple, ...] = ()  # stored with another condition (expected form)
    held: tuple[RelationTuple, ...] = ()  # left alone (in flight, marker, or no longer true)

    def __bool__(self) -> bool:
        return bool(self.missing or self.extra or self.changed)


def key_of(item: RelationTuple) -> Key:
    return (item.user, item.relation, item.object)


def _sorted(items: Iterable[RelationTuple]) -> tuple[RelationTuple, ...]:
    return tuple(sorted(items, key=lambda t: t.sort_key))


def diff_tuples(want: Tuples, have: Tuples) -> TupleDiff:
    missing = (t for k, t in want.items() if k not in have)
    changed = (t for k, t in want.items() if k in have and have[k] != t)
    extra = (t for k, t in have.items() if k not in want)
    return TupleDiff(_sorted(missing), _sorted(extra), _sorted(changed))


def _refs(kind: str, ids: Iterable[UUID]) -> set[str]:
    return {f"{kind}:{value}" for value in ids}


def scope_refs(snapshot: rows.Snapshot, *, tenant_id: UUID, org_id: UUID | None) -> frozenset[str]:
    """The object refs a company (or one of its organizations) owns in ``snapshot``."""

    def own(org: UUID | None) -> bool:
        return org_id is None or org == org_id

    found = set() if org_id is not None else {f"tenant:{tenant_id}"}
    found |= _refs("organization", (o.id for o in snapshot.organizations if own(o.id)))
    found |= _refs("group", (g.id for g in snapshot.groups if own(g.org_id)))
    found |= {f"{r.resource_type}:{r.id}" for r in snapshot.resources if own(r.org_id)}
    found |= _refs("credential", (c.id for c in snapshot.credentials if own(c.org_id)))
    found |= _refs("marketplace_item", (m.id for m in snapshot.marketplace_items if own(m.org_id)))
    tools = {t.id for t in snapshot.mcp_tools if f"mcp_server:{t.server_id}" in found}
    return frozenset(found | _refs("mcp_tool", tools))


def in_scope(item: RelationTuple, refs: frozenset[str]) -> bool:
    return item.object in refs or item.user.split("#", 1)[0] in refs


class StoredIndex:
    """Stored tuples, claimed partition by partition (each tuple by at most one)."""

    def __init__(self, stored: dict[Key, RelationTuple]) -> None:
        self.stored = stored
        self._by_ref: defaultdict[str, list[Key]] = defaultdict(list)
        for key in stored:
            self._by_ref[key[2]].append(key)
            self._by_ref[key[0].split("#", 1)[0]].append(key)

    def claim(
        self, refs: frozenset[str] | None, expected: Iterable[Key]
    ) -> dict[Key, RelationTuple]:
        """Pop the partition's tuples; ``refs=None`` claims everything left."""
        if refs is None:
            claimed, self.stored = self.stored, {}
            return claimed
        keys = {k for ref in refs for k in self._by_ref.get(ref, ())}
        keys.update(expected)
        return {k: self.stored.pop(k) for k in keys if k in self.stored}


def marker_blocks(item: RelationTuple, markers: Sequence[tuple[str, str, str]]) -> bool:
    """A pending (or recently finalized) revocation marker forbids writing ``item`` (D12).

    Mirrors the deny overlay, conservatively: an organization-wide marker on the tuple's
    subject blocks every write for that subject; a marker naming the object blocks it for
    the marked subject, and for every subject when the marker is not actor-specific.
    """
    for scope, subject, obj in markers:
        if subject == item.user and (obj == item.object or scope in ORG_WIDE_SCOPES):
            return True
        if obj == item.object and not subject.startswith(_ACTOR_PREFIXES):
            return True
    return False


def hold(
    diff: TupleDiff, outbox_keys: frozenset[str], markers: Sequence[tuple[str, str, str]]
) -> TupleDiff:
    """Move recently active keys (any op) and marker-blocked writes to ``held``."""
    held = list(diff.held)

    def keep(items: tuple[RelationTuple, ...], *, writes: bool) -> tuple[RelationTuple, ...]:
        kept = []
        for item in items:
            if tuple_key(item) in outbox_keys or (writes and marker_blocks(item, markers)):
                held.append(item)
            else:
                kept.append(item)
        return tuple(kept)

    return TupleDiff(
        missing=keep(diff.missing, writes=True),
        extra=keep(diff.extra, writes=False),
        changed=keep(diff.changed, writes=True),
        held=_sorted(held),
    )


def recheck(diff: TupleDiff, fresh: Tuples) -> TupleDiff:
    """Against a re-read projection: drop writes no longer expected, deletes now expected."""
    held = list(diff.held)

    def keep(items: tuple[RelationTuple, ...], ok) -> tuple[RelationTuple, ...]:
        kept = tuple(t for t in items if ok(t))
        held.extend(t for t in items if not ok(t))
        return kept

    return TupleDiff(
        missing=keep(diff.missing, lambda t: fresh.get(key_of(t)) == t),
        extra=keep(diff.extra, lambda t: key_of(t) not in fresh),
        changed=keep(diff.changed, lambda t: fresh.get(key_of(t)) == t),
        held=_sorted(held),
    )
