"""Memory embeddings (memory policy plan 15, 5.5).

A company uses one embedding model for memory: vectors carry its name and width, recall
compares only vectors of the model in use, and a new model means embedding every memory
again (``memory_index``). The model comes from the embedding model kind of the model
registry, picked in the memory settings (next PR); ``resolve_embedder`` has none yet.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from app.outbound.client import outbound_http_client

logger = logging.getLogger(__name__)


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
    """The company's memory vector model, or ``None`` (recall then uses keywords only).

    No model can be chosen yet: the embedding model kind and the memory setting that picks
    one arrive in the next PR (plan 5.5). Until then every run recalls by keywords.
    """
    return None


__all__ = [
    "MemoryEmbedder",
    "OpenAIShapedEmbedder",
    "from_bytes",
    "normalized",
    "resolve_embedder",
    "text_hash",
    "to_bytes",
]
