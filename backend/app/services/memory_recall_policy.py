"""Independent, operator-reviewed evidence for a tenant's selected embedding model.

Each organization can opt in to a qualified company model. Reviewers install the
evaluator's report outside tenant-writable storage and set its measured threshold.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal

from app.config import settings

type RecallMode = Literal["off", "shadow", "serve"]

logger = logging.getLogger(__name__)
SCHEMA: Final = "moldy.memory-recall.quality.v1"
# SHA-256 of the reviewed independent Korean report set (including its gold labels).
CORPUS_SHA256: Final = "9eb543ed3fff0302cfabb55a7ea127534cc34eca663c5fdd06319f38b578898a"
CATEGORIES: Final = ("same_word", "particle", "paraphrase", "time", "multi_turn", "unrelated")
_MAX_REPORT_BYTES: Final = 8 * 1024 * 1024


def requested_mode(memory: dict[str, Any]) -> RecallMode:
    value = memory.get("recall_mode")
    return value if value in ("shadow", "serve") else "off"


@lru_cache(maxsize=64)
def _read_report(path: Path, _inode: int, _ctime: int, _mtime: int, size: int) -> dict[str, Any]:
    if size > _MAX_REPORT_BYTES:
        raise ValueError("quality report exceeds size limit")
    report = json.loads(path.read_bytes())
    if not isinstance(report, dict):
        raise ValueError("invalid quality report")
    return report


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _results_valid(mode: dict[str, Any]) -> bool:
    categories = mode["categories"]
    for category in CATEGORIES:
        result = categories[category]
        expected_metric = "irrelevant_rejection" if category == "unrelated" else "recall_at_10"
        lo, hi = result["ci95"]
        if (
            result["metric"] != expected_metric
            or type(result["n"]) is not int
            or result["n"] < 25
            or not all(_number(x) and 0 <= x <= 1 for x in (lo, hi, result["value"]))
            or lo > hi
        ):
            return False
    for key in ("mean_prompt_tokens", "p95_elapsed_ms"):
        measure = mode[key]
        lo, hi = measure["ci95"]
        if (
            not all(_number(bound) and bound >= 0 for bound in (lo, hi))
            or lo > hi
            or measure["n"] != sum(categories[c]["n"] for c in CATEGORIES)
            or not _number(measure["value"])
            or measure["value"] < 0
        ):
            return False
    return True


def passing_floor(
    *,
    tenant_id: uuid.UUID,
    model_id: uuid.UUID,
    provider: str,
    base_url: str,
    model_name: str,
    dimensions: int,
) -> float | None:
    from app.services.memory_recall import KEYWORD_FLOOR

    directory = settings.memory_recall_quality_dir
    if directory is None or settings.opensearch_url:
        return None
    path = directory / str(tenant_id) / f"{model_id}.json"
    try:
        metadata = path.stat()
        report = _read_report(
            path, metadata.st_ino, metadata.st_ctime_ns, metadata.st_mtime_ns, metadata.st_size
        )
        if (
            report["schema"] != SCHEMA
            or report["corpus_sha256"] != CORPUS_SHA256
            or report["analyzer"] != "suffix"
            or report["keyword_floor"] != KEYWORD_FLOOR
            or report["gate"]["passed"] is not True
        ):
            return None
        identity = {
            "tenant_id": str(tenant_id),
            "model_id": str(model_id),
            "provider": provider,
            "base_url": base_url.rstrip("/"),
            "model": model_name,
            "dimensions": dimensions,
        }
        candidate = report["candidate"]
        if not all(candidate[key] == value for key, value in identity.items()):
            return None
        floor = candidate["cosine_floor"]
        if not _number(floor) or not 0 < floor <= 1:
            return None
        key, hybrid = report["modes"]["keyword"], report["modes"]["hybrid"]
        if not (_results_valid(key) and _results_valid(hybrid)):
            return None
        baseline, trial = key["categories"], hybrid["categories"]
        if not all(baseline[c]["n"] == trial[c]["n"] for c in CATEGORIES):
            return None
        if (
            trial["unrelated"]["value"] >= 0.90
            and trial["paraphrase"]["value"] > baseline["paraphrase"]["value"]
        ):
            return float(floor)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        logger.warning("Invalid memory quality report for model %s", model_id)
    return None
