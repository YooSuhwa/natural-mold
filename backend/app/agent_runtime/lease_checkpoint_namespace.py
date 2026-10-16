"""Checkpoint namespace authority for a prepared run lease (parent Task 10).

The root namespace is ``""``. A nested namespace is accepted only when its
parent namespace is already authorized for this lease and its last segment is
``<node>:<task_id>`` with ``node`` in the precompiled child catalog. Callers
can never authorize an arbitrary prefix or another lease's namespace.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

NS_SEP = "|"
NS_END = ":"
LINEAGE_DENIED = "checkpoint_lineage_denied"
_TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class CheckpointLineageDeniedError(PermissionError):
    code = LINEAGE_DENIED

    def __init__(self, reason: str) -> None:
        super().__init__(f"{LINEAGE_DENIED}:{reason}")
        self.reason = reason


class LeaseNamespaceAuthority:
    def __init__(self, child_node_names: Iterable[str]) -> None:
        self._child_node_names = frozenset(child_node_names)
        self._authorized: set[str] = {""}

    def authorize(self, namespace: str) -> None:
        if namespace in self._authorized:
            return
        parent, _sep, segment = namespace.rpartition(NS_SEP)
        node, end, task_id = segment.partition(NS_END)
        if (
            parent not in self._authorized
            or not end
            or node not in self._child_node_names
            or _TASK_ID.fullmatch(task_id) is None
        ):
            raise CheckpointLineageDeniedError("namespace")
        self._authorized.add(namespace)
