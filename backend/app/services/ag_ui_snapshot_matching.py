"""Match selected checkpoint rows and public frames within one run's tool issuance scope."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.services.ag_ui_snapshot_call_visibility import canonical_checkpoint_calls

type Row = dict[str, Any]


class ScopedCheckpointCalls:
    def __init__(
        self,
        rows: Sequence[tuple[int, Row, AgUiSnapshotMessageLink]],
        *,
        public_id: str,
        visibility: ChildVisibility | None,
        issuances: Mapping[tuple[str | None, str], str],
    ) -> None:
        projected = canonical_checkpoint_calls(
            [(row, link) for _, row, link in rows],
            public_run_id=public_id,
            visibility=visibility,
            root_issuances=issuances,
        )
        self.rows: dict[int, Row | None] = {}
        self.by_id: dict[str, list[int]] = defaultdict(list)
        self.by_call: dict[tuple[str, str], set[int]] = defaultdict(set)
        for index, raw, _ in rows:
            safe = projected.get(raw["id"])
            self.rows[index] = safe
            if safe is None:
                continue
            self.by_id[safe["id"]].append(index)
            if safe["role"] == "assistant":
                for call in safe.get("toolCalls", []):
                    self.by_call["assistant", call["id"]].add(index)
            elif safe["role"] == "tool":
                self.by_call["tool", safe["toolCallId"]].add(index)
