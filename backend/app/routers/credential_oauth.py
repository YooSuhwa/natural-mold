"""Credential OAuth2 routes (moved out of ``credentials``; mounted there).

- ``POST /api/oauth2-credential/auth/{id}``     start OAuth2 authorize flow
- ``GET  /api/oauth2-credential/callback``      OAuth2 callback handler (RFC 9207 ``iss``)
- ``GET  /.well-known/oauth-client/{deployment}`` Client ID Metadata Document (F-15)
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import PUBLIC, SELF, authz_policy
from app.credentials import oauth_service
from app.credentials.mcp_oauth_spec import client_metadata_document
from app.dependencies import get_db, get_org_context, verify_csrf
from app.schemas.credential import OAuth2AuthStartRequest, OAuth2AuthStartResponse

oauth_router = APIRouter(prefix="/api/oauth2-credential", tags=["credentials"])
well_known_router = APIRouter(prefix="/.well-known", tags=["credentials"])


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
@authz_policy(SELF)
async def oauth2_auth_start(
    credential_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    payload: OAuth2AuthStartRequest | None = Body(None),
) -> OAuth2AuthStartResponse:
    """Start the OAuth2 authorize flow (orchestration in ``oauth_service``)."""

    result = await oauth_service.start_oauth(
        db,
        context=context,
        credential_id=credential_id,
        fallback_redirect_uri=str(request.url_for("oauth2_callback")),
        return_to=request.headers.get("origin"),
        requested_scope=payload.scope if payload else None,
    )
    await db.commit()
    return OAuth2AuthStartResponse(
        authorization_url=result.authorization_url,
        state=result.state,
    )


@oauth_router.get("/callback", name="oauth2_callback")
@authz_policy(PUBLIC)
async def oauth2_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    iss: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Exchange ``code`` for tokens (orchestration in ``oauth_service``)."""

    try:
        result = await oauth_service.handle_callback(db, code=code, state=state, iss=iss)
    except oauth_service.OAuthResponseRejected:
        await db.commit()  # persist the consumed state, then answer the generic 400
        raise
    await db.commit()
    return _oauth_callback_html(result.credential_id, result.return_to)


@well_known_router.get("/oauth-client/{deployment}")
@authz_policy(PUBLIC)
async def oauth_client_metadata_document(deployment: str) -> dict[str, Any]:
    """Moldy's public OAuth Client ID Metadata Document (plan 11.4.1).

    Served only when ``mcp_oauth_client_metadata_base_url`` is configured.
    """

    document = client_metadata_document(deployment)
    if document is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return document
