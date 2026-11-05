"""The ``kr_model`` pack: the pii-detector service (docker/pii-detector) plus the RRN fallback.

guardrail-minimum-plan.md §2: schift-ko-pii v7 with its ko-pii adapter, plus the RRN pattern
without the checksum (benchmark strict F1 0.78). The model runs in its own container, so the
pack is async and sends texts in batches.

Texts the service does not answer (not configured, unreachable, slow, a bad reply, or a text
over its size limit) are checked with the ``kr`` rules pack instead: detection gets weaker but
is never skipped.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

import httpx
from anyio.to_thread import run_sync

from app.config import settings
from app.guardrails.detectors import Finding, detect, merge, rrn_fallback
from app.outbound.client import outbound_http_client

logger = logging.getLogger(__name__)

MODEL_PACK = "kr_model"
# Warn when the service stops answering and when it is back, not on every call.
_service_down = False
# The service's limits (docker/pii-detector/server.py), with room for the JSON envelope.
_MAX_TEXTS_PER_REQUEST = 64
_MAX_REQUEST_BYTES = (1 << 20) - 1024


def _batches(texts: Sequence[str]) -> list[list[int]]:
    """Text indexes grouped into requests; a text too large for any request is left out."""
    batches: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, text in enumerate(texts):
        encoded = len(json.dumps(text, ensure_ascii=False).encode()) + 1
        if encoded > _MAX_REQUEST_BYTES:
            continue
        if current and (
            len(current) == _MAX_TEXTS_PER_REQUEST or size + encoded > _MAX_REQUEST_BYTES
        ):
            batches.append(current)
            current, size = [], 0
        current.append(index)
        size += encoded
    if current:
        batches.append(current)
    return batches


def _parse(payload: object, texts: list[str]) -> list[list[Finding]]:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or len(results) != len(texts):
        raise ValueError("results do not match the request")
    parsed: list[list[Finding]] = []
    for text, items in zip(texts, results, strict=True):
        findings = []
        for item in items:
            start, end = int(item["start"]), int(item["end"])
            if not 0 <= start < end <= len(text):
                raise ValueError("span outside the text")
            findings.append(
                Finding(str(item["entity"]), start, end, float(item["score"]), MODEL_PACK)
            )
        parsed.append(findings)
    return parsed


async def _ask_service(client: httpx.AsyncClient, texts: list[str]) -> list[list[Finding]]:
    headers = {"Content-Type": "application/json"}
    if settings.guardrail_pii_model_api_key:
        headers["Authorization"] = f"Bearer {settings.guardrail_pii_model_api_key}"
    response = await client.post(
        f"{settings.guardrail_pii_model_url.rstrip('/')}/detect",
        content=json.dumps({"texts": texts}, ensure_ascii=False).encode(),
        headers=headers,
    )
    response.raise_for_status()
    return _parse(response.json(), texts)


async def _model_findings(texts: Sequence[str]) -> dict[int, list[Finding]]:
    """Findings per text index for the texts the service answered."""
    global _service_down
    answered: dict[int, list[Finding]] = {}
    if not settings.guardrail_pii_model_url:
        return answered
    timeout = httpx.Timeout(settings.guardrail_pii_model_timeout_seconds)
    async with outbound_http_client(timeout=timeout) as client:
        for batch in _batches(texts):
            try:
                results = await _ask_service(client, [texts[i] for i in batch])
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                if not _service_down:
                    # The error type only: the texts under check never reach the log.
                    logger.warning(
                        "guardrail model pack unavailable, using the kr rules pack: %s",
                        type(exc).__name__,
                    )
                _service_down = True
                break
            if _service_down:
                logger.warning("guardrail model pack available again")
            _service_down = False
            answered.update(zip(batch, results, strict=True))
    return answered


def _rule_findings(texts: Sequence[str], packs: Sequence[str]) -> list[list[Finding]]:
    return [detect(text, packs) for text in texts]


async def detect_texts(texts: Sequence[str], packs: Sequence[str]) -> list[list[Finding]]:
    """``detectors.detect`` for many texts, off the event loop, with ``kr_model`` allowed."""
    rule_packs = [name for name in packs if name != MODEL_PACK]
    found = await run_sync(_rule_findings, texts, rule_packs)
    if MODEL_PACK not in packs:
        return found
    model = await _model_findings(texts)
    missed = [i for i in range(len(texts)) if i not in model]
    fallback = await run_sync(_rule_findings, [texts[i] for i in missed], ["kr"])
    rules = dict(zip(missed, fallback, strict=True))
    for index, text in enumerate(texts):
        # The rules pack already carries the RRN pattern; the service answer does not.
        extra = model[index] + rrn_fallback(text, MODEL_PACK) if index in model else rules[index]
        found[index] = merge(found[index] + extra)
    return found
