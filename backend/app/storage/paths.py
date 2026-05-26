"""Storage path resolution (ADR-018).

Persisted ``storage_path`` columns hold values *relative to*
``settings.data_root``. This module is the single place that resolves them
back to absolute paths at read time. Direct ``Path(skill.storage_path)`` use
in service code is a regression — always go through :func:`resolve_data_path`.

Legacy absolute paths (M44 sweeps these out, but defence-in-depth) are
returned as-is so a partially-migrated DB still reads correctly.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def _data_root() -> Path:
    return Path(settings.data_root).resolve()


def resolve_data_path(value: str | os.PathLike[str]) -> Path:
    """Return an absolute path for a ``storage_path`` column value.

    Relative input is resolved against ``settings.data_root``. Absolute
    input is returned as-is (legacy / external mount fallback). Raises
    ``ValueError`` on empty input.
    """

    if not value:
        raise ValueError("empty storage_path")
    path = Path(value)
    if path.is_absolute():
        logger.debug("resolve_data_path got absolute input: %s", path)
        return path
    return (_data_root() / path).resolve()


def ensure_relative(value: str) -> str:
    """Guardrail before assigning to a ``storage_path`` column — reject
    empty/absolute values so ADR-018's invariant holds at the write site."""

    if not value:
        raise ValueError("empty storage_path")
    if Path(value).is_absolute():
        raise ValueError(
            f"storage_path must be relative to data_root, got absolute: {value}"
        )
    return value
