"""Memory embeddings (memory policy plan 15, 5.5; the minimal 순번 12 embedding model kind).

The operator picks the model in the system LLM settings' ``embedding`` role (a company
override wins, as for every role). Memory uses that one model: vectors carry its name and
width, recall compares only vectors of the model in use, and a new model means embedding
every memory again (``memory_index``). The default deployment uses the company LiteLLM
gateway's ``text-embedding-3-large`` at ``memory_embedding_dimensions`` (1024) wide.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from app.config import settings
from app.database import async_session, platform_session
from app.outbound.client import outbound_http_client

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
# Providers whose API takes OpenAI's ``POST /embeddings`` shape.
_OPENAI_SHAPED = frozenset({"openai", "openai_compatible", "litellm", "openrouter"})


class MemoryEmbedder(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: list[str], *, seconds: float) -> list[np.ndarray]: ...


@dataclass(frozen=True, slots=True)
class OpenAIShapedEmbedder:
    model: str
    dimensions: int
    api_key: str | None
    base_url: str

    async def embed(self, texts: list[str], *, seconds: float) -> list[np.ndarray]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with outbound_http_client(timeout=seconds) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/embeddings",
                json={"model": self.model, "input": texts, "dimensions": self.dimensions},
                headers=headers,
            )
            response.raise_for_status()
            data = sorted(response.json()["data"], key=lambda item: item["index"])
        vectors = [normalized(item["embedding"]) for item in data]
        if len(vectors) != len(texts) or any(v.shape != (self.dimensions,) for v in vectors):
            raise ValueError("embedding reply does not match the request")
        return vectors


def normalized(values: object) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def to_bytes(vector: np.ndarray) -> bytes:
    return vector.astype("<f4").tobytes()


def from_bytes(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def resolve_embedder(tenant_id: uuid.UUID | None) -> MemoryEmbedder | None:
    """The configured embedding model, or ``None`` (recall then uses keywords only)."""
    from app.services.system_credential_resolver import (
        SystemModelNotConfiguredError,
        resolve_system_model,
    )

    try:
        async with platform_session("system_llm.resolve", maker=async_session) as db:
            resolved = await resolve_system_model(db, "embedding", tenant_id=tenant_id)
    except SystemModelNotConfiguredError:
        return None
    except Exception:  # noqa: BLE001 - a broken setting must not break a run
        logger.warning("embedding model lookup failed", exc_info=True)
        return None
    if resolved.provider not in _OPENAI_SHAPED:
        logger.warning("embedding provider %s is not supported", resolved.provider)
        return None
    return OpenAIShapedEmbedder(
        model=resolved.model_name,
        dimensions=settings.memory_embedding_dimensions,
        api_key=resolved.api_key,
        base_url=resolved.base_url or _DEFAULT_BASE_URL,
    )


__all__ = [
    "MemoryEmbedder",
    "OpenAIShapedEmbedder",
    "from_bytes",
    "normalized",
    "resolve_embedder",
    "text_hash",
    "to_bytes",
]
