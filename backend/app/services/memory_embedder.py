"""Memory embeddings (memory policy plan 15, 5.5).

A company uses one embedding model for memory: vectors carry its name and width, recall
compares only vectors of the model in use, and a new model means embedding every memory
again (``memory_index``). The model comes from the embedding model kind of the model
registry (``models.kind = 'embedding'``), picked in the company memory settings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import uuid
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from app.database import async_session, platform_session
from app.outbound.client import outbound_http_client
from app.services.memory_recall_policy import RecallMode

logger = logging.getLogger(__name__)

# Providers whose API takes OpenAI's ``POST /embeddings`` request shape.
OPENAI_SHAPED = frozenset({"openai", "openai_compatible", "litellm", "openrouter"})


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
    # The company's model-change count (``memory_vectors``): one backfill per change.
    generation: int = 0
    registry_id: uuid.UUID | None = None
    provider: str = ""
    recall_mode: RecallMode = "off"
    recall_floor: float | None = None

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
        if [item["index"] for item in data] != list(range(len(texts))) or not all(
            valid_vector(v, self.dimensions) for v in vectors
        ):
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


def valid_vector(vector: np.ndarray, dimensions: int) -> bool:
    """The stored and query vectors used in a dot product must be finite unit vectors."""
    return vector.shape == (dimensions,) and math.isclose(
        float(np.linalg.norm(vector)), 1.0, rel_tol=1e-5
    )


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def resolve_embedder(
    tenant_id: uuid.UUID | None, *, org_id: uuid.UUID | None = None
) -> MemoryEmbedder | None:
    """The company's memory vector model, or ``None`` (recall then uses keywords only).

    Read in a platform session: the model may be a platform row and its credential a
    platform credential (``memory_vectors.embedder_for`` applies the company's limits).
    """
    if tenant_id is None:
        return None
    from app.models.organization import Tenant
    from app.services import memory_vectors  # it imports this module

    try:
        async with platform_session("memory.embedding_model", maker=async_session) as db:
            tenant = await db.get(Tenant, tenant_id)
            if tenant is None:
                return None
            return await memory_vectors.embedder_for(db, tenant, org_id=org_id)
    except Exception as exc:  # noqa: BLE001 - a broken setting must not break a run
        logger.warning("memory embedding model lookup failed: %s", type(exc).__name__)
        return None


def vector_model_key(embedder: MemoryEmbedder) -> str:
    """Persist the identity of the registered model and the endpoint that produced a vector."""
    registry_id = getattr(embedder, "registry_id", None)
    if registry_id is None:
        return embedder.model
    identity = (
        str(registry_id),
        getattr(embedder, "provider", ""),
        getattr(embedder, "base_url", "").rstrip("/"),
        embedder.model,
        embedder.dimensions,
    )
    return "v2:" + text_hash(json.dumps(identity, separators=(",", ":")))


__all__ = [
    "MemoryEmbedder",
    "OpenAIShapedEmbedder",
    "from_bytes",
    "normalized",
    "resolve_embedder",
    "text_hash",
    "to_bytes",
    "valid_vector",
    "vector_model_key",
]
