"""HTTP fetchers for the upstream price-list snapshots.

We pull from the ENTERPILOT/ai-model-price-list mirror — a public repo that
already aggregates the four primary datasets on a 6-hour cron — instead of
hitting LiteLLM/OpenRouter/etc. directly. That keeps the PoC narrow:
one network surface, predictable rate limits, no API keys required.

Atomic write semantics: a successful fetch overwrites the snapshot via
``write_text`` after a stable JSON parse. A failed fetch leaves the previous
snapshot untouched so the catalog build can still run on yesterday's data.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# Raw URLs (ENTERPILOT/ai-model-price-list). All MIT/Apache-2.0 / unspecified
# upstream — see NOTICES.md for attribution.
SOURCES: dict[str, str] = {
    "litellm": "https://raw.githubusercontent.com/ENTERPILOT/ai-model-price-list/main/sources/litellm_model_prices.json",
    "openrouter": "https://raw.githubusercontent.com/ENTERPILOT/ai-model-price-list/main/sources/openrouter_models.json",
    "llm_prices": "https://raw.githubusercontent.com/ENTERPILOT/ai-model-price-list/main/sources/llm_prices_current.json",
    "pydantic_genai": "https://raw.githubusercontent.com/ENTERPILOT/ai-model-price-list/main/sources/pydantic_genai_prices.json",
    # ai-model-list: pre-merged registry with LMArena / LiveBench / AA rankings.
    # ENTERPILOT publishes this with their own AA API key, so we get rankings
    # for free without managing keys ourselves.
    "ai_model_list": "https://raw.githubusercontent.com/ENTERPILOT/ai-model-list/main/models.json",
}

_FILENAMES: dict[str, str] = {
    "litellm": "litellm_model_prices.json",
    "openrouter": "openrouter_models.json",
    "llm_prices": "llm_prices_current.json",
    "pydantic_genai": "pydantic_genai_prices.json",
    "ai_model_list": "ai_model_list.json",
}

# Per-request HTTP timeout. Public mirrors are fast; 30s is a generous ceiling.
FETCH_TIMEOUT_SEC = 30.0


def _utc_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _data_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data" / "model_catalog"


def default_sources_dir() -> Path:
    """Repo-relative path of the snapshot directory."""

    return _data_root() / "sources"


def default_metadata_path() -> Path:
    return _data_root() / "fetch_metadata.json"


async def fetch_source(
    name: str, url: str, *, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """Fetch one source and return ``{status, sha256, payload}``.

    Raises only on programmer error (unknown source name). Network failures
    are surfaced via ``status='error'`` so the caller can decide whether to
    keep the previous snapshot.
    """

    if name not in SOURCES:
        raise ValueError(f"unknown source name: {name}")

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=FETCH_TIMEOUT_SEC, follow_redirects=True)

    try:
        response = await client.get(url)  # type: ignore[union-attr]
        response.raise_for_status()
        body = response.content
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            return {
                "status": "error",
                "http_status": response.status_code,
                "error": f"json decode failed: {exc}",
            }
        return {
            "status": "ok",
            "http_status": response.status_code,
            "sha256": _sha256(body),
            "payload": parsed,
            "bytes": len(body),
        }
    except httpx.HTTPError as exc:
        logger.warning("fetch %s failed: %s", name, exc)
        return {"status": "error", "error": str(exc)}
    finally:
        if own_client and client is not None:
            await client.aclose()


async def fetch_all(sources_dir: Path | None = None) -> dict[str, Any]:
    """Fetch every source concurrently, persist successful payloads, return metadata.

    Returns a dict keyed by ``fetched_at`` (str) and ``sources`` (per-source
    dict containing ``status``, ``http_status``, ``sha256``, ``bytes`` or
    ``error``). The on-disk snapshot file is only rewritten on success — a
    failed source leaves the prior copy intact.
    """

    target_dir = sources_dir or default_sources_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SEC, follow_redirects=True) as client:
        coros = [fetch_source(name, url, client=client) for name, url in SOURCES.items()]
        results = await asyncio.gather(*coros, return_exceptions=False)

    metadata: dict[str, Any] = {"fetched_at": _utc_iso(), "sources": {}}

    for name, result in zip(SOURCES.keys(), results, strict=True):
        entry: dict[str, Any] = {"status": result.get("status", "error")}
        if "http_status" in result:
            entry["http_status"] = result["http_status"]
        if result.get("status") == "ok":
            payload_path = target_dir / _FILENAMES[name]
            try:
                _atomic_write_json(payload_path, result["payload"])
            except OSError as exc:
                entry["status"] = "error"
                entry["error"] = f"write failed: {exc}"
            else:
                entry["sha256"] = result.get("sha256")
                entry["bytes"] = result.get("bytes")
        else:
            entry["error"] = result.get("error", "unknown")
        metadata["sources"][name] = entry

    _atomic_write_json(default_metadata_path(), metadata)
    return metadata


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write ``payload`` to ``path`` via temp + rename so partial writes never land."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ---- Snapshot readers ------------------------------------------------------


def load_snapshot(source: str, sources_dir: Path | None = None) -> Any | None:
    """Read a single snapshot file, returning ``None`` if absent."""

    target_dir = sources_dir or default_sources_dir()
    filename = _FILENAMES.get(source)
    if filename is None:
        return None
    path = target_dir / filename
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_all_snapshots(sources_dir: Path | None = None) -> dict[str, Any]:
    """Return every snapshot present on disk, keyed by source name."""

    out: dict[str, Any] = {}
    for source in SOURCES:
        payload = load_snapshot(source, sources_dir)
        if payload is not None:
            out[source] = payload
    return out


def load_metadata(metadata_path: Path | None = None) -> dict[str, Any]:
    path = metadata_path or default_metadata_path()
    if not path.exists():
        return {"fetched_at": None, "sources": {}}
    with path.open(encoding="utf-8") as f:
        return json.load(f)
