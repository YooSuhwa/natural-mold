"""Fixed native registry for the B11/B12 company-administration PostgreSQL lane."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

# Every integration module added by B11/B12 and parent Task 10 (prepared leases), relative to
# backend/. The ordinary registry contract compares this tuple with the files on disk.
COMPANY_ADMINISTRATION_MODULES: Final[tuple[str, ...]] = (
    "tests/authz/test_company_administration_migration_integration.py",
    "tests/authz/test_b04_write_serialization_integration.py",
    "tests/authz/test_registration_lock_order_integration.py",
    "tests/authz/test_tenant_member_exit_integration.py",
    "tests/authz/test_tenant_member_exit_authority_integration.py",
    "tests/authz/test_tenant_member_exit_races_integration.py",
    "tests/authz/test_tenant_member_exit_invitation_accept_first_integration.py",
    "tests/authz/test_tenant_member_exit_invitation_remove_first_integration.py",
    "tests/authz/test_tenant_organizations_integration.py",
    "tests/authz/test_tenant_organization_atomicity_integration.py",
    "tests/authz/test_company_admin_org_assignments_integration.py",
    "tests/authz/test_tenant_members_integration.py",
    "tests/authz/test_tenant_invitation_registration_integration.py",
    "tests/authz/test_tenant_invitation_accept_race_integration.py",
    "tests/authz/test_tenant_invitation_create_race_integration.py",
    "tests/authz/test_tenant_invitation_cleanup_integration.py",
    "tests/authz/test_tenant_invitation_member_exit_race_integration.py",
    "tests/authz/test_task10_authority_integration.py",
    "tests/authz/test_task10_chat_integration.py",
    "tests/authz/test_task10_queue_integration.py",
    "tests/authz/test_task10_external_integration.py",
    "tests/authz/test_task10_lifecycle_compat_integration.py",
    "tests/authz/test_platform_tenant_creation_integration.py",
    "tests/authz/test_platform_tenant_creation_rollback_integration.py",
    "tests/authz/test_platform_tenant_suspension_integration.py",
    "tests/authz/test_company_administration_http_integration.py",
    "tests/integration/test_tenant_session_scope_postgres.py",
)
# Selected B04/B05/B08 nodes the company claims depend on (plan Task 14 legacy registry).
COMPANY_ADMINISTRATION_LEGACY_NODES: Final[tuple[str, ...]] = (
    "tests/authz/test_org_catalog_invitation_integration.py"
    "::test_live_catalog_scope_and_serialized_invitation_retries",
    "tests/integration/test_agent_lifecycle_run_ownership.py"
    "::test_native_owner_rows_projection_and_foreign_ack_barrier",
    "tests/integration/test_agent_lifecycle_chat_races.py"
    "::test_native_archive_first_config_first_and_recovery_zero_launch",
    "tests/integration/test_agent_lifecycle_chat_races.py"
    "::test_native_launch_first_owner_cancels_and_acknowledges",
    "tests/integration/test_agent_lifecycle_chat_races.py"
    "::test_native_revoked_inactive_recovery_preserves_history_without_payload_hydration",
    "tests/integration/test_agent_lifecycle_api_races.py"
    "::test_native_api_archive_after_config_before_wait_invoke",
    "tests/integration/test_agent_lifecycle_api_races.py"
    "::test_native_api_launch_first_archive_cancels_actual_stream_owner",
    "tests/integration/test_agent_lifecycle_api_races.py"
    "::test_native_api_surfaces_keep_snapshot_after_envelope_detach",
    "tests/integration/test_agent_lifecycle_trigger_races.py"
    "::test_native_scheduled_and_forced_runs_share_durable_ownership",
    "tests/integration/test_agent_lifecycle_trigger_races.py"
    "::test_native_archive_first_and_config_first_never_invoke",
    "tests/integration/test_agent_lifecycle_trigger_races.py"
    "::test_native_launch_first_cancels_local_owner_and_preserves_remote_owner",
    "tests/integration/test_conversation_queue_concurrency.py"
    "::test_stop_and_claim_share_conversation_first_lock_order",
    "tests/integration/test_conversation_queue_concurrency.py"
    "::test_concurrent_stop_steer_and_claim_do_not_deadlock_or_overlap",
)
COMPANY_ADMINISTRATION_TARGETS: Final[tuple[str, ...]] = (
    COMPANY_ADMINISTRATION_MODULES + COMPANY_ADMINISTRATION_LEGACY_NODES
)


def company_administration_argv(pytest_executable: str) -> list[str]:
    """Return the fixed child argv; no caller-supplied path or marker is accepted."""
    return [
        pytest_executable,
        "-q",
        "-p",
        "tests.postgres_execution_plugin",
        *COMPANY_ADMINISTRATION_TARGETS,
        "-m",
        "integration",
    ]


def _covers(target: str, node_id: str) -> bool:
    if "::" in target:
        return node_id == target or node_id.startswith(f"{target}[")
    return node_id.startswith(f"{target}::")


def company_selection_gaps(selected: Sequence[str]) -> list[str]:
    """Name each registered target with no selected node, plus any foreign selected node."""
    missing = [
        target
        for target in COMPANY_ADMINISTRATION_TARGETS
        if not any(_covers(target, node) for node in selected)
    ]
    foreign = [
        node
        for node in selected
        if not any(_covers(target, node) for target in COMPANY_ADMINISTRATION_TARGETS)
    ]
    return [f"missing:{target}" for target in missing] + [f"foreign:{node}" for node in foreign]
