"""End-to-end updater tests — fetch_all → normalize → merge → validate → write."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.services import model_catalog_updater
from app.services.model_catalog import loaders, validate

_FIXTURE_LITELLM = {
    "claude-haiku-4-5": {
        "litellm_provider": "anthropic",
        "max_input_tokens": 200000,
        "max_output_tokens": 64000,
        "input_cost_per_token": 1e-06,
        "output_cost_per_token": 5e-06,
        "supports_vision": True,
        "supports_function_calling": True,
    }
}

_FIXTURE_OPENROUTER = {
    "data": [
        {
            "id": "anthropic/claude-haiku-4-5",
            "name": "Claude Haiku 4.5",
            "context_length": 200000,
            "pricing": {"prompt": "0.000001", "completion": "0.000005"},
            "top_provider": {"max_completion_tokens": 64000},
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": ["tools"],
        }
    ]
}

_FIXTURE_LLM_PRICES = {
    "prices": [
        {
            "id": "claude-haiku-4-5",
            "vendor": "anthropic",
            "name": "Claude Haiku 4.5",
            "input": 1,
            "output": 5,
        }
    ]
}

_FIXTURE_PYDANTIC = [
    {
        "id": "anthropic",
        "models": [
            {
                "id": "claude-haiku-4-5",
                "name": "Claude Haiku 4.5",
                "context_window": 200000,
                "prices": {"input_mtok": 1, "output_mtok": 5},
            }
        ],
    }
]


@pytest.fixture
def staged_catalog_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stage a self-contained catalog directory and redirect the module's paths there."""

    catalog_dir = tmp_path / "model_catalog"
    sources_dir = catalog_dir / "sources"
    curated_dir = catalog_dir / "curated"
    sources_dir.mkdir(parents=True)
    curated_dir.mkdir(parents=True)

    # Pre-stage every source file so build_from_disk has something to merge.
    (sources_dir / loaders._FILENAMES["litellm"]).write_text(json.dumps(_FIXTURE_LITELLM))
    (sources_dir / loaders._FILENAMES["openrouter"]).write_text(json.dumps(_FIXTURE_OPENROUTER))
    (sources_dir / loaders._FILENAMES["llm_prices"]).write_text(json.dumps(_FIXTURE_LLM_PRICES))
    (sources_dir / loaders._FILENAMES["pydantic_genai"]).write_text(json.dumps(_FIXTURE_PYDANTIC))

    # Minimal providers + curated layer.
    providers = {
        "openai": {"display_name": "OpenAI", "api_type": "openai"},
        "anthropic": {"display_name": "Anthropic", "api_type": "anthropic"},
    }
    (catalog_dir / "providers.json").write_text(json.dumps(providers))
    (curated_dir / "aliases.json").write_text("{}")
    (curated_dir / "overrides.json").write_text("{}")
    (curated_dir / "excluded.json").write_text("{}")

    fetch_meta = catalog_dir / "fetch_metadata.json"
    catalog_file = catalog_dir / "catalog.json"
    providers_file = catalog_dir / "providers.json"

    monkeypatch.setattr(loaders, "default_sources_dir", lambda: sources_dir)
    monkeypatch.setattr(loaders, "default_metadata_path", lambda: fetch_meta)
    monkeypatch.setattr(model_catalog_updater, "_catalog_path", lambda: catalog_file)
    monkeypatch.setattr(model_catalog_updater, "_providers_path", lambda: providers_file)
    monkeypatch.setattr(model_catalog_updater, "_curated_dir", lambda: curated_dir)

    return catalog_dir


def test_build_from_disk_produces_validated_catalog(staged_catalog_dir: Path) -> None:
    catalog = model_catalog_updater.build_from_disk()
    assert catalog["version"] == 1
    assert "anthropic" in catalog["providers"]
    assert "claude-haiku-4-5" in catalog["models"]
    assert "anthropic/claude-haiku-4-5" in catalog["provider_models"]

    # Sources accumulated from all four normalizers.
    pm = catalog["provider_models"]["anthropic/claude-haiku-4-5"]
    assert set(pm["sources"]) == {"openrouter", "litellm", "pydantic_genai", "llm_prices"}

    assert validate.validate_catalog(catalog) == []


@pytest.mark.asyncio
async def test_update_catalog_writes_artifact_and_returns_report(
    staged_catalog_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fetch=False path: build from cached snapshots, validate, write atomically."""

    report = await model_catalog_updater.update_catalog(fetch=False)
    assert report["model_count"] >= 1
    assert report["provider_model_count"] >= 1

    # File written via atomic rename (no .tmp leftovers).
    catalog_path = staged_catalog_dir / "catalog.json"
    assert catalog_path.exists()
    assert not (catalog_path.with_suffix(catalog_path.suffix + ".tmp")).exists()

    written = json.loads(catalog_path.read_text())
    assert "claude-haiku-4-5" in written["models"]


@pytest.mark.asyncio
async def test_update_catalog_with_network_uses_mock_transport(
    staged_catalog_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full path: HTTP → snapshot → build → validate → write."""

    fixture_by_filename = {
        loaders._FILENAMES["litellm"]: _FIXTURE_LITELLM,
        loaders._FILENAMES["openrouter"]: _FIXTURE_OPENROUTER,
        loaders._FILENAMES["llm_prices"]: _FIXTURE_LLM_PRICES,
        loaders._FILENAMES["pydantic_genai"]: _FIXTURE_PYDANTIC,
    }

    def handler(req: httpx.Request) -> httpx.Response:
        filename = req.url.path.split("/")[-1]
        body = fixture_by_filename.get(filename, {})
        return httpx.Response(200, content=json.dumps(body).encode())

    transport = httpx.MockTransport(handler)
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        kwargs.pop("follow_redirects", None)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    report = await model_catalog_updater.update_catalog(fetch=True)
    assert report["fetched_at"]
    for src_status in report["sources"].values():
        assert src_status["status"] == "ok"
    assert report["model_count"] >= 1


@pytest.mark.asyncio
async def test_update_catalog_keeps_old_artifact_when_validation_fails(
    staged_catalog_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the merged catalog fails JSON Schema, the on-disk file must not change."""

    # Pre-write a "good" catalog so we can detect tampering.
    good = {
        "version": 1,
        "updated_at": "2020-01-01T00:00:00Z",
        "providers": {},
        "models": {},
        "provider_models": {},
    }
    (staged_catalog_dir / "catalog.json").write_text(json.dumps(good))

    # Force the merge step to emit something the schema rejects.
    def fake_build(*args, **kwargs):  # type: ignore[no-untyped-def]
        return {"version": 99, "providers": {}, "models": {}, "provider_models": {}}

    monkeypatch.setattr("app.services.model_catalog.merge.build_catalog", fake_build)

    with pytest.raises(RuntimeError):
        await model_catalog_updater.update_catalog(fetch=False)

    # Untouched.
    on_disk = json.loads((staged_catalog_dir / "catalog.json").read_text())
    assert on_disk == good
