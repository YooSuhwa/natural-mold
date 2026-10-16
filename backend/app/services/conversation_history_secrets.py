from __future__ import annotations

from collections.abc import Iterable, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.run_secrets import collect_secret_values, collect_url_userinfo
from app.credentials import service as credential_service
from app.credentials.external_secrets import EXTERNAL_MARKER
from app.credentials.service import PROVIDER_TO_DEFINITION_KEY
from app.mcp.client import build_env_vars, build_headers
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.model import Model
from app.schemas.conversation_history import JsonValue
from app.security.cipher import CipherError
from app.services.chat.runtime_context import _agent_runtime_load_options
from app.services.conversation_history_participants import (
    HistoryParticipantClosureError,
    HistoryParticipantRequest,
    load_history_participant_ids,
)


class HistorySecretSourceError(RuntimeError):
    """Current runtime secret material could not be collected completely."""


async def collect_strict_history_secrets(
    db: AsyncSession,
    agent_id: UUID,
    conversation_id: UUID,
) -> set[str]:
    """Collect current secrets for the retained runtime participant closure."""
    try:
        participant_ids = await load_history_participant_ids(
            HistoryParticipantRequest(db, agent_id, conversation_id)
        )
    except HistoryParticipantClosureError as error:
        raise HistorySecretSourceError("runtime participant closure is incomplete") from error
    agents = list(
        (
            await db.execute(
                select(Agent)
                .where(Agent.id.in_(participant_ids))
                .options(*_agent_runtime_load_options())
                .execution_options(populate_existing=True, autoflush=False)
            )
        )
        .unique()
        .scalars()
    )
    if {agent.id for agent in agents} != set(participant_ids):
        raise HistorySecretSourceError("participant Agent source is incomplete")

    secrets: set[str] = set()
    for agent in agents:
        await _collect_agent_secrets(db, agent, secrets)
    return secrets


async def _collect_agent_secrets(
    db: AsyncSession,
    agent: Agent,
    secrets: set[str],
) -> None:
    model = agent.model
    if model is None:
        raise HistorySecretSourceError("participant model source is incomplete")
    collect_url_userinfo(model.base_url, secrets)
    credential_ids: set[UUID] = set()
    if agent.llm_credential_id is not None:
        credential_ids.add(agent.llm_credential_id)
    if model.default_credential_id is not None:
        credential_ids.add(model.default_credential_id)
    if agent.llm_credential_id is None and model.default_credential_id is None:
        matched = await _provider_credential_id(db, agent)
        if matched is not None:
            credential_ids.add(matched)

    for link in agent.tool_links:
        tool = link.tool
        if tool.credential_id is not None:
            credential_ids.add(tool.credential_id)
        secrets.update(collect_secret_values(tool.parameters))
    for link in agent.mcp_tool_links:
        server = link.mcp_tool.server
        if server.credential_id is not None:
            credential_ids.add(server.credential_id)
        collect_url_userinfo(server.url, secrets)
        secrets.update(collect_secret_values(server.headers))
        _collect_authorization_components(server.headers, secrets)
        secrets.update(collect_secret_values(server.env_vars))
    for link in agent.skill_links:
        secrets.update(collect_secret_values(link.config))
        credential_ids.update(_skill_credential_ids(link.config))

    fallback_ids = _uuid_values(agent.model_fallback_list or ())
    if fallback_ids:
        fallback_models = list(await db.scalars(select(Model).where(Model.id.in_(fallback_ids))))
        if {row.id for row in fallback_models} != set(fallback_ids):
            raise HistorySecretSourceError("fallback model source is incomplete")
        for fallback in fallback_models:
            collect_url_userinfo(fallback.base_url, secrets)

    payloads = await _credential_payloads(db, credential_ids)
    for payload in payloads.values():
        secrets.update(collect_secret_values(payload))
    for link in agent.mcp_tool_links:
        server = link.mcp_tool.server
        payload = payloads.get(server.credential_id) if server.credential_id else None
        try:
            secrets.update(collect_secret_values(build_headers(server.headers, payload)))
            secrets.update(collect_secret_values(build_env_vars(server.env_vars, payload)))
        except (KeyError, TypeError, ValueError) as error:
            raise HistorySecretSourceError("MCP runtime secret source is incomplete") from error


async def _provider_credential_id(db: AsyncSession, agent: Agent) -> UUID | None:
    definition_key = PROVIDER_TO_DEFINITION_KEY.get(agent.model.provider)
    if definition_key is None:
        return None
    return await db.scalar(
        select(Credential.id)
        .where(
            Credential.user_id == agent.user_id,
            Credential.org_id == agent.org_id,
            Credential.tenant_id == agent.tenant_id,
            Credential.is_system.is_(False),
            Credential.definition_key == definition_key,
            Credential.status == "active",
        )
        .order_by(Credential.created_at.desc())
        .limit(1)
    )


async def _credential_payloads(
    db: AsyncSession,
    credential_ids: set[UUID],
) -> dict[UUID, dict[str, JsonValue]]:
    if not credential_ids:
        return {}
    credentials = list(
        await db.scalars(
            select(Credential).where(
                Credential.id.in_(credential_ids),
                Credential.status == "active",
            )
        )
    )
    if {credential.id for credential in credentials} != credential_ids:
        raise HistorySecretSourceError("credential source is incomplete")
    payloads: dict[UUID, dict[str, JsonValue]] = {}
    for credential in credentials:
        try:
            payload = await credential_service.decrypt_with_external(credential)
        except (CipherError, OSError, ValueError, RuntimeError, KeyError) as error:
            raise HistorySecretSourceError("credential source could not be resolved") from error
        if _contains_external_marker(payload):
            raise HistorySecretSourceError("external credential source could not be resolved")
        payloads[credential.id] = payload
    return payloads


def _contains_external_marker(value: JsonValue) -> bool:
    if isinstance(value, Mapping):
        if EXTERNAL_MARKER in value:
            return True
        return any(_contains_external_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_external_marker(item) for item in value)
    return False


def _collect_authorization_components(
    headers: Mapping[str, JsonValue] | None,
    secrets: set[str],
) -> None:
    if not headers:
        return
    for key, value in headers.items():
        if key.casefold() not in {"authorization", "proxy-authorization"}:
            continue
        if not isinstance(value, str):
            continue
        parts = value.split(maxsplit=1)
        if len(parts) == 2:
            secrets.update(collect_secret_values(parts[1]))


def _uuid_values(values: Iterable[str]) -> tuple[UUID, ...]:
    parsed: list[UUID] = []
    for value in values:
        try:
            parsed.append(UUID(value))
        except ValueError as error:
            raise HistorySecretSourceError("invalid referenced UUID") from error
    return tuple(parsed)


def _skill_credential_ids(config: Mapping[str, str] | None) -> set[UUID]:
    if not config:
        return set()
    bindings = config.get("credential_bindings")
    if not isinstance(bindings, Mapping):
        return set()
    ids: set[UUID] = set()
    for value in bindings.values():
        if not isinstance(value, str):
            raise HistorySecretSourceError("invalid skill credential binding")
        try:
            ids.add(UUID(value))
        except ValueError as error:
            raise HistorySecretSourceError("invalid skill credential binding") from error
    return ids


__all__ = ["HistorySecretSourceError", "collect_strict_history_secrets"]
