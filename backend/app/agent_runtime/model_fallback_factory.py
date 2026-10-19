"""Model fallback walk over ``Agent.model_fallback_list`` (M10).

``create_chat_model`` is read from the ``model_factory`` facade at call time so
tests that patch ``model_factory.create_chat_model`` observe the replacement.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

import httpx
from langchain_core.language_models import BaseChatModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.model_facade_bindings import model_attr

if TYPE_CHECKING:
    from app.models.agent import Agent

logger = logging.getLogger("app.agent_runtime.model_factory")

# ---------------------------------------------------------------------------
# Model fallback (M10)
# ---------------------------------------------------------------------------


# Recoverable error classes — fall back on the next model in the chain.
# Keep this list narrow: a programming error (e.g., ``TypeError``) should
# still surface so we don't silently mask bugs as fallbacks.
_FALLBACK_RECOVERABLE_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,
    httpx.HTTPError,
    httpx.TimeoutException,
    ConnectionError,
)


_FALLBACK_RECOVERABLE_STATUS = frozenset({401, 403, 404, 408, 409, 429, 500, 502, 503, 504})


def _is_fallback_recoverable(exc: BaseException) -> bool:
    """Return ``True`` when ``exc`` looks worth a fallback retry.

    Provider SDKs surface auth / rate / outage failures as ``HTTPStatusError``
    (with a ``response.status_code``) or as their own typed wrappers (e.g.
    ``openai.AuthenticationError``). We unify on:

    1. ``status_code`` attribute / nested response — accept the canonical 4xx
       and 5xx codes from the LiteLLM fallback set.
    2. Subclasses of the recoverable type tuple.
    3. The string fallback (``"timeout"`` / ``"unauthorized"`` etc.) is
       deliberately *not* tried — relying on message strings is fragile and
       has bitten us before.
    """

    if isinstance(exc, _FALLBACK_RECOVERABLE_TYPES):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
    return bool(isinstance(status, int) and status in _FALLBACK_RECOVERABLE_STATUS)


async def _resolve_model_for_fallback(
    db: AsyncSession,
    model_id: uuid.UUID,
) -> tuple[str, str, str | None] | None:
    """Look up a fallback model row → ``(provider, model_name, base_url)``.

    Returns ``None`` when the row is missing so the walker can skip it
    instead of crashing the whole chain.
    """

    from app.models.model import Model as ModelRow

    result = await db.execute(select(ModelRow).where(ModelRow.id == model_id))
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return row.provider, row.model_name, getattr(row, "base_url", None)


async def _audit_fallback_attempt(
    db: AsyncSession,
    *,
    agent: Agent,
    model_id: uuid.UUID | None,
    provider: str,
    model_name: str,
    error: BaseException | None,
    success: bool,
) -> None:
    """Record one ``fallback`` step on the credential audit log.

    The credential is the agent's ``llm_credential`` — fallbacks reuse the
    same key. We swallow audit failures because the agent run must succeed
    even if the audit DB is misbehaving.
    """

    if agent.llm_credential_id is None:
        return
    try:
        from app.credentials import service as credential_service

        metadata: dict[str, Any] = {
            "phase": "attempt",
            "provider": provider,
            "model_name": model_name,
            "success": success,
            "agent_id": str(agent.id),
        }
        if model_id is not None:
            metadata["model_id"] = str(model_id)
        await credential_service.write_audit_log(
            db,
            credential_id=agent.llm_credential_id,
            actor_user_id=agent.user_id,
            action="fallback",
            source="runtime",
            error=str(error) if error else None,
            metadata=metadata,
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — never break the runtime on audit
        logger.warning("fallback audit log write failed", exc_info=True)


async def create_chat_model_with_fallback(
    agent: Agent,
    db: AsyncSession,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    **extra: object,
) -> BaseChatModel:
    """Build a chat model, walking ``agent.model_fallback_list`` on failure.

    The function attempts the primary ``agent.model`` first. If construction
    succeeds the result is returned immediately (we don't probe the model on
    every request — that's the health check's job). If construction raises a
    recoverable error, each fallback model id is tried in order and the
    first one to instantiate cleanly wins. Every attempt — primary plus
    each fallback — writes one ``fallback`` audit row when the agent has a
    bound credential. The ``api_key`` / ``base_url`` arguments are reused
    across the chain because the fallback list is "same key, different
    model".

    Backward compatible with :func:`create_chat_model`: when an agent has no
    fallback list the call is a thin wrapper around the primary path.
    """

    primary = agent.model
    if primary is None:
        raise ValueError("agent.model relationship not loaded")

    primary_provider = primary.provider
    primary_name = primary.model_name
    primary_base = base_url or getattr(primary, "base_url", None)

    fallback_ids: list[uuid.UUID] = []
    if agent.model_fallback_list:
        for raw in agent.model_fallback_list:
            try:
                fallback_ids.append(uuid.UUID(str(raw)))
            except (TypeError, ValueError):
                logger.warning("ignoring non-UUID fallback id: %r", raw)

    model_extra: dict[str, Any] = dict(extra)
    allow_env_fallback_raw = model_extra.pop("allow_env_fallback", True)
    if not isinstance(allow_env_fallback_raw, bool):
        raise TypeError("allow_env_fallback must be a bool")
    allow_env_fallback = allow_env_fallback_raw

    last_error: BaseException | None = None

    # 1) Primary attempt.
    try:
        model = model_attr("create_chat_model")(
            primary_provider,
            primary_name,
            api_key=api_key,
            base_url=primary_base,
            allow_env_fallback=allow_env_fallback,
            **model_extra,
        )
        if fallback_ids:
            await _audit_fallback_attempt(
                db,
                agent=agent,
                model_id=primary.id,
                provider=primary_provider,
                model_name=primary_name,
                error=None,
                success=True,
            )
        return model
    except Exception as exc:  # noqa: BLE001 — fall through to retries
        last_error = exc
        logger.info(
            "primary model %s/%s failed; attempting fallback chain (n=%d)",
            primary_provider,
            primary_name,
            len(fallback_ids),
        )
        await _audit_fallback_attempt(
            db,
            agent=agent,
            model_id=primary.id,
            provider=primary_provider,
            model_name=primary_name,
            error=exc,
            success=False,
        )
        if not fallback_ids or not _is_fallback_recoverable(exc):
            raise

    # 2) Walk fallbacks in order.
    for fallback_id in fallback_ids:
        resolved = await _resolve_model_for_fallback(db, fallback_id)
        if resolved is None:
            logger.warning("fallback model id missing: %s — skipping", fallback_id)
            continue
        provider, model_name, fb_base = resolved
        try:
            model = model_attr("create_chat_model")(
                provider,
                model_name,
                api_key=api_key,
                base_url=fb_base,
                allow_env_fallback=allow_env_fallback,
                **model_extra,
            )
            await _audit_fallback_attempt(
                db,
                agent=agent,
                model_id=fallback_id,
                provider=provider,
                model_name=model_name,
                error=None,
                success=True,
            )
            return model
        except Exception as exc:  # noqa: BLE001 — try next
            last_error = exc
            await _audit_fallback_attempt(
                db,
                agent=agent,
                model_id=fallback_id,
                provider=provider,
                model_name=model_name,
                error=exc,
                success=False,
            )
            if not _is_fallback_recoverable(exc):
                raise

    # 3) Everything failed — re-raise the most recent error.
    assert last_error is not None  # noqa: S101 — fallback-loop invariant (type narrowing)
    raise last_error
