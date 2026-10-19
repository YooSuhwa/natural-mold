"""I24 M2/L1 unit contracts: error-code table, bounded child ledger, stable digest
material and scrubbed provider construction errors."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from app.agent_runtime.lease_bound_model import LeaseSendGuard
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.lease_google_transport import ProviderTransportDescriptor
from app.agent_runtime.lease_model_factory import (
    LeaseModelConstructionError,
    LeaseModelSpec,
    create_lease_chat_model,
)
from app.agent_runtime.prepared_child_launch import LEASE_TERMINAL, ChildLaunchLedger
from app.agent_runtime.run_error_codes import (
    denial_error_code,
    inherited_envelope_code,
    run_error_code,
)
from app.services.prepared_conversation_start import PreparedStartError


@pytest.mark.parametrize(
    "code", ["tenant_suspended", "forbidden", "organization_archived", "prepared_run_lease_missing"]
)
def test_denial_codes_map_directly_and_through_wrapping(code: str) -> None:
    denied = DispatchDeniedError(code)
    try:
        raise RuntimeError("Agent run failed") from denied
    except RuntimeError as wrapped:
        assert denial_error_code(wrapped) == code
    assert run_error_code(denied, "stream_error") == code
    assert inherited_envelope_code(code) == code


def test_unknown_codes_keep_the_entry_default() -> None:
    assert run_error_code(DispatchDeniedError("child_launch_duplicate"), "stream_error") == (
        "stream_error"
    )
    assert run_error_code(ValueError("boom"), "runtime_error") == "runtime_error"
    assert inherited_envelope_code("runtime_error") is None


def test_prestart_error_uses_the_cause_machine_code_only() -> None:
    class Coded(RuntimeError):
        def __init__(self, code: str) -> None:
            super().__init__(f"{code}:/private/tmp/run-root")
            self.code = code

    for cause, expected in (
        (Coded("lease_backend_root_shared"), "lease_backend_root_shared"),
        (Coded("bad code with /path"), "runtime_error"),
        (ValueError("no code"), "runtime_error"),
    ):
        try:
            raise PreparedStartError from cause
        except PreparedStartError as error:
            assert run_error_code(error, "runtime_error") == expected


def test_closed_lease_ids_are_bounded_lru() -> None:
    ledger = ChildLaunchLedger(closed_cap=3)
    leases = [uuid.uuid4() for _ in range(5)]
    for lease_id in leases:
        ledger.terminalize(lease_id)
    assert [ledger.is_closed(lease_id) for lease_id in leases] == [False, False, True, True, True]
    with pytest.raises(DispatchDeniedError) as denied:
        ledger.ensure_open(leases[-1])
    assert denied.value.code == LEASE_TERMINAL


def test_digest_material_has_no_process_identity() -> None:
    first = ProviderTransportDescriptor(
        provider="google",
        transport_kind="httpx_async",
        client_identity=987654321,
        sdk_attempts=1,
        adapter_max_retries=0,
        base_url="https://generativelanguage.googleapis.com",
        api_version="v1beta",
        allowed_headers=(),
        cleanup_owner="lease_google_owned_cleanup",
    )
    second = replace(first, client_identity=123456789)
    assert first.digest_material() == second.digest_material()
    assert 987654321 not in first.digest_material().values()


def test_provider_validation_error_is_scrubbed() -> None:
    secret = "sk-live-secret-value"
    spec = LeaseModelSpec(
        provider="openai", model_name="gpt-4o", api_key="sk-x", params={"max_tokens": secret}
    )
    with pytest.raises(LeaseModelConstructionError) as rejected:
        create_lease_chat_model(spec, guard=LeaseSendGuard.__new__(LeaseSendGuard))
    assert secret not in str(rejected.value) and "max_tokens" in str(rejected.value)
    assert rejected.value.__cause__ is None and rejected.value.__suppress_context__
