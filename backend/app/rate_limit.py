"""IP-based rate limiter for public, auth-free endpoints.

The only callers today are the public share routes — they accept arbitrary
tokens and walk LangGraph state, so unauthenticated abuse can drive load.
``slowapi`` gives us per-IP throttling with a standard ``X-Forwarded-For``
fallback (``get_remote_address`` reads ``request.client.host`` and the
forwarded header chain).

Tests flip ``settings.rate_limit_enabled`` off via the conftest so route
behavior assertions stay deterministic.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    enabled=settings.rate_limit_enabled,
)
