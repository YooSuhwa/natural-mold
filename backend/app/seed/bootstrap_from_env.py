"""Auto-create system Credentials from environment variables.

PoC-only convenience: when ``OPENAI_API_KEY`` and friends are present in the
environment, generate a Credential row encrypted with the active Cipher V2
key. Rows are stored as ``is_system=True, user_id=NULL`` so they belong to
the operator (not any single user) and only surface through the
super_user-gated ``/api/system-credentials`` router.

Idempotent — credentials with the marker name are not duplicated on
subsequent boots. This module never deletes or rotates anything; it only
inserts missing rows.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.credentials import service as credential_service
from app.models.credential import Credential

logger = logging.getLogger(__name__)

# Credentials inserted by this seed are tagged with this prefix so re-runs
# can skip them and operators can identify env-derived rows in the UI.
SEED_NAME_PREFIX = "[system]"


@dataclass(frozen=True)
class _EnvSpec:
    """Mapping from env vars to credential definition + payload keys."""

    definition_key: str
    name: str
    env_to_field: dict[str, str]


_ENV_SPECS: tuple[_EnvSpec, ...] = (
    _EnvSpec(
        definition_key="openai",
        name=f"{SEED_NAME_PREFIX} OpenAI",
        env_to_field={
            "OPENAI_API_KEY": "api_key",
        },
    ),
    _EnvSpec(
        definition_key="anthropic",
        name=f"{SEED_NAME_PREFIX} Anthropic",
        env_to_field={
            "ANTHROPIC_API_KEY": "api_key",
        },
    ),
    _EnvSpec(
        definition_key="google_search",
        name=f"{SEED_NAME_PREFIX} Google Search",
        env_to_field={
            "GOOGLE_API_KEY": "api_key",
            "GOOGLE_CSE_ID": "cse_id",
        },
    ),
    _EnvSpec(
        definition_key="naver_search",
        name=f"{SEED_NAME_PREFIX} Naver Search",
        env_to_field={
            "NAVER_CLIENT_ID": "client_id",
            "NAVER_CLIENT_SECRET": "client_secret",
        },
    ),
    _EnvSpec(
        definition_key="google_workspace_oauth2",
        name=f"{SEED_NAME_PREFIX} Google Workspace",
        env_to_field={
            "GOOGLE_OAUTH_CLIENT_ID": "client_id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "client_secret",
            "GOOGLE_OAUTH_REFRESH_TOKEN": "refresh_token",
        },
    ),
    _EnvSpec(
        definition_key="http_bearer",
        name=f"{SEED_NAME_PREFIX} Google Chat Webhook",
        env_to_field={
            "GOOGLE_CHAT_WEBHOOK_URL": "token",
        },
    ),
)


def _gather_env_payload(spec: _EnvSpec) -> dict[str, Any] | None:
    """Read env vars for a spec; return ``None`` if any required var is empty."""

    payload: dict[str, Any] = {}
    for env_var, field in spec.env_to_field.items():
        value = os.environ.get(env_var, "").strip()
        if not value:
            return None
        payload[field] = value
    return payload


async def bootstrap_system_credentials(db: AsyncSession) -> list[Credential]:
    """Create env-derived ``is_system=True`` credentials if missing. Idempotent.

    Skipped when ``settings.environment`` (if defined) is ``"production"`` so
    real deployments never auto-import their own env into the DB. The rows
    are written with ``user_id=NULL`` — they belong to the operator, not to
    any specific user account.
    """

    env_setting = getattr(settings, "environment", None)
    if env_setting == "production":
        logger.info("skip bootstrap_system_credentials: production environment")
        return []

    existing_rows = await db.execute(
        select(Credential.definition_key, Credential.name).where(
            Credential.is_system.is_(True)
        )
    )
    existing = {(row[0], row[1]) for row in existing_rows.all()}

    created: list[Credential] = []
    for spec in _ENV_SPECS:
        if (spec.definition_key, spec.name) in existing:
            continue
        payload = _gather_env_payload(spec)
        if payload is None:
            continue
        credential = await credential_service.create(
            db,
            user_id=None,
            definition_key=spec.definition_key,
            name=spec.name,
            data=payload,
            is_system=True,
            source="seed",
        )
        created.append(credential)
        logger.info(
            "bootstrap: created system credential %s (%s) from env",
            spec.name,
            spec.definition_key,
        )
    return created


__all__ = ["SEED_NAME_PREFIX", "bootstrap_system_credentials"]
