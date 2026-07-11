"""Credential API — greenfield rewrite.

Endpoints:
- ``GET    /api/credential-types``                  catalog of definitions
- ``GET    /api/credentials``                       list user's credentials
- ``POST   /api/credentials``                       create
- ``GET    /api/credentials/{id}``                  detail (no decrypted data)
- ``PATCH  /api/credentials/{id}``                  update
- ``DELETE /api/credentials/{id}``                  delete
- ``POST   /api/credentials/{id}/test``             run test recipe on stored data
- ``POST   /api/credentials/preview-test``          run test on raw form data
- ``GET    /api/credentials/{id}/audit-logs``       recent audit log entries
- ``POST   /api/oauth2-credential/auth/{id}``       start OAuth2 authorize flow
- ``GET    /api/oauth2-credential/callback``        OAuth2 callback handler
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.model_factory import sync_env_fallback_from_credentials
from app.credentials import oauth_service
from app.credentials import service as credential_service
from app.credentials.registry import registry
from app.credentials.tester import CredentialTester
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    require_super_user,
    verify_csrf,
)
from app.error_codes import (
    credential_not_found,
    system_credential_not_found,
    unknown_credential_definition,
)
from app.models.credential import Credential
from app.schemas.credential import (
    CredentialAuditLogResponse,
    CredentialCreate,
    CredentialDefinitionSchema,
    CredentialResponse,
    CredentialTestResponse,
    CredentialUpdate,
    OAuth2AuthStartResponse,
    PreviewTestRequest,
)
from app.schemas.model import DiscoveredModelSchema
from app.services import model_discovery

router = APIRouter(tags=["credentials"])

# Operator-managed system credentials (Fix Agent / builder / image gen).
# Same Cipher / definition machinery as user credentials but stored with
# ``is_system=True`` so user-facing pickers never surface them.
system_router = APIRouter(prefix="/api/system-credentials", tags=["credentials"])

# Catalog mounts at /api/credential-types; CRUD lives under /api/credentials.
catalog_router = APIRouter(prefix="/api/credential-types", tags=["credentials"])
crud_router = APIRouter(prefix="/api/credentials", tags=["credentials"])
oauth_router = APIRouter(prefix="/api/oauth2-credential", tags=["credentials"])


# -- Helpers -----------------------------------------------------------------


def _to_response(cred: Credential) -> CredentialResponse:
    return CredentialResponse(
        id=cred.id,
        user_id=cred.user_id,
        definition_key=cred.definition_key,
        name=cred.name,
        field_keys=cred.field_keys or [],
        is_shared=cred.is_shared,
        is_system=cred.is_system,
        status=cred.status,
        key_id=cred.key_id,
        last_used_at=cred.last_used_at,
        last_tested_at=cred.last_tested_at,
        last_test_result=cred.last_test_result,
        created_at=cred.created_at,
        updated_at=cred.updated_at,
    )


def _request_meta(request: Request) -> tuple[str | None, str | None]:
    client = request.client.host if request.client else None
    return client, request.headers.get("user-agent")


async def _load_owned(db: AsyncSession, credential_id: uuid.UUID, user_id: uuid.UUID) -> Credential:
    cred = await credential_service.get_for_user(db, credential_id, user_id)
    if cred is None:
        raise credential_not_found()
    return cred


async def _maybe_sync_env_fallback(db: AsyncSession, definition_key: str) -> None:
    """ADR-013 invalidate hook — refresh ``_ENV_FALLBACK`` after CRUD.

    Only fires for LLM provider definitions (anthropic/openai/google_genai/
    openrouter). Other providers don't participate in builder/assistant key
    resolution, so the dict stays untouched.
    """

    if not credential_service.is_llm_definition(definition_key):
        return
    await sync_env_fallback_from_credentials(db)


# -- Catalog -----------------------------------------------------------------


@catalog_router.get("", response_model=list[CredentialDefinitionSchema])
async def list_credential_types() -> list[CredentialDefinitionSchema]:
    return [CredentialDefinitionSchema(**d.serialize()) for d in registry.all()]


@catalog_router.get("/{key}", response_model=CredentialDefinitionSchema)
async def get_credential_type(key: str) -> CredentialDefinitionSchema:
    definition = registry.get(key)
    if definition is None:
        raise unknown_credential_definition(key)
    return CredentialDefinitionSchema(**definition.serialize())


# -- CRUD --------------------------------------------------------------------


@crud_router.get("", response_model=list[CredentialResponse])
async def list_credentials(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[CredentialResponse]:
    creds = await credential_service.list_for_user(db, user.id)
    return [_to_response(c) for c in creds]


@crud_router.post("", response_model=CredentialResponse, status_code=201)
async def create_credential(
    payload: CredentialCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> CredentialResponse:
    if registry.get(payload.definition_key) is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown definition '{payload.definition_key}'",
        )
    name = payload.normalized_name()
    cred = await credential_service.create(
        db,
        user_id=user.id,
        definition_key=payload.definition_key,
        name=name,
        data=payload.data,
        is_shared=payload.is_shared,
    )
    await db.commit()
    await db.refresh(cred)
    await _maybe_sync_env_fallback(db, payload.definition_key)
    return _to_response(cred)


@crud_router.get("/{credential_id}", response_model=CredentialResponse)
async def get_credential(
    credential_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> CredentialResponse:
    cred = await _load_owned(db, credential_id, user.id)
    return _to_response(cred)


@crud_router.patch("/{credential_id}", response_model=CredentialResponse)
async def update_credential(
    credential_id: uuid.UUID,
    payload: CredentialUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> CredentialResponse:
    cred = await _load_owned(db, credential_id, user.id)
    if payload.name is not None:
        # Preserve marker reservation enforced by markers.check_reserved_marker
        from app.schemas.markers import check_reserved_marker

        check_reserved_marker(payload.name, "name")
    await credential_service.update(
        db,
        credential=cred,
        actor_user_id=user.id,
        name=payload.name,
        data=payload.data,
        is_shared=payload.is_shared,
        status=payload.status,
    )
    await db.commit()
    await db.refresh(cred)
    await _maybe_sync_env_fallback(db, cred.definition_key)
    return _to_response(cred)


@crud_router.delete("/{credential_id}", status_code=204)
async def delete_credential(
    credential_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> None:
    cred = await _load_owned(db, credential_id, user.id)
    definition_key = cred.definition_key
    await credential_service.write_audit_log(
        db,
        credential_id=cred.id,
        actor_user_id=user.id,
        action="delete",
    )
    await db.delete(cred)
    await db.commit()
    await _maybe_sync_env_fallback(db, definition_key)


# -- System credentials (operator-managed) ----------------------------------


async def _load_system(db: AsyncSession, credential_id: uuid.UUID) -> Credential:
    cred = await credential_service.get_system(db, credential_id)
    if cred is None:
        raise system_credential_not_found()
    return cred


@system_router.get("", response_model=list[CredentialResponse])
async def list_system_credentials(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_super_user),
) -> list[CredentialResponse]:
    """List all operator-managed system credentials. Super_user only."""

    creds = await credential_service.list_system(db)
    return [_to_response(c) for c in creds]


@system_router.post("", response_model=CredentialResponse, status_code=201)
async def create_system_credential(
    payload: CredentialCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_super_user),
    _csrf: None = Depends(verify_csrf),
) -> CredentialResponse:
    if registry.get(payload.definition_key) is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown definition '{payload.definition_key}'",
        )
    # System credentials are operator-owned (user_id=NULL) so they survive
    # any individual user's lifecycle and never leak through the user-scoped
    # picker queries (``list_for_user`` filters ``is_system=False``).
    cred = await credential_service.create(
        db,
        user_id=None,
        definition_key=payload.definition_key,
        name=payload.normalized_name(),
        data=payload.data,
        is_shared=False,
        is_system=True,
    )
    await db.commit()
    await db.refresh(cred)
    await _maybe_sync_env_fallback(db, payload.definition_key)
    return _to_response(cred)


@system_router.get("/{credential_id}", response_model=CredentialResponse)
async def get_system_credential(
    credential_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_super_user),
) -> CredentialResponse:
    return _to_response(await _load_system(db, credential_id))


@system_router.patch("/{credential_id}", response_model=CredentialResponse)
async def update_system_credential(
    credential_id: uuid.UUID,
    payload: CredentialUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_super_user),
    _csrf: None = Depends(verify_csrf),
) -> CredentialResponse:
    cred = await _load_system(db, credential_id)
    if payload.name is not None:
        from app.schemas.markers import check_reserved_marker

        check_reserved_marker(payload.name, "name")
    await credential_service.update(
        db,
        credential=cred,
        actor_user_id=user.id,
        name=payload.name,
        data=payload.data,
        status=payload.status,
    )
    await db.commit()
    await db.refresh(cred)
    await _maybe_sync_env_fallback(db, cred.definition_key)
    return _to_response(cred)


@system_router.delete("/{credential_id}", status_code=204)
async def delete_system_credential(
    credential_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_super_user),
    _csrf: None = Depends(verify_csrf),
) -> None:
    cred = await _load_system(db, credential_id)
    definition_key = cred.definition_key
    await credential_service.write_audit_log(
        db,
        credential_id=cred.id,
        actor_user_id=user.id,
        action="delete",
    )
    await db.delete(cred)
    await db.commit()
    await _maybe_sync_env_fallback(db, definition_key)


# -- Test --------------------------------------------------------------------


@crud_router.post("/{credential_id}/test", response_model=CredentialTestResponse)
async def test_credential(
    credential_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> CredentialTestResponse:
    cred = await _load_owned(db, credential_id, user.id)
    definition = registry.get(cred.definition_key)
    if definition is None:
        raise HTTPException(
            status_code=400,
            detail=f"definition '{cred.definition_key}' is not registered",
        )
    data = await credential_service.decrypt_with_external(cred.data_encrypted)
    result = await CredentialTester().run(definition, data)
    payload = result.to_dict()
    await credential_service.record_test(
        db,
        credential=cred,
        actor_user_id=user.id,
        result=payload,
    )
    await db.commit()
    return CredentialTestResponse(**payload)


@crud_router.post("/preview-test", response_model=CredentialTestResponse)
async def preview_test(
    payload: PreviewTestRequest,
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> CredentialTestResponse:
    definition = registry.get(payload.definition_key)
    if definition is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown definition '{payload.definition_key}'",
        )
    result = await CredentialTester().run(definition, payload.data)
    return CredentialTestResponse(**result.to_dict())


# -- Model discovery ---------------------------------------------------------


@crud_router.post(
    "/{credential_id}/discover-models",
    response_model=list[DiscoveredModelSchema],
)
async def discover_models(
    credential_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> list[DiscoveredModelSchema]:
    """List models reachable through this Credential.

    Per-provider dispatch lives in ``app.services.model_discovery``. Failures
    bubble up as a 502 with the provider message — credential ``test`` is the
    canonical 'is this key valid' surface, not this endpoint.
    """

    cred = await credential_service.get_for_user(db, credential_id, user.id)
    if cred is None and user.is_super_user:
        # System LLM settings (ADR-019) discovers models from is_system
        # credentials, which _load_owned (user-scoped) can't see. super_user
        # owns system credentials, so allow discovery against them here.
        cred = await credential_service.get_system(db, credential_id)
    if cred is None:
        raise credential_not_found()
    try:
        results = await model_discovery.discover_from_credential(db, cred)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — opaque external errors
        raise HTTPException(
            status_code=502,
            detail=f"model discovery failed: {exc}",
        ) from exc

    ip, user_agent = _request_meta(request)
    await credential_service.write_audit_log(
        db,
        credential_id=cred.id,
        actor_user_id=user.id,
        action="discover",
        source="api",
        ip=ip,
        user_agent=user_agent,
        metadata={"count": len(results)},
    )
    await db.commit()
    return [DiscoveredModelSchema(**m.to_dict()) for m in results]


# -- Audit log ---------------------------------------------------------------


@crud_router.get(
    "/{credential_id}/audit-logs",
    response_model=list[CredentialAuditLogResponse],
)
async def list_audit_logs(
    credential_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[CredentialAuditLogResponse]:
    await _load_owned(db, credential_id, user.id)
    logs = await credential_service.list_audit_logs(db, credential_id=credential_id, limit=limit)
    return [
        CredentialAuditLogResponse(
            id=log.id,
            credential_id=log.credential_id,
            actor_user_id=log.actor_user_id,
            action=log.action,
            source=log.source,
            ip=log.ip,
            user_agent=log.user_agent,
            error=log.error,
            log_metadata=log.log_metadata,
            created_at=log.created_at,
        )
        for log in logs
    ]


# -- OAuth2 ------------------------------------------------------------------


def _oauth_callback_html(credential_id: uuid.UUID, target_origin: str | None) -> HTMLResponse:
    safe_target = target_origin or "*"
    body = f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>Moldy OAuth Complete</title></head>
  <body>
    <p>OAuth authorization completed. You can close this window.</p>
    <script>
      if (window.opener) {{
        window.opener.postMessage(
          {{ type: 'moldy.oauth.completed', credentialId: '{credential_id}' }},
          {safe_target!r}
        );
      }}
      window.close();
    </script>
  </body>
</html>"""
    return HTMLResponse(content=body)


@oauth_router.post("/auth/{credential_id}", response_model=OAuth2AuthStartResponse)
async def oauth2_auth_start(
    credential_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> OAuth2AuthStartResponse:
    """Start the OAuth2 authorize flow (orchestration in ``oauth_service``)."""

    result = await oauth_service.start_oauth(
        db,
        user=user,
        credential_id=credential_id,
        fallback_redirect_uri=str(request.url_for("oauth2_callback")),
        return_to=request.headers.get("origin"),
    )
    await db.commit()
    return OAuth2AuthStartResponse(
        authorization_url=result.authorization_url,
        state=result.state,
    )


@oauth_router.get("/callback", name="oauth2_callback")
async def oauth2_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Exchange ``code`` for tokens (orchestration in ``oauth_service``)."""

    result = await oauth_service.handle_callback(db, code=code, state=state)
    await db.commit()
    return _oauth_callback_html(result.credential_id, result.return_to)


# -- Composition -------------------------------------------------------------

router.include_router(catalog_router)
router.include_router(crud_router)
router.include_router(oauth_router)
router.include_router(system_router)
