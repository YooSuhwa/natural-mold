"""The one outbound client for IdP calls (F-11 ``outbound_http_client``).

HTTPS only, no redirects, short timeout. The SSRF guard (public addresses only, TCP
pinned to the checked address) is on for every IdP host — issuer, discovery, token
and JWKS endpoints alike — except in ``single_tenant`` deployments; hosts listed in
``OUTBOUND_PRIVATE_ALLOWED_HOSTS`` (e.g. an internal Keycloak) are exempt inside the
guard. ``TRANSPORT`` is the test seam (mock IdP).
"""

import httpx

from app.config import settings
from app.outbound.client import outbound_http_client

TRANSPORT: httpx.AsyncBaseTransport | None = None
IDP_TIMEOUT = 10.0


def private_idps_allowed() -> bool:
    """Single-company installs reach their IdP on the internal network by design."""
    return settings.deployment_mode == "single_tenant"


def idp_client() -> httpx.AsyncClient:
    return outbound_http_client(
        timeout=IDP_TIMEOUT,
        https_only=True,
        ssrf_guard=not private_idps_allowed(),
        transport=TRANSPORT,
    )
