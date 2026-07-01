"""ADR-021 M0 — run-scoped secret collection + ContextVar plumbing."""

from __future__ import annotations

from app.agent_runtime.run_secrets import (
    add_run_secrets,
    collect_secret_values,
    get_run_secrets,
    reset_run_secrets,
    set_run_secrets,
)

# --- collect_secret_values: flattening + min-length + Bearer body ----------


def test_collect_flattens_nested_dict_and_list() -> None:
    obj = {
        "api_key": "sk-proj-abcdef0123456789",
        "nested": {"headers": ["short", "Cookie-secret-value-long"]},
        "list_of_dicts": [{"password": "hunter2-strong-pass"}],
    }
    result = collect_secret_values(obj)
    assert "sk-proj-abcdef0123456789" in result
    assert "Cookie-secret-value-long" in result
    assert "hunter2-strong-pass" in result


def test_collect_drops_values_under_min_length() -> None:
    # ``len < 5`` are low signal — never collected.
    result = collect_secret_values({"a": "1234", "b": "12345", "c": "x"})
    assert "1234" not in result
    assert "x" not in result
    assert "12345" in result


def test_collect_extracts_bearer_token_body() -> None:
    result = collect_secret_values("Bearer my-opaque-bearer-token-body")
    # Both the full header value and the bare token body are collected.
    assert "Bearer my-opaque-bearer-token-body" in result
    assert "my-opaque-bearer-token-body" in result


def test_collect_extracts_basic_token_body() -> None:
    result = collect_secret_values({"Authorization": "Basic dXNlcjpwYXNzd29yZA=="})
    assert "Basic dXNlcjpwYXNzd29yZA==" in result
    assert "dXNlcjpwYXNzd29yZA==" in result


def test_collect_pair_shape_takes_value_only() -> None:
    # ADR-021 review #3 — ``{name|key|header: <meta>, value: <secret>}`` is the
    # canonical serialized header/param pair shape. The metadata field (e.g. the
    # header name ``Authorization``) must NOT be collected, else it over-masks
    # that common word in legitimate prose.
    result = collect_secret_values(
        [{"name": "Authorization", "value": "Bearer supersecrettoken12345"}]
    )
    assert "Authorization" not in result
    assert "Bearer supersecrettoken12345" in result
    assert "supersecrettoken12345" in result  # bare body still split out

    # ``{key, value}`` variant behaves the same.
    keyed = collect_secret_values({"key": "x-api-name", "value": "longsecret999"})
    assert "x-api-name" not in keyed
    assert "longsecret999" in keyed


def test_collect_pair_shape_keeps_sibling_secrets() -> None:
    # ADR-021 re-review #3 — skipping the meta field must NOT drop sibling
    # secret fields. ``{name, value, token}`` keeps ``token`` (only ``name`` is
    # treated as metadata and skipped).
    result = collect_secret_values(
        {"name": "Authorization", "value": "Bearer aaaaa11111", "token": "sk-sibling-secret-99999"}
    )
    assert "Authorization" not in result  # meta field still skipped
    assert "Bearer aaaaa11111" in result
    assert "sk-sibling-secret-99999" in result  # sibling secret survives


def test_collect_ignores_non_str_leaves() -> None:
    result = collect_secret_values({"count": 12345, "flag": True, "blob": b"rawbytes"})
    assert result == set()


def test_collect_handles_none_and_empty() -> None:
    assert collect_secret_values(None) == set()
    assert collect_secret_values({}) == set()
    assert collect_secret_values([]) == set()


# --- set/get/reset + add (lazy union) --------------------------------------


def test_set_then_get_returns_collected_values() -> None:
    token = set_run_secrets(["sk-proj-abcdef0123456789"])
    try:
        secrets = get_run_secrets()
        assert secrets is not None
        assert "sk-proj-abcdef0123456789" in secrets
    finally:
        reset_run_secrets(token)


def test_get_is_none_when_unset() -> None:
    # No active run — value masking must be a no-op.
    assert get_run_secrets() is None


def test_set_none_installs_empty_set_for_lazy_union() -> None:
    token = set_run_secrets(None)
    try:
        current = get_run_secrets()
        assert current == set()
        add_run_secrets({"field": "lazy-skill-secret-value"})
        assert "lazy-skill-secret-value" in (get_run_secrets() or set())
    finally:
        reset_run_secrets(token)


def test_add_run_secrets_unions_in_place() -> None:
    token = set_run_secrets(["eager-secret-value"])
    try:
        live = get_run_secrets()
        add_run_secrets({"username": "ignored-short", "password": "lazy-secret-value"})
        # Same object mutated in place (subagents observe the union).
        assert get_run_secrets() is live
        assert "eager-secret-value" in (live or set())
        assert "lazy-secret-value" in (live or set())
        assert "ignored-short" in (live or set())  # 13 chars >= 5
    finally:
        reset_run_secrets(token)


def test_add_run_secrets_noop_when_unset() -> None:
    # Lazy skill path runs even when no run opted in (DB-free tests) — must
    # not crash and must not install a set.
    add_run_secrets({"field": "would-be-secret-value"})
    assert get_run_secrets() is None


def test_reset_restores_previous_state_no_leak() -> None:
    assert get_run_secrets() is None
    token = set_run_secrets(["run-one-secret-value"])
    reset_run_secrets(token)
    # No leak across runs.
    assert get_run_secrets() is None


def test_add_skill_secrets_unions_into_cfg_secret_values() -> None:
    # cfg.secret_values는 worker의 error_message 마스킹이 ContextVar reset 후에도
    # 읽는 소스다(_redact_run_error_message). lazy 스킬 credential이 여기 들어가지
    # 않으면 에러 메시지 마스킹을 빠져나간다(2차 리뷰 gap).
    from types import SimpleNamespace

    from app.agent_runtime.runtime_component_builder import _add_skill_secrets_to_run

    cfg = SimpleNamespace(secret_values=set())
    skill_ctx = SimpleNamespace(
        descriptors={
            "s1": SimpleNamespace(
                credential_bindings={
                    "b1": SimpleNamespace(decrypted={"api_key": "skill-plaintext-secret-abcdef123"})
                }
            )
        }
    )

    # ContextVar 미설정이어도 cfg union은 동작해야 한다(add_run_secrets는 no-op).
    _add_skill_secrets_to_run(skill_ctx, cfg)

    assert "skill-plaintext-secret-abcdef123" in cfg.secret_values
