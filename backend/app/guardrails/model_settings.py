"""Which model service the ``kr_model`` pack calls (guardrail-minimum-plan.md §2.1).

The operator's saved row (``guardrail_model_settings``) wins; with no row the environment
applies (``GUARDRAIL_PII_MODEL_URL``, ``GUARDRAIL_PII_MODEL_API_KEY``). Reads go through a
short cache so a guardrail check does not query the database each time; saving clears it in
this process, and other workers pick the change up within ``CACHE_SECONDS``.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.credentials import service as credential_service
from app.credentials.boundary import open_values
from app.database import platform_session
from app.models.guardrail_model_setting import SINGLETON_ID, GuardrailModelSetting
from app.outbound.client import outbound_http_client

logger = logging.getLogger(__name__)

CACHE_SECONDS = 30.0
BEARER_DEFINITION = "http_bearer"
MAX_URL_LENGTH = 2048
# Synthetic; checks that the service answers with spans, not what it finds.
PROBE_TEXT = "담당자 홍길동 과장 010-1234-5678"
PROBE_TIMEOUT_SECONDS = 10.0

ProbeStatus = Literal[
    "ok", "unreachable", "timeout", "unauthorized", "model_missing", "bad_response"
]


@dataclass(frozen=True, slots=True)
class ModelService:
    enabled: bool
    url: str
    source: Literal["environment", "saved"]
    api_key: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    status: ProbeStatus
    latency_ms: int | None = None
    model: str | None = None
    findings: int | None = None


@dataclass(frozen=True, slots=True)
class SettingView:
    """What the operator screen shows: the effective setting and where it comes from."""

    enabled: bool
    url: str
    source: Literal["environment", "saved"]
    environment_url: str
    credential_id: uuid.UUID | None = None
    credential_name: str | None = None
    updated_at: datetime | None = None


_cached: tuple[float, ModelService] | None = None


def environment() -> ModelService:
    url = settings.guardrail_pii_model_url.strip()
    return ModelService(bool(url), url, "environment", settings.guardrail_pii_model_api_key)


def invalid_url_reason(url: str) -> str | None:
    """Why ``url`` cannot be a model service address; ``None`` when it can."""
    if len(url) > MAX_URL_LENGTH:
        return "too_long"
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        return "malformed"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "scheme_or_host"
    if parts.username is not None or parts.password is not None:
        return "credentials_in_url"
    if parts.query or parts.fragment:
        return "query_or_fragment"
    return None


async def bearer_token(db: AsyncSession, credential_id: uuid.UUID) -> str | None:
    """The token of a system ``http_bearer`` credential; ``None`` if it is not one."""
    credential = await credential_service.get_system(db, credential_id)
    if credential is None or credential.definition_key != BEARER_DEFINITION:
        return None
    values = await open_values(credential, "system")
    token = values.get("token")
    return str(token) if token else None


async def load(db: AsyncSession) -> ModelService:
    row = await db.get(GuardrailModelSetting, SINGLETON_ID)
    if row is None:
        return environment()
    api_key = settings.guardrail_pii_model_api_key
    if row.credential_id is not None:
        api_key = await bearer_token(db, row.credential_id) or ""
    return ModelService(row.enabled, row.url, "saved", api_key)


async def view(db: AsyncSession) -> SettingView:
    environment_url = environment().url
    row = await db.get(GuardrailModelSetting, SINGLETON_ID)
    if row is None:
        return SettingView(bool(environment_url), environment_url, "environment", environment_url)
    name = None
    if row.credential_id is not None:
        credential = await credential_service.get_system(db, row.credential_id)
        name = credential.name if credential is not None else None
    return SettingView(
        row.enabled,
        row.url,
        "saved",
        environment_url,
        row.credential_id,
        name,
        row.updated_at,
    )


async def is_bearer_credential(db: AsyncSession, credential_id: uuid.UUID) -> bool:
    credential = await credential_service.get_system(db, credential_id)
    return credential is not None and credential.definition_key == BEARER_DEFINITION


async def save(
    db: AsyncSession,
    *,
    enabled: bool,
    url: str,
    credential_id: uuid.UUID | None,
    user_id: uuid.UUID,
) -> None:
    """Upsert the platform row and commit; this process sees it on the next check."""
    row = await db.get(GuardrailModelSetting, SINGLETON_ID)
    if row is None:
        row = GuardrailModelSetting(id=SINGLETON_ID, enabled=enabled)
        db.add(row)
    row.enabled = enabled
    row.url = url
    row.credential_id = credential_id
    row.updated_by = user_id
    await db.commit()
    invalidate()


async def forget(db: AsyncSession) -> bool:
    """Delete the saved row (the environment applies again); False when there was none."""
    row = await db.get(GuardrailModelSetting, SINGLETON_ID)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    invalidate()
    return True


async def current() -> ModelService:
    global _cached
    now = time.monotonic()
    if _cached is not None and now - _cached[0] < CACHE_SECONDS:
        return _cached[1]
    try:
        async with platform_session("guardrail.model_settings") as db:
            service = await load(db)
    except Exception as exc:  # noqa: BLE001 - a settings outage must not stop guardrail checks
        # The environment never weakens a check: without a URL the rules pack runs.
        logger.warning("guardrail model settings unreadable: %s", type(exc).__name__)
        service = environment()
    _cached = (now, service)
    return service


def invalidate() -> None:
    global _cached
    _cached = None


async def probe(url: str, api_key: str) -> ProbeResult:
    """One ``/detect`` call with a synthetic sentence; only the outcome is reported back.

    The response body is never returned, so the probe does not read arbitrary internal
    pages for the caller (operators only; no redirects are followed).
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    started = time.perf_counter()
    try:
        async with outbound_http_client(timeout=PROBE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{url.rstrip('/')}/detect", json={"texts": [PROBE_TEXT]}, headers=headers
            )
    except httpx.TimeoutException:
        return ProbeResult("timeout")
    except httpx.HTTPError:
        return ProbeResult("unreachable")
    latency = round((time.perf_counter() - started) * 1000)
    if response.status_code == 401:
        return ProbeResult("unauthorized", latency)
    if response.status_code == 503:
        return ProbeResult("model_missing", latency)
    try:
        payload = response.json() if response.status_code == 200 else None
        results = payload["results"] if isinstance(payload, dict) else None
        found = results[0] if isinstance(results, list) and len(results) == 1 else None
    except (ValueError, KeyError, TypeError):
        found = None
    if not isinstance(found, list):
        return ProbeResult("bad_response", latency)
    model = payload.get("model") if isinstance(payload, dict) else None
    return ProbeResult("ok", latency, str(model)[:200] if model else None, len(found))
