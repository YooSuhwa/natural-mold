"""ADR-021 M1 — value-based masking layer in ``redact_protocol_data``.

Covers the value-masking layer that runs BEFORE the key heuristics, both via
the explicit ``secret_values`` arg and via the run-scoped ContextVar, plus the
no-leak guarantee across runs.
"""

from __future__ import annotations

import json

from app.agent_runtime.protocol_redaction import (
    REDACTED_SENSITIVE_FIELD,
    _mask_known_values,
    redact_protocol_data,
)
from app.agent_runtime.run_secrets import get_run_secrets, reset_run_secrets, set_run_secrets

# --- (b) _mask_known_values masks injected secrets, leaves prose intact -----


def test_mask_known_values_masks_secret_in_string_leaf() -> None:
    secret = "sk-proj-abcdef0123456789ABCDEF"
    out = _mask_known_values(f"the key is {secret} done", [secret])
    assert secret not in out
    assert REDACTED_SENSITIVE_FIELD in out
    assert out == f"the key is {REDACTED_SENSITIVE_FIELD} done"


def test_mask_known_values_recurses_nested_dict_and_list() -> None:
    secret = "supersecret-dsn-password-value"
    data = {
        "tool_output": {"detail": f"postgres://user:{secret}@host/db"},
        "items": [f"echoed {secret}", "clean text"],
        "normal": "this stays exactly the same",
    }
    out = _mask_known_values(data, [secret])
    assert secret not in json.dumps(out)
    assert out["normal"] == "this stays exactly the same"
    assert out["items"][1] == "clean text"
    assert REDACTED_SENSITIVE_FIELD in out["items"][0]


def test_mask_known_values_does_not_overmask_short_values() -> None:
    # Values < 5 chars are dropped by the shared core → no over-masking of
    # benign text fragments.
    out = _mask_known_values("id=42 count=7 abcd", ["42", "7", "abcd"])
    assert out == "id=42 count=7 abcd"


def test_mask_known_values_noop_without_secrets() -> None:
    data = {"value": "Authorization header text"}
    assert _mask_known_values(data, None) is data
    assert _mask_known_values(data, []) is data


def test_mask_masks_secret_dict_keys() -> None:
    # ADR-021 review #2 — a secret echoed as a dict KEY must be masked too, not
    # just its occurrence inside values. Value-only masking leaked the key.
    out = _mask_known_values({"my-secret-token": "my-secret-token at end"}, ["my-secret-token"])
    assert "my-secret-token" not in out  # key masked, not preserved
    assert out == {REDACTED_SENSITIVE_FIELD: f"{REDACTED_SENSITIVE_FIELD} at end"}


def test_mask_preserves_non_secret_keys() -> None:
    # Keys that don't contain a run secret are left verbatim (no over-masking).
    secret = "sk-secret-value-123"
    out = _mask_known_values({"status": "ok", "api_key": secret}, [secret])
    assert out == {"status": "ok", "api_key": REDACTED_SENSITIVE_FIELD}


def test_mask_secret_key_collision_collapses_without_leak() -> None:
    # ADR-021 re-review #2 — two DISTINCT secret keys both mask to the
    # placeholder and collapse to one entry (documented egress behavior). No
    # leak: both keys are masked; the surviving value is also masked if secret.
    out = _mask_known_values(
        {"secretAAAAA": "vA", "secretBBBBB": "vB"}, ["secretAAAAA", "secretBBBBB"]
    )
    assert list(out.keys()) == [REDACTED_SENSITIVE_FIELD]  # collapsed, no plaintext key
    assert "secretAAAAA" not in out
    assert "secretBBBBB" not in out


# --- (c) propagation via ContextVar without an explicit arg -----------------


def test_redact_masks_via_contextvar_without_explicit_arg() -> None:
    secret = "sk-proj-context-var-secret-9999"
    token = set_run_secrets([secret])
    try:
        result = redact_protocol_data("custom", {"detail": f"leaked {secret} here"})
    finally:
        reset_run_secrets(token)
    rendered = json.dumps(result)
    assert secret not in rendered
    assert REDACTED_SENSITIVE_FIELD in rendered


def test_redact_masks_secret_in_tool_output_via_contextvar() -> None:
    # A DB-injected DSN password (in the run set) appears in tool output.
    dsn_password = "p@ssw0rd-injected-secret-value"
    token = set_run_secrets([dsn_password])
    try:
        result = redact_protocol_data(
            "values",
            {"output": {"text": f"connecting with {dsn_password}"}},
        )
    finally:
        reset_run_secrets(token)
    assert dsn_password not in json.dumps(result)


def test_explicit_secret_values_arg_overrides_contextvar() -> None:
    # Explicit arg is honoured even with no ContextVar set.
    assert get_run_secrets() is None
    secret = "explicit-arg-secret-value"
    result = redact_protocol_data(
        "custom",
        {"detail": f"value {secret}"},
        secret_values=[secret],
    )
    assert secret not in json.dumps(result)


# --- (d) ContextVar reset → no leak across runs -----------------------------


def test_no_leak_across_runs() -> None:
    secret = "run-one-only-secret-value"
    token = set_run_secrets([secret])
    reset_run_secrets(token)
    # Second run never set this secret → it must NOT be masked anymore.
    result = redact_protocol_data("custom", {"detail": f"value {secret}"})
    assert secret in json.dumps(result)


def test_unset_contextvar_is_pure_noop_for_value_layer() -> None:
    # Without a run set, an arbitrary opaque token survives the value layer
    # (heuristics may still touch keyed values, but a bare prose token does
    # not match any value-mask). Confirms value masking is gated on the set.
    assert get_run_secrets() is None
    token = "totallyrandomopaquetokenstring1234567890"
    result = redact_protocol_data("custom", {"note": f"saw {token}"})
    assert token in json.dumps(result)
