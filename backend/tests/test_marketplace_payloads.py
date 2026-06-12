"""Marketplace JSON payload helpers for MCP / Agent resources."""

from __future__ import annotations

from app.marketplace.payloads import canonical_json_bytes, canonical_json_hash, scan_payload


def test_canonical_json_hash_is_stable_for_key_order() -> None:
    left = {
        "schema_version": 1,
        "resource": "mcp_server",
        "headers": {"Authorization": "=Bearer {{ $credentials.access_token }}"},
        "args": ["--one", "--two"],
    }
    right = {
        "args": ["--one", "--two"],
        "headers": {"Authorization": "=Bearer {{ $credentials.access_token }}"},
        "resource": "mcp_server",
        "schema_version": 1,
    }

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert canonical_json_hash(left) == canonical_json_hash(right)
    assert len(canonical_json_hash(left)) == 64


def test_canonical_json_hash_changes_when_payload_changes() -> None:
    base = {"schema_version": 1, "resource": "agent_blueprint", "name": "A"}
    changed = {"schema_version": 1, "resource": "agent_blueprint", "name": "B"}

    assert canonical_json_hash(base) != canonical_json_hash(changed)


def test_scan_payload_finds_nested_secret_values() -> None:
    payload = {
        "headers": {
            "Authorization": "Bearer sk-123456789012345678901234",
        },
        "nested": [
            {
                "env": {
                    "AWS_SECRET_ACCESS_KEY": "abc123",
                }
            }
        ],
    }

    findings = scan_payload(payload)

    assert {finding.path for finding in findings} >= {
        "$.headers.Authorization",
        "$.nested[0].env.AWS_SECRET_ACCESS_KEY",
    }


def test_scan_payload_allows_credential_placeholders() -> None:
    payload = {
        "headers": {
            "Authorization": "=Bearer {{ $credentials.access_token }}",
        },
        "env_vars": {
            "OPENAI_API_KEY": "={{ $credentials.openai_api_key }}",
        },
    }

    assert scan_payload(payload) == []


def test_scan_payload_flags_pass_segment_env_key() -> None:
    """``PASS`` as a ``_``-separated key segment must be detected."""

    payload = {"env_vars": {"DATABASE_PASS": "supersecretpw"}}

    findings = scan_payload(payload)

    assert any(
        finding.path == "$.env_vars.DATABASE_PASS" and finding.kind == "key"
        for finding in findings
    )


def test_scan_payload_flags_url_userinfo_credentials() -> None:
    """``scheme://user:password@host`` URLs embed a literal credential."""

    payload = {"url": "https://user:p4ssw0rd@host/mcp"}

    findings = scan_payload(payload)

    assert any(
        finding.path == "$.url" and finding.kind == "content" for finding in findings
    )


def test_scan_payload_flags_url_userinfo_with_empty_user() -> None:
    """``redis://:password@host`` (user omitted) is still a literal credential."""

    payload = {"url": "redis://:onlypass@host:6379/0"}

    findings = scan_payload(payload)

    assert any(
        finding.path == "$.url" and finding.kind == "content" for finding in findings
    )


def test_scan_payload_flags_auth_headers_with_literal_values() -> None:
    payload = {
        "headers": {
            "X-Api-Key": "abc123",
            "Cookie": "session=opaque-value",
            "Proxy-Authorization": "Basic dXNlcjpwdw==",
            "X-Auth-Token": "raw-token-value",
        }
    }

    findings = scan_payload(payload)
    key_paths = {finding.path for finding in findings if finding.kind == "key"}

    assert key_paths >= {
        "$.headers.X-Api-Key",
        "$.headers.Cookie",
        "$.headers.Proxy-Authorization",
        "$.headers.X-Auth-Token",
    }


def test_scan_payload_ignores_benign_config_values() -> None:
    """Segment-boundary matching must not flag ordinary config keys or
    substrings inside a segment (``passenger``, ``compass``)."""

    payload = {
        "env_vars": {
            "LOG_LEVEL": "debug",
            "BASE_URL": "https://api.example.com",
            "MODE": "production",
            "PASSENGER_COUNT": "4",
            "COMPASS_HEADING": "north",
        },
        "headers": {"Content-Type": "application/json"},
        "credential_definition_key": "mcp_oauth2",
    }

    assert scan_payload(payload) == []


def test_scan_payload_allows_empty_secret_named_values() -> None:
    """Empty values carry no secret even under a secret-looking key."""

    assert scan_payload({"env_vars": {"DATABASE_PASS": ""}}) == []


def test_scan_payload_allows_placeholder_url_userinfo() -> None:
    """A placeholder password in URL userinfo stays allowed."""

    assert scan_payload({"url": "https://user:{{$credentials.pw}}@host/mcp"}) == []
