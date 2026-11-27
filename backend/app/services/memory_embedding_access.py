"""Authorize the embedding model and its service credential for one memory's organization."""

from __future__ import annotations

from uuid import UUID

import httpx
from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.catalog_fanout import company_allows
from app.authz.company_catalog import CompanyTier, company_tier, model_scope, stored_policy
from app.authz.org_catalog_queries import CatalogEntry
from app.credentials.allowed_hosts import default_allowed_hosts
from app.models.credential import Credential
from app.models.model import Model
from app.models.organization import Organization, Tenant
from app.outbound.destination import CredentialDestination, OutboundBlockedError, check_destination
from app.outbound.hosts import host_allowed
from app.services import memory_embedder, memory_vectors
from app.services.system_credential_resolver import open_model_key


async def _org_model(
    db: AsyncSession, tenant_id: UUID, org_id: UUID, model_id: UUID, *, lock: bool = False
) -> tuple[Tenant, Model, CompanyTier] | None:
    # A worker can process several organizations in a tenant session. Read current policy
    # from the actual record's organization on every resolution, including after a commit.
    query = (
        select(Tenant, Organization, Model)
        .join_from(Tenant, Organization, Organization.tenant_id == Tenant.id)
        .join_from(Tenant, Model, and_(Model.id == model_id, model_scope(tenant_id)))
        .where(
            Tenant.id == tenant_id,
            Tenant.status == "active",
            Organization.id == org_id,
            Organization.status == "active",
            Model.kind == "embedding",
            Model.is_visible.is_(True),
            Model.provider.in_(memory_embedder.OPENAI_SHAPED),
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        # Publication retries a concurrent catalog/lifecycle change; those writers may
        # acquire tenant and organization rows in different orders.
        query = query.with_for_update(read=True, nowait=True)
    row = (await db.execute(query)).one_or_none()
    if row is None:
        return None
    tenant, organization, model = row
    tier = company_tier(tenant)
    entry = CatalogEntry(
        "model", model.id, model.display_name, "company" if model.tenant_id else "platform"
    )
    if company_allows(tier, entry, org_id) and stored_policy(organization.settings).enabled_for(
        "model", model.id
    ):
        return tenant, model, tier
    return None


async def resolve_org_model(
    db: AsyncSession, tenant_id: UUID, org_id: UUID, model_id: UUID
) -> Model | None:
    """Discover a usable embedding registration without selecting or opening a credential."""
    result = await _org_model(db, tenant_id, org_id, model_id)
    return result[1] if result is not None else None


async def _credential(
    db: AsyncSession, model: Model, tier: CompanyTier, tenant_id: UUID, org_id: UUID, *, lock: bool
) -> Credential | None:
    query = select(Credential).where(
        Credential.is_system.is_(True),
        Credential.user_id.is_(None),
        Credential.org_id.is_(None),
        Credential.status == "active",
        Credential.definition_key == model.provider,
    )
    platform = (Credential.scope == "platform") & Credential.tenant_id.is_(None)
    if model.default_credential_id is None:
        if not tier.allow_system_models:
            return None
        query = query.where(platform).order_by(Credential.created_at.desc(), Credential.id).limit(1)
    else:
        company = (Credential.scope == "tenant") & (Credential.tenant_id == tenant_id)
        query = query.where(
            Credential.id == model.default_credential_id,
            (company | platform) if tier.allow_system_models else company,
        )
    if lock:
        query = query.with_for_update(read=True, nowait=True)
    row = await db.scalar(query.execution_options(populate_existing=True))
    if (
        row is not None
        and row.tenant_id is not None
        and not tier.credential_reaches(row.id, org_id)
    ):
        return None
    return row


def _allows_destination(
    credential: Credential, endpoint: str, key_url: str | None, *, sends_key: bool
) -> bool:
    # A tenant model may use a platform key, but only at that key's intended provider.
    # For generic gateways the operator selected the endpoint in the credential itself.
    try:
        hosts = tuple(credential.allowed_hosts or ())
        if key_url:
            if httpx.URL(key_url.rstrip("/")) != httpx.URL(endpoint.rstrip("/")):
                return False
            if not hosts:
                hosts = (httpx.URL(key_url).host,)
        elif not hosts:
            hosts = tuple(default_allowed_hosts(credential.definition_key) or ())
        if not hosts:
            return False
        if sends_key:
            check_destination(endpoint, CredentialDestination(hosts, empty_ok=False))
        else:
            url = httpx.URL(endpoint)
            return url.scheme in {"http", "https"} and host_allowed(url.host, hosts)
    except (httpx.InvalidURL, OutboundBlockedError, ValueError):
        return False
    return True


async def org_embedder_for(
    db: AsyncSession,
    tenant_id: UUID,
    org_id: UUID,
    model_id: UUID,
    *,
    generation: int = 0,
    lock: bool = False,
) -> memory_embedder.OpenAIShapedEmbedder | None:
    """Resolve the selected generation in its active catalog and credential boundary."""
    resolved = await _org_model(db, tenant_id, org_id, model_id, lock=lock)
    if resolved is None:
        return None
    tenant, model, tier = resolved
    if (
        memory_vectors.chosen_model_id(tenant.settings) != model.id
        or memory_vectors.generation(tenant.settings) != generation
    ):
        return None
    credential = await _credential(db, model, tier, tenant_id, org_id, lock=lock)
    compatible = model.provider in {"openai_compatible", "litellm"}
    keyless = model.default_credential_id is None and compatible and bool(model.base_url)
    if credential is not None:
        try:
            api_key, key_url = await open_model_key(
                db, credential_id=credential.id, provider=model.provider, tenant=tenant
            )
        except SQLAlchemyError:
            raise
        except Exception:  # noqa: BLE001 - an unreadable service key disables this org's embedding
            return None
        if api_key or (key_url and compatible):
            embedder = memory_vectors.bind_embedder(
                tenant, model, api_key=api_key, key_base_url=key_url, org_id=org_id
            )
            if embedder is not None and _allows_destination(
                credential, embedder.base_url, key_url, sends_key=bool(api_key)
            ):
                return embedder
    # A different platform gateway's optional fallback key must never be sent here.
    # An unbound, explicitly registered compatible server can still accept a keyless call.
    if keyless:
        return memory_vectors.bind_embedder(
            tenant, model, api_key=None, key_base_url=None, org_id=org_id
        )
    return None
