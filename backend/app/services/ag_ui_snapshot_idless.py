"""Match migrated, ID-less assistant text within one checkpoint-stamped run."""

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from typing import Any


def _plain_text(row: Mapping[str, Any]) -> str | None:
    text = row.get("content")
    if row.get("role") != "assistant" or row.get("subagentRunId") or row.get("toolCalls"):
        return None
    return text if isinstance(text, str) and text else None


def match_idless_run_text(
    rows: Sequence[Mapping[str, Any]],
    matches: list[int | None],
    checkpoints: Mapping[int, Mapping[str, Any]],
    idless_ids: Collection[str],
) -> set[int]:
    """Fill only unique, ordered matches and withhold ambiguous projected aliases."""
    by_text: dict[str, set[int]] = defaultdict(set)
    for index, checkpoint in checkpoints.items():
        if checkpoint.get("id") in idless_ids and (text := _plain_text(checkpoint)):
            by_text[text].add(index)
    if not by_text:
        return set()

    proposed: dict[int, set[int]] = {}
    prefix: int | None = None
    for position, row in enumerate(rows):
        anchor = matches[position]
        if anchor is not None:
            prefix = anchor
            continue
        text = _plain_text(row)
        if text in by_text:
            proposed[position] = {i for i in by_text[text] if prefix is None or prefix < i}

    suffix: int | None = None
    offers: dict[int, set[int]] = defaultdict(set)
    for position in reversed(range(len(rows))):
        if matches[position] is not None:
            suffix = matches[position]
        elif position in proposed:
            choices = proposed[position]
            if suffix is not None:
                choices.intersection_update(i for i in choices if i < suffix)
            for index in choices:
                offers[index].add(position)

    resolved = {
        position: index
        for index, positions in offers.items()
        if len(positions) == 1
        for position in positions
        if len(proposed[position]) == 1
    }
    positions = sorted(resolved)
    accepted = [resolved[position] for position in positions]
    if accepted != sorted(accepted):
        # A different ordering cannot prove that the same saved turn produced both texts.
        return set(proposed)
    for position, index in resolved.items():
        matches[position] = index
    return set(proposed) - resolved.keys()
