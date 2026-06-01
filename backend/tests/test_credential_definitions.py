"""ADR-017 Slice D — credential definition registry coverage.

Asserts the 8 new marketplace credential definitions are registered into
the process-wide ``app.credentials.registry`` at import time, and that
their key fields match what the Spec / contracts table claims.
"""

from __future__ import annotations

from app.credentials.definitions import (  # noqa: F401 — trigger registration
    coupang_partners,
    dart_api,
    foresttrip_account,
    k_skill_proxy,
    kipris_plus_api,
    ktx_account,
    odsay_api,
    srt_account,
)
from app.credentials.registry import registry

NEW_DEFINITION_KEYS = {
    "srt_account",
    "ktx_account",
    "foresttrip_account",
    "kipris_plus_api",
    "dart_api",
    "odsay_api",
    "coupang_partners",
    "k_skill_proxy",
}


def test_all_8_definitions_registered() -> None:
    keys = {d.key for d in registry.all()}
    missing = NEW_DEFINITION_KEYS - keys
    assert not missing, f"missing definitions in registry: {missing}"


def test_total_definition_count_is_22() -> None:
    # 13 baseline (ADR-016 era) + MCP Secret + 8 marketplace additions.
    keys = {d.key for d in registry.all()}
    assert len(keys) == 22, sorted(keys)


def test_srt_account_fields() -> None:
    definition = registry.require("srt_account")
    fields = [p.name for p in definition.properties]
    assert fields == ["username", "password"]


def test_kipris_plus_api_key_field_is_password() -> None:
    definition = registry.require("kipris_plus_api")
    api_key = next(p for p in definition.properties if p.name == "api_key")
    assert api_key.required is True
    # Password kind is the renderer hint that masks input.
    assert api_key.kind.value == "password"


def test_coupang_partners_two_fields() -> None:
    """Both fields are *required* — the credential row itself is required to
    exist. ``coupang_partners`` is treated as ``optional`` at the *skill*
    requirement level (Spec §0.1) — handled by the requirement entry's
    ``required=False`` flag, not by the definition fields.
    """

    definition = registry.require("coupang_partners")
    fields = {p.name: p for p in definition.properties}
    assert set(fields) == {"access_key", "secret_key"}
    assert fields["access_key"].required is True
    assert fields["secret_key"].required is True


def test_k_skill_proxy_base_url_required_api_key_optional() -> None:
    definition = registry.require("k_skill_proxy")
    fields = {p.name: p for p in definition.properties}
    assert set(fields) == {"base_url", "api_key"}
    assert fields["base_url"].required is True
    assert fields["api_key"].required is False
