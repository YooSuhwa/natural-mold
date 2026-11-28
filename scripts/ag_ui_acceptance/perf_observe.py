"""Read stored measurements for the one six-chunk turn before the browser removes it."""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path
from uuid import UUID

import psycopg
from perf_journal import journal_timing
from perf_model import model_timing


def _journal(conversation: UUID) -> list[dict[str, int | float]]:
    # A v3 browser can see the final delta before the final durable chunk commits.
    deadline = time.monotonic() + 3
    while True:
        try:
            rows = journal_timing(conversation, os.environ["MOLDY_AGUI_OBSERVER_URL"])
            return [
                {"ordinal": row["token_index"], "commitEpochMs": row["commit_epoch_ms"]}
                for row in rows
            ]
        except ValueError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def observe(conversation: UUID, sample: UUID) -> dict:
    output = {"model": [], "journal": [], "errors": []}
    for name, key, read in (
        (
            "model",
            "sourceEpochMs",
            lambda: model_timing(sample, Path(os.environ["MOLDY_AGUI_MODEL_TIMINGS"])),
        ),
        (
            "journal",
            "commitEpochMs",
            lambda: _journal(conversation),
        ),
    ):
        values = {}
        try:
            for row in read():
                ordinal = row["ordinal"]
                value = row[key]
                if ordinal not in range(1, 7) or ordinal in values or not math.isfinite(value):
                    raise ValueError("Invalid measured token binding")
                values[ordinal] = value
            if len(values) != 6:
                output["errors"].append(f"{name}_incomplete")
        except (KeyError, OSError, psycopg.Error, TypeError, ValueError) as exc:
            # No frames, connection strings, query values or provider data in the capture.
            output["errors"].append(f"{name}_{type(exc).__name__}")
        output[name] = [{"ordinal": ordinal, key: values.get(ordinal)} for ordinal in range(1, 7)]
    return output


if __name__ == "__main__":
    print(json.dumps(observe(UUID(sys.argv[1]), UUID(sys.argv[2]))))
