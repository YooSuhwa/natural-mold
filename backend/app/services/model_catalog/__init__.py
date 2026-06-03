"""Multi-source AI model catalog pipeline.

Aggregates pricing/capability/ranking metadata from public datasets
(LiteLLM, OpenRouter, llm-prices, pydantic genai-prices) into a single
3-layer-merged ``catalog.json`` consumed by ``app.services.model_metadata``.

Pipeline composition follows a conventional loader/normalize/merge/resolve
split with provider-default + model-default + provider-override layering,
JSON Schema-validated output, and sparse/additive null-inheritance. The
implementation, identifiers, normalization rules, and CLI wiring are
Moldy-native.
"""

from __future__ import annotations

from . import loaders, merge, normalize, resolve, rules, validate

__all__ = [
    "loaders",
    "merge",
    "normalize",
    "resolve",
    "rules",
    "validate",
]
