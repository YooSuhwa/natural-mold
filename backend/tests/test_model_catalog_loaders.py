"""Loader tests — fetch_source/fetch_all + atomic snapshot writes."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.services.model_catalog import loaders

# --------------------------------------------------------------------------
# fetch_source
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_source_unknown_name_raises() -> None:
    with pytest.raises(ValueError, match="unknown source name"):
        await loaders.fetch_source("totally-fake-source", "https://example.com")


@pytest.mark.asyncio
async def test_fetch_source_returns_payload_on_success() -> None:
    body = {"hello": "world"}
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=json.dumps(body).encode())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await loaders.fetch_source("litellm", "https://x", client=client)
    assert result["status"] == "ok"
    assert result["http_status"] == 200
    assert result["payload"] == body
    assert "sha256" in result and len(result["sha256"]) == 64
    assert result["bytes"] == len(json.dumps(body).encode())


@pytest.mark.asyncio
async def test_fetch_source_handles_http_error() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await loaders.fetch_source("openrouter", "https://x", client=client)
    assert result["status"] == "error"
    assert "error" in result


@pytest.mark.asyncio
async def test_fetch_source_handles_invalid_json() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"not-json"))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await loaders.fetch_source("llm_prices", "https://x", client=client)
    assert result["status"] == "error"
    assert "json decode" in result["error"]


# --------------------------------------------------------------------------
# fetch_all + atomic write
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_all_writes_payloads_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every successful fetch lands on disk; metadata records statuses."""

    def handler(req: httpx.Request) -> httpx.Response:
        # Different bodies per source so we can verify per-source files.
        body = {"source": req.url.path.split("/")[-1]}
        return httpx.Response(200, content=json.dumps(body).encode())

    monkeypatch.setattr(
        "app.services.model_catalog.loaders.default_metadata_path",
        lambda: tmp_path / "fetch_metadata.json",
    )

    # Patch httpx.AsyncClient so the fetcher sees our mock transport.
    transport = httpx.MockTransport(handler)
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        kwargs.pop("follow_redirects", None)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    metadata = await loaders.fetch_all(sources_dir=tmp_path / "sources")

    assert metadata["fetched_at"]
    assert set(metadata["sources"].keys()) == set(loaders.SOURCES.keys())
    for src, info in metadata["sources"].items():
        assert info["status"] == "ok", f"{src} should succeed"
        assert info["sha256"]
        assert (tmp_path / "sources" / loaders._FILENAMES[src]).exists()

    written_metadata = json.loads((tmp_path / "fetch_metadata.json").read_text())
    assert written_metadata == metadata


@pytest.mark.asyncio
async def test_fetch_all_keeps_previous_snapshot_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed fetch must not clobber the existing snapshot file."""

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    # Pre-seed a "previous" litellm snapshot.
    prior = {"prior": True}
    (sources_dir / loaders._FILENAMES["litellm"]).write_text(json.dumps(prior))

    monkeypatch.setattr(
        "app.services.model_catalog.loaders.default_metadata_path",
        lambda: tmp_path / "fetch_metadata.json",
    )
    transport = httpx.MockTransport(lambda req: httpx.Response(500))
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        kwargs.pop("follow_redirects", None)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    metadata = await loaders.fetch_all(sources_dir=sources_dir)

    # All sources fail → status='error' but the prior file remains intact.
    for info in metadata["sources"].values():
        assert info["status"] == "error"

    surviving = json.loads((sources_dir / loaders._FILENAMES["litellm"]).read_text())
    assert surviving == prior


def test_load_snapshot_returns_none_for_missing(tmp_path: Path) -> None:
    assert loaders.load_snapshot("litellm", sources_dir=tmp_path / "missing") is None


def test_load_metadata_returns_default_when_missing(tmp_path: Path) -> None:
    md = loaders.load_metadata(metadata_path=tmp_path / "absent.json")
    assert md == {"fetched_at": None, "sources": {}}
