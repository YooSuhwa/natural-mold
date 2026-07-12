"""Agent-blueprint-type install / overwrite logic — BE-S3 split of
``install_service``. Function bodies are moved verbatim; only import
plumbing changed.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.error_codes import (
    marketplace_credential_required,
    marketplace_invalid_package,
    marketplace_item_not_found,
)
from app.marketplace.install.bindings import (
    _agent_blueprint_payload_with_requirements,
    _credential_requirements_by_key,
    _required_credential_keys,
    _validate_version_credential_bindings,
)
from app.marketplace.install.common import _derive_origin, _existing_installation, _now
from app.marketplace.payloads import canonical_json_hash
from app.marketplace.schemas import InstallMarketplaceItemIn
from app.models.agent_blueprint import AgentBlueprint
from app.models.credential import Credential
from app.models.marketplace import (
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplaceVersion,
)

if TYPE_CHECKING:
    from app.dependencies import CurrentUser


async def _install_agent_blueprint_item(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    version: MarketplaceVersion,
    user: CurrentUser,
    body: InstallMarketplaceItemIn,
) -> MarketplaceInstallation:
    if version.payload_kind != "agent_spec":
        raise marketplace_invalid_package("version is not an Agent spec")

    existing = await _existing_installation(db, item=item, user=user)
    if existing is not None and body.install_mode == "reuse_or_update":
        if not body.credential_bindings:
            return existing
        blueprint_id = existing.installed_agent_blueprint_id
        if blueprint_id is None:
            raise marketplace_invalid_package("installed Agent Blueprint is missing")
        blueprint = await db.get(AgentBlueprint, blueprint_id)
        # Re-validate ownership before mutating (mirror
        # ``_overwrite_agent_blueprint_installation``) — collapse to 404
        # per the enumeration-safety convention.
        if blueprint is None or blueprint.user_id != user.id:
            raise marketplace_item_not_found()
        installed_version = await db.get(MarketplaceVersion, existing.version_id)
        binding_version = installed_version or version
        credential_bindings = await _validate_version_credential_bindings(
            db,
            version=binding_version,
            user=user,
            bindings=body.credential_bindings,
        )
        merged_bindings = {
            **(blueprint.credential_bindings or {}),
            **credential_bindings,
        }
        required = _required_credential_keys(binding_version)
        missing = [key for key in required if key not in merged_bindings]
        if missing and body.install_missing_credentials == "reject":
            raise marketplace_credential_required(
                f"missing required credential bindings: {', '.join(missing)}"
            )
        install_status = "needs_setup" if missing else "active"
        blueprint.credential_bindings = merged_bindings
        blueprint.install_status = install_status
        blueprint.is_dirty = False
        blueprint.updated_at = _now()
        existing.install_status = install_status
        existing.is_dirty = False
        existing.updated_at = _now()
        await db.flush()
        return existing

    credential_bindings = await _validate_version_credential_bindings(
        db,
        version=version,
        user=user,
        bindings=body.credential_bindings,
    )
    required = _required_credential_keys(version)
    missing = [key for key in required if key not in credential_bindings]
    if missing and body.install_missing_credentials == "reject":
        raise marketplace_credential_required(
            f"missing required credential bindings: {', '.join(missing)}"
        )
    install_status = "needs_setup" if missing else "active"

    payload = _agent_blueprint_payload_with_requirements(version)
    agent_spec = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
    name = body.name_override or agent_spec.get("name") or payload.get("name") or item.name

    if (
        existing is not None
        and body.install_mode == "overwrite_existing"
        and existing.installed_agent_blueprint_id is not None
    ):
        blueprint = await db.get(AgentBlueprint, existing.installed_agent_blueprint_id)
        # Re-validate ownership before mutating (mirror
        # ``_overwrite_agent_blueprint_installation``) — collapse to 404
        # per the enumeration-safety convention.
        if blueprint is None or blueprint.user_id != user.id:
            raise marketplace_item_not_found()
        blueprint.name = str(name)
        blueprint.description = item.description
        blueprint.spec = payload
        blueprint.spec_hash = version.content_hash or canonical_json_hash(payload)
        blueprint.credential_bindings = credential_bindings
        blueprint.source_marketplace_item_id = item.id
        blueprint.source_marketplace_version_id = version.id
        blueprint.origin_user_id = item.owner_user_id
        blueprint.install_status = install_status
        blueprint.is_dirty = False
        blueprint.updated_at = _now()
        existing.version_id = version.id
        existing.install_status = install_status
        existing.is_dirty = False
        existing.updated_at = _now()
        await db.flush()
        return existing

    blueprint = AgentBlueprint(
        id=uuid.uuid4(),
        user_id=user.id,
        name=str(name),
        description=item.description,
        icon_id=item.icon_id,
        tags=list(item.tags or []),
        categories=list(item.categories or []),
        spec=payload,
        spec_hash=version.content_hash or canonical_json_hash(payload),
        credential_bindings=credential_bindings,
        source_marketplace_item_id=item.id,
        source_marketplace_version_id=version.id,
        origin_user_id=item.owner_user_id,
        origin_kind=_derive_origin(item, user)[0],
        install_status=install_status,
        is_dirty=False,
        created_agent_count=0,
    )
    db.add(blueprint)
    await db.flush()

    installation = MarketplaceInstallation(
        id=uuid.uuid4(),
        user_id=user.id,
        item_id=item.id,
        version_id=version.id,
        resource_type="agent",
        installed_agent_blueprint_id=blueprint.id,
        install_status=install_status,
        is_dirty=False,
        installed_at=_now(),
    )
    db.add(installation)
    await db.flush()
    return installation


async def _agent_blueprint_status_from_bindings(
    db: AsyncSession,
    *,
    version: MarketplaceVersion,
    user: CurrentUser,
    stored_bindings: dict[str, Any] | None,
) -> tuple[str, dict[str, str]]:
    requirement_by_key = _credential_requirements_by_key(version)
    normalized: dict[str, str] = {}
    for key, raw_id in (stored_bindings or {}).items():
        requirement = requirement_by_key.get(str(key))
        if requirement is None:
            continue
        try:
            credential_id = uuid.UUID(str(raw_id))
        except (TypeError, ValueError):
            continue
        credential = await db.get(Credential, credential_id)
        expected_definition = requirement.get("definition_key")
        if (
            credential is None
            or credential.user_id != user.id
            or (expected_definition and credential.definition_key != expected_definition)
        ):
            continue
        normalized[str(key)] = str(credential.id)

    missing = [key for key in _required_credential_keys(version) if key not in normalized]
    return ("needs_setup" if missing else "active", normalized)


def _apply_agent_payload_to_blueprint(
    *,
    blueprint: AgentBlueprint,
    item: MarketplaceItem,
    version: MarketplaceVersion,
    name: str,
    payload: dict[str, Any],
    credential_bindings: dict[str, str],
    install_status: str,
) -> None:
    blueprint.name = name
    blueprint.description = item.description
    blueprint.icon_id = item.icon_id
    blueprint.tags = list(item.tags or [])
    blueprint.categories = list(item.categories or [])
    blueprint.spec = payload
    blueprint.spec_hash = version.content_hash or canonical_json_hash(payload)
    blueprint.credential_bindings = credential_bindings
    blueprint.source_marketplace_item_id = item.id
    blueprint.source_marketplace_version_id = version.id
    blueprint.origin_user_id = item.owner_user_id
    blueprint.install_status = install_status
    blueprint.is_dirty = False
    blueprint.updated_at = _now()


async def _overwrite_agent_blueprint_installation(
    db: AsyncSession,
    *,
    installation: MarketplaceInstallation,
    item: MarketplaceItem,
    latest: MarketplaceVersion,
    user: CurrentUser,
) -> MarketplaceInstallation:
    if latest.payload_kind != "agent_spec":
        raise marketplace_invalid_package("latest version is not an Agent spec")
    if installation.installed_agent_blueprint_id is None:
        raise marketplace_item_not_found()

    blueprint = await db.get(AgentBlueprint, installation.installed_agent_blueprint_id)
    if blueprint is None or blueprint.user_id != user.id:
        raise marketplace_item_not_found()

    payload = _agent_blueprint_payload_with_requirements(latest)
    agent_spec = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
    name = agent_spec.get("name") or payload.get("name") or item.name
    install_status, credential_bindings = await _agent_blueprint_status_from_bindings(
        db,
        version=latest,
        user=user,
        stored_bindings=blueprint.credential_bindings,
    )
    _apply_agent_payload_to_blueprint(
        blueprint=blueprint,
        item=item,
        version=latest,
        name=str(name),
        payload=payload,
        credential_bindings=credential_bindings,
        install_status=install_status,
    )
    installation.version_id = latest.id
    installation.install_status = install_status
    installation.is_dirty = False
    installation.updated_at = _now()
    return installation
