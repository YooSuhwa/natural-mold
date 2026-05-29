"""Shared TLS verification context for outbound HTTP clients."""

from __future__ import annotations

import os
import ssl
from functools import lru_cache

import certifi


def _existing_cert_paths() -> list[str]:
    paths: list[str] = []
    for env_key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        path = os.environ.get(env_key)
        if path and os.path.exists(path) and path not in paths:
            paths.append(path)

    hc_cert = os.path.expanduser("~/.ssl/HC_SSL.pem")
    if os.path.exists(hc_cert) and hc_cert not in paths:
        paths.append(hc_cert)
    return paths


@lru_cache(maxsize=1)
def get_outbound_ssl_context() -> ssl.SSLContext:
    """Return the app-wide outbound SSL context.

    httpx defaults to certifi, which can miss corporate/VPN roots on macOS.
    truststore delegates verification to the OS trust store and we layer any
    explicit PEM bundle used by the app on top.
    """

    try:
        import truststore

        ctx: ssl.SSLContext = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:  # pragma: no cover - dependency is installed in app env
        ctx = ssl.create_default_context(cafile=certifi.where())

    for path in _existing_cert_paths():
        ctx.load_verify_locations(path)
    return ctx


__all__ = ["get_outbound_ssl_context"]
