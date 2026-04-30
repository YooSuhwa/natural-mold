"""Catalog build orchestration — fetch, normalize, merge, validate, write.

The cron job in ``app.scheduler.register_catalog_update_job`` calls
``update_catalog`` every ``settings.catalog_update_cron`` interval (default
6 hours). The function is also safe to invoke ad-hoc from a Python REPL or
a test fixture; it owns its own HTTP client and is idempotent.

Failure isolation:
- Network failure for one source: keep the previous on-disk snapshot, skip
  it in the build, log a warning.
- All sources fail: still try a build with the cached snapshots.
- Build fails (normalize, merge, JSON Schema): leave the on-disk
  ``catalog.json`` untouched, log + raise.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.model_catalog import loaders, merge, validate

logger = logging.getLogger(__name__)


def _catalog_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "model_catalog" / "catalog.json"


def _providers_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "model_catalog" / "providers.json"


def _curated_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "model_catalog" / "curated"


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_curated() -> dict[str, Any]:
    """Read the curated layer from disk; missing files become empty dicts."""

    curated_dir = _curated_dir()
    out: dict[str, Any] = {}
    for name in ("aliases", "overrides", "excluded"):
        path = curated_dir / f"{name}.json"
        out[name] = _read_json(path) if path.exists() else {}
    return out


def build_from_disk() -> dict[str, Any]:
    """Build the catalog using the snapshots already cached on disk.

    Useful for offline rebuilds and tests — no network access.
    """

    snapshots = loaders.load_all_snapshots()
    providers = _read_json(_providers_path())
    curated = load_curated()
    catalog = merge.build_catalog(snapshots, providers, curated)
    return catalog


async def update_catalog(*, fetch: bool = True) -> dict[str, Any]:
    """Refresh the on-disk snapshots, build the catalog, validate, persist.

    Returns a small report dict with ``fetched_at``, per-source statuses,
    model count, and any validation errors. Raises ``RuntimeError`` if the
    final catalog fails JSON Schema validation — the on-disk file stays at
    the previous good build in that case.
    """

    fetch_metadata: dict[str, Any]
    if fetch:
        try:
            fetch_metadata = await loaders.fetch_all()
        except Exception:  # noqa: BLE001
            logger.exception("fetch_all failed; building from cached snapshots only")
            fetch_metadata = loaders.load_metadata()
    else:
        fetch_metadata = loaders.load_metadata()

    snapshots = loaders.load_all_snapshots()
    providers = _read_json(_providers_path())
    curated = load_curated()

    catalog = merge.build_catalog(snapshots, providers, curated)

    errors = validate.validate_catalog(catalog)
    if errors:
        logger.error("catalog validation failed (%d errors); not writing", len(errors))
        for err in errors[:20]:
            logger.error("  - %s", err)
        raise RuntimeError(
            f"catalog validation failed with {len(errors)} errors; first: {errors[0]}"
        )

    _atomic_write_json(_catalog_path(), catalog)

    # Reset the metadata cache in ``model_metadata`` so the next request
    # picks up the new catalog without a process restart.
    try:
        from app.services import model_metadata
    except Exception:  # noqa: BLE001
        pass
    else:
        model_metadata.reset_catalog_cache()

    report = {
        "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fetched_at": fetch_metadata.get("fetched_at"),
        "sources": fetch_metadata.get("sources", {}),
        "model_count": len(catalog.get("models", {})),
        "provider_model_count": len(catalog.get("provider_models", {})),
        "provider_count": len(catalog.get("providers", {})),
    }
    logger.info(
        "catalog rebuild OK: providers=%d models=%d provider_models=%d",
        report["provider_count"],
        report["model_count"],
        report["provider_model_count"],
    )
    return report


def get_catalog_path() -> Path:
    """Public accessor for the on-disk catalog path."""

    return _catalog_path()
