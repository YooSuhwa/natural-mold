"""Langfuse payload masking (split from ``langfuse``; re-exported there).

I-08 (plan 8.9 "Langfuse 추적"): one Langfuse project serves the platform, so in a
multi-tenant (SaaS) deployment input/output bodies are never recorded, whatever
``LANGFUSE_CAPTURE_INPUT_OUTPUT`` says; single-tenant installations keep the setting.
"""

from __future__ import annotations

import re
from typing import Any

from app.agent_runtime.run_secrets import get_run_secrets
from app.config import settings
from app.marketplace.redaction import replace_secret_values

_SECRET_VALUE_KEYS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "password",
    "refresh_token",
    "secret",
    "token",
)
_TOKEN_RE = re.compile(r"\b(sk|pk|pat|Bearer)[-_A-Za-z0-9]{12,}\b")
_SCOPE_KEY_PREFIXES = ("moldy_", "langfuse_")
_HEX_SECRET_RE = re.compile(r"\b[a-fA-F0-9]{32,}\b")


def capture_io_enabled() -> bool:
    """Whether trace input/output bodies may be recorded (never in multi-tenant)."""

    return bool(settings.langfuse_capture_input_output) and (
        settings.deployment_mode != "multi_tenant"
    )


def _mask_run_secrets(text: str) -> str:
    """Strip the active run's known injected secret values from ``text``.

    ADR-021 M3 — the Langfuse ``mask`` callback runs synchronously inside the
    run's async task (span attributes are masked at observation
    creation/update/end, NOT deferred to a background flush thread; see
    ``langfuse/_client/span.py``), so the run-scoped ContextVar is live here.
    We read it directly rather than snapshotting at capture-registration time.

    No-op when no run is active (``get_run_secrets()`` returns ``None`` — unit
    tests, trigger mode) or the set is empty. Reuses the shared exact-substring
    ``replace_secret_values`` (ReDoS-impossible) instead of new heuristics.
    """

    secrets = get_run_secrets()
    if not secrets:
        return text
    return replace_secret_values(text, secrets, placeholder="[redacted]")


def redact_payload(data: Any, **_kwargs: Any) -> Any:
    if not capture_io_enabled():
        if isinstance(data, dict):
            redacted = {}
            for key, value in data.items():
                if str(key).lower() in {"input", "output", "messages", "content", "prompt"}:
                    redacted[key] = "[redacted]"
                elif str(key).startswith(_SCOPE_KEY_PREFIXES):
                    # I-08: trace scope metadata (company/org/run ids, tags) survives the
                    # body redaction so the shared project stays filterable per company.
                    redacted[key] = value
                else:
                    redacted[key] = redact_payload(value)
            return redacted
        if isinstance(data, list):
            return [redact_payload(item) for item in data]
        if isinstance(data, str):
            return "[redacted]"
        return data

    if not settings.langfuse_redaction_enabled:
        return data

    if isinstance(data, dict):
        result: dict[Any, Any] = {}
        for key, value in data.items():
            key_l = str(key).lower()
            if any(secret in key_l for secret in _SECRET_VALUE_KEYS):
                result[key] = "[redacted]"
            else:
                result[key] = redact_payload(value)
        return result
    if isinstance(data, list):
        return [redact_payload(item) for item in data]
    if isinstance(data, str):
        # Value-based masking FIRST (ADR-021): strip the run's known injected
        # secrets by exact substring, then fall back to the key/value
        # heuristics for secrets not in the run set (LLM-generated tokens etc.).
        masked = _mask_run_secrets(data)
        masked = _TOKEN_RE.sub("[redacted]", masked)
        return _HEX_SECRET_RE.sub("[redacted]", masked)
    return data
