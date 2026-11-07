"""Company LiteLLM gateway calls, cached on disk so a re-run costs nothing.

Only ``LITELLM_BASE_URL`` (already ends in ``/v1``) and ``LITELLM_API_KEY`` are read; neither
is ever printed or written to the cache. Chat replies are cached per request hash, vectors
per ``model@dimensions`` and text hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import httpx
import numpy as np

CACHE = Path(__file__).parent / "cache"
DIMENSIONS = 1024
calls = {"chat": 0, "embeddings": 0}


def _post(path: str, body: dict) -> dict:
    base = os.environ["LITELLM_BASE_URL"].rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['LITELLM_API_KEY']}"}
    for attempt in range(3):
        try:
            response = httpx.post(f"{base}{path}", json=body, headers=headers, timeout=300)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:  # report the kind only, never the request
            if attempt == 2:
                raise RuntimeError(f"gateway {path} failed: {type(exc).__name__}") from None
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def chat(model: str, prompt: str) -> str:
    """One user message to ``model``; the reply text, from the cache when present."""
    body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    path = CACHE / "llm" / f"{_hash(json.dumps(body, ensure_ascii=False))}.json"
    if path.exists():
        return json.loads(path.read_text())["reply"]
    calls["chat"] += 1
    reply = _post("/chat/completions", body)["choices"][0]["message"]["content"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "prompt": prompt, "reply": reply}, indent=1))
    return reply


class Vectors:
    """Unit vectors of one ``model@1024``: ``index.json`` (text hashes) + ``vectors.npy``."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.dir = CACHE / "vectors" / f"{model}@{DIMENSIONS}"
        index = self.dir / "index.json"
        self.hashes: list[str] = json.loads(index.read_text()) if index.exists() else []
        self.matrix = (
            np.load(self.dir / "vectors.npy")
            if self.hashes
            else np.zeros((0, DIMENSIONS), dtype=np.float32)
        )
        self.row = {h: i for i, h in enumerate(self.hashes)}

    def get(self, text: str) -> np.ndarray:
        return self.matrix[self.row[_hash(text)]]

    def ensure(self, texts: list[str], batch: int = 64) -> None:
        missing = list(dict.fromkeys(t for t in texts if _hash(t) not in self.row))
        for start in range(0, len(missing), batch):
            chunk = missing[start : start + batch]
            calls["embeddings"] += 1
            body = {"model": self.model, "input": chunk, "dimensions": DIMENSIONS}
            data = sorted(_post("/embeddings", body)["data"], key=lambda item: item["index"])
            rows = np.asarray([item["embedding"] for item in data], dtype=np.float32)
            rows /= np.linalg.norm(rows, axis=1, keepdims=True)
            self.matrix = np.vstack([self.matrix, rows])
            for text in chunk:
                self.row[_hash(text)] = len(self.hashes)
                self.hashes.append(_hash(text))
        if missing:
            self.dir.mkdir(parents=True, exist_ok=True)
            np.save(self.dir / "vectors.npy", self.matrix)
            (self.dir / "index.json").write_text(json.dumps(self.hashes))

    def timed_single(self, text: str) -> float:
        """Milliseconds for one uncached single-text request (query-embedding latency)."""
        calls["embeddings"] += 1
        started = time.perf_counter()
        _post("/embeddings", {"model": self.model, "input": [text], "dimensions": DIMENSIONS})
        return (time.perf_counter() - started) * 1000
