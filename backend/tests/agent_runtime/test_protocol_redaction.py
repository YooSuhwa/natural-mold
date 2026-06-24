"""Regression tests for protocol trace redaction.

Guards two confirmed flaws in the original ``protocol_redaction``:

1. Over-redaction — substring key matching scrubbed debug-critical keys
   (``session_id``, ``token_count``, ``possession`` ...). Now keys are
   matched on word boundaries and usage metrics are explicitly safe.
2. Leak paths — secrets only redacted when a sensitive key sat directly
   before the value. A value-based masking layer now catches bare
   ``Bearer`` tokens, JWTs, ``sk-`` keys, ``moldy_at=`` cookies and URL
   userinfo credentials regardless of the surrounding key.
"""

from __future__ import annotations

import json

import pytest

from app.agent_runtime.protocol_redaction import (
    REDACTED_SENSITIVE_FIELD,
    _is_sensitive_protocol_key,
    redact_protocol_data,
)

# --- 1. Over-redaction: these keys MUST stay visible -----------------------

SAFE_KEYS = [
    "session_id",
    "session_count",
    "token_count",
    "tokens_used",
    "possession",
    "my_secretary",
    "cookies_enabled",
    "total_tokens",
    "prompt_tokens",
    "reasoning_tokens",
    "input_token_details",
    "output_token_details",
    "usage",
    "usage_metadata",
]

# --- and these real secrets MUST still be redacted -------------------------

SENSITIVE_KEYS = [
    "api_key",
    "access_token",
    "client_secret",
    "password",
    "authorization",
    "Authorization",
    "Set-Cookie",
    "proxy-authorization",
    "csrf_token",
    "refresh_token",
    "cookie",
    "session_token",
]


@pytest.mark.parametrize("key", SAFE_KEYS)
def test_safe_keys_are_not_sensitive(key: str) -> None:
    assert _is_sensitive_protocol_key(key) is False


@pytest.mark.parametrize("key", SENSITIVE_KEYS)
def test_sensitive_keys_are_detected(key: str) -> None:
    assert _is_sensitive_protocol_key(key) is True


def test_mapping_redacts_secrets_and_keeps_metrics() -> None:
    payload = {
        "session_id": "abc-123",
        "token_count": 42,
        "tokens_used": 99,
        "possession": "valuable",
        "my_secretary": "Pat",
        "cookies_enabled": True,
        "usage": {"total_tokens": 8, "prompt_tokens": 3},
        "api_key": "sk-abcdef0123456789abcdef",
        "access_token": "a-real-access-token",
        "headers": {
            "Authorization": "Bearer xyz",
            "Cookie": "moldy_at=abc",
            "User-Agent": "safe-agent",
        },
    }

    result = redact_protocol_data("values", payload)

    # Metrics / non-secret keys preserved.
    assert result["session_id"] == "abc-123"
    assert result["token_count"] == 42
    assert result["tokens_used"] == 99
    assert result["possession"] == "valuable"
    assert result["my_secretary"] == "Pat"
    assert result["cookies_enabled"] is True
    assert result["usage"] == {"total_tokens": 8, "prompt_tokens": 3}
    assert result["headers"]["User-Agent"] == "safe-agent"

    # Real secrets redacted.
    assert result["api_key"] == REDACTED_SENSITIVE_FIELD
    assert result["access_token"] == REDACTED_SENSITIVE_FIELD
    assert result["headers"]["Authorization"] == REDACTED_SENSITIVE_FIELD
    assert result["headers"]["Cookie"] == REDACTED_SENSITIVE_FIELD


# --- 2. Leak paths: value-based masking ------------------------------------


def test_serialized_header_value_pair_is_masked() -> None:
    # ``{name: 'authorization', value: 'Bearer xyz'}`` serialization leaks the
    # token under the generic ``value`` key — the Bearer value mask catches it.
    payload = {"name": "authorization", "value": "Bearer eyJabc.def.ghi"}
    result = redact_protocol_data("values", payload)
    assert "eyJabc.def.ghi" not in json.dumps(result)
    assert result["value"].startswith("Bearer <redacted>")


def test_bare_bearer_token_in_string_is_masked() -> None:
    secret = "moldy-super-secret-bearer-token-value"
    result = redact_protocol_data("debug_traces", f"Authorization: Bearer {secret}")
    assert secret not in result
    assert "Bearer <redacted>" in result


def test_bare_jwt_in_string_is_masked() -> None:
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    result = redact_protocol_data("debug_traces", f"token is {jwt} here")
    assert jwt not in result
    assert REDACTED_SENSITIVE_FIELD in result


def test_openai_style_key_in_string_is_masked() -> None:
    key = "sk-proj-abcdefABCDEF0123456789abcdefABCDEF0123"
    result = redact_protocol_data("debug_traces", f"using key {key} now")
    assert key not in result
    assert REDACTED_SENSITIVE_FIELD in result


def test_nonstandard_cookie_token_name_is_masked() -> None:
    for cookie in ("moldy_at", "moldy_rt", "moldy_csrf"):
        value = f"{cookie}=secret-cookie-payload-value"
        result = redact_protocol_data("debug_traces", value)
        assert "secret-cookie-payload-value" not in result
        assert result == f"{cookie}=<redacted>"


def test_url_userinfo_credentials_are_masked() -> None:
    url = "http://admin:hunter2pass@internal.example.com/path"
    result = redact_protocol_data("debug_traces", url)
    assert "hunter2pass" not in result
    assert "admin" not in result
    assert result == "http://<redacted>@internal.example.com/path"


def test_assignment_secret_in_string_is_masked() -> None:
    result = redact_protocol_data("debug_traces", "api_key=sk-abcdef0123456789abcdef")
    assert "sk-abcdef0123456789abcdef" not in result
    assert REDACTED_SENSITIVE_FIELD in result


# --- normal prose must not be over-masked ----------------------------------


def test_normal_prose_is_untouched() -> None:
    prose = (
        "The session lasted two hours and we used 42 tokens to summarize the "
        "secretary's notes about the upcoming possession hearing."
    )
    result = redact_protocol_data("debug_traces", prose)
    assert result == prose


def test_plain_url_without_credentials_is_untouched() -> None:
    url = "https://api.example.com/v1/resource?id=42"
    result = redact_protocol_data("debug_traces", url)
    assert result == url


# --- 3. state-snapshot no-op removal preserves observable output -----------


@pytest.mark.parametrize("method", ["values", "updates"])
def test_state_snapshot_output_matches_key_redaction(method: str) -> None:
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "session_id": "keep-me",
        "api_key": "leak-me",
        "nested": {"refresh_token": "secret", "total_tokens": 5},
    }
    result = redact_protocol_data(method, payload)
    assert result == {
        "messages": [{"role": "user", "content": "hi"}],
        "session_id": "keep-me",
        "api_key": REDACTED_SENSITIVE_FIELD,
        "nested": {"refresh_token": REDACTED_SENSITIVE_FIELD, "total_tokens": 5},
    }
