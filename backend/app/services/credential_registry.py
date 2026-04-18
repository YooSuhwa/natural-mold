"""Credential provider registry.

Provider별 필드 정의. 키 이름(`key`)은 기존 tool builder들이 기대하는
auth_config 키와 동일하므로 별도 매핑이 불필요하다.

`env_field` (optional): 해당 필드 값을 `app.config.settings` 의 어떤 속성에서
bootstrap 시드할지. lifespan의 `seed_mock_user_prebuilt_connections`가 이 매핑을
사용해 env → credential data 변환을 수행한다. 대부분 `key == env_field`지만
google_chat처럼 다른 경우도 있다 (`settings.google_chat_webhook_url` → data
key `webhook_url`). env_field가 없는 필드는 seed 대상이 아님 (custom_api_key 등).
"""

from __future__ import annotations

from typing import Any

CREDENTIAL_PROVIDERS: dict[str, dict[str, Any]] = {
    "naver": {
        "name": "Naver Open API",
        "credential_type": "api_key",
        "fields": [
            {
                "key": "naver_client_id",
                "label": "Client ID",
                "secret": True,
                "env_field": "naver_client_id",
            },
            {
                "key": "naver_client_secret",
                "label": "Client Secret",
                "secret": True,
                "env_field": "naver_client_secret",
            },
        ],
    },
    "google_search": {
        "name": "Google Search API",
        "credential_type": "api_key",
        "fields": [
            {
                "key": "google_api_key",
                "label": "API Key",
                "secret": True,
                "env_field": "google_api_key",
            },
            {
                "key": "google_cse_id",
                "label": "Search Engine ID",
                "secret": False,
                "env_field": "google_cse_id",
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
                "env_field": "google_oauth_client_id",
            },
            {
                "key": "google_oauth_client_secret",
                "label": "OAuth Client Secret",
                "secret": True,
                "env_field": "google_oauth_client_secret",
            },
            {
                "key": "google_oauth_refresh_token",
                "label": "Refresh Token",
                "secret": True,
                "env_field": "google_oauth_refresh_token",
            },
        ],
    },
    "google_chat": {
        "name": "Google Chat Webhook",
        "credential_type": "api_key",
        "fields": [
            {
                "key": "webhook_url",
                "label": "Webhook URL",
                "secret": True,
                # env 소스 이름은 settings.google_chat_webhook_url 이지만
                # tool builder가 lookup하는 data key는 "webhook_url" 이다.
                "env_field": "google_chat_webhook_url",
            },
        ],
    },
    "custom_api_key": {
        "name": "Custom API Key",
        "credential_type": "api_key",
        # 사용자 입력 기반이므로 env 시드 대상 아님 (env_field 없음).
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
