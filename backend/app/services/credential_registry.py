"""Credential provider registry.

Provider별 필드 정의. 키 이름은 기존 tool builder들이 기대하는
auth_config 키와 동일하므로 별도 매핑이 불필요하다.
"""

from __future__ import annotations

from typing import Any

CREDENTIAL_PROVIDERS: dict[str, dict[str, Any]] = {
    "naver": {
        "name": "Naver Open API",
        "credential_type": "api_key",
        "fields": [
            {"key": "naver_client_id", "label": "Client ID", "secret": True},
            {
                "key": "naver_client_secret",
                "label": "Client Secret",
                "secret": True,
            },
        ],
    },
    "google_search": {
        "name": "Google Search API",
        "credential_type": "api_key",
        "fields": [
            {"key": "google_api_key", "label": "API Key", "secret": True},
            {
                "key": "google_cse_id",
                "label": "Search Engine ID",
                "secret": False,
            },
        ],
    },
    "google_workspace": {
        "name": "Google Workspace (OAuth2)",
        "credential_type": "oauth2",
        "fields": [
            {
                "key": "google_oauth_client_id",
                "label": "OAuth Client ID",
                "secret": True,
            },
            {
                "key": "google_oauth_client_secret",
                "label": "OAuth Client Secret",
                "secret": True,
            },
            {
                "key": "google_oauth_refresh_token",
                "label": "Refresh Token",
                "secret": True,
            },
        ],
    },
    "google_chat": {
        "name": "Google Chat Webhook",
        "credential_type": "api_key",
        "fields": [
            {"key": "webhook_url", "label": "Webhook URL", "secret": True},
        ],
    },
    "custom_api_key": {
        "name": "Custom API Key",
        "credential_type": "api_key",
        "fields": [
            {
                "key": "header_name",
                "label": "Header Name",
                "secret": False,
                "default": "Authorization",
            },
            {"key": "api_key", "label": "API Key", "secret": True},
        ],
    },
}
