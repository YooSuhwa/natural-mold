"""Multi-source AI model catalog pipeline.

Aggregates pricing/capability/ranking metadata from public datasets
(LiteLLM, OpenRouter, llm-prices, pydantic genai-prices) into a single
3-layer-merged ``catalog.json`` consumed by ``app.services.model_metadata``.

Pipeline composition is borrowed in shape from the ENTERPILOT/ai-model-list
project (see ``NOTICES.md``) — the loader/normalize/merge/resolve split, the
provider-default + model-default + provider-override layering, the JSON
Schema-validated output, and the sparse/additive null-inheritance rule. The
implementation, identifiers, normalization rules, and CLI wiring are
Moldy-native.
"""

from __future__ import annotations

__all__ = [
    "loaders",
    "merge",
    "normalize",
    "resolve",
    "rules",
    "validate",
]
