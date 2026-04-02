"""Google OAuth2 credential helper for Workspace API access."""

from __future__ import annotations

from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from app.config import settings

# Gmail: read + send.  Calendar: read + write events.
_DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]


def get_google_credentials(
    auth_config: dict[str, Any] | None = None,
    scopes: list[str] | None = None,
) -> Credentials | None:
    """Build Google OAuth2 Credentials from config or auth_config.

    Priority: auth_config values > settings (.env) values.
    Returns None if required fields are missing.
    """
    cfg = auth_config or {}
    client_id = cfg.get("google_oauth_client_id") or settings.google_oauth_client_id
    client_secret = cfg.get("google_oauth_client_secret") or settings.google_oauth_client_secret
    refresh_token = cfg.get("google_oauth_refresh_token") or settings.google_oauth_refresh_token

    if not all([client_id, client_secret, refresh_token]):
        return None

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes or _DEFAULT_SCOPES,
    )

    # Refresh to get a valid access token
    if not creds.valid:
        creds.refresh(Request())

    return creds
