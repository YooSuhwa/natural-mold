"""Membership writer inventory contract test."""

from pathlib import Path

from app.authz.tenant_org_write_locks import (
    CURRENT_MEMBERSHIP_WRITERS,
    FUTURE_MEMBERSHIP_WRITER_CONTRACTS,
)


def test_membership_writer_inventory_covers_current_and_future_seams() -> None:
    # Given: current writers and the Task 7/8/9 consumer contracts.
    repository = Path(__file__).parents[3]

    # When: the checked inventory is evaluated.
    current = set(CURRENT_MEMBERSHIP_WRITERS)
    future = set(FUTURE_MEMBERSHIP_WRITER_CONTRACTS)

    # Then: every current writer exists while future contracts need no placeholder file.
    assert current == {
        "backend/app/services/orgs/members.py",
        "backend/app/services/orgs/roles.py",
        "backend/app/services/orgs/invitation_acceptance.py",
        "backend/app/services/orgs/invitation_membership.py",
        "backend/app/services/registration_policy.py",
        "backend/app/services/registration_membership.py",
    }
    assert all((repository / path).is_file() for path in current)
    required_seams = {
        "backend/app/services/orgs/members.py": (
            "members_write_context(",
            "lock_member_rows(",
            "preflight_member_exit(",
        ),
        "backend/app/services/orgs/roles.py": (
            "members_write_context(",
            ".order_by(OrganizationRoleGrant.id)",
            ".with_for_update()",
        ),
        "backend/app/services/orgs/invitation_acceptance.py": (
            "locked_invitation_scope(",
            "lock_member_rows(",
            "join_invited_membership(",
        ),
        "backend/app/services/orgs/invitation_membership.py": (
            "join_invited_membership(",
            'company.status != "active"',
        ),
        "backend/app/services/registration_policy.py": (
            "locked_registration_scope(",
            "include_default=settings.allow_first_user_as_admin",
        ),
        "backend/app/services/registration_membership.py": (
            "enrollment.scope.tenant",
            "ensure_signup_scope_active(enrollment.scope)",
        ),
    }
    for path, seams in required_seams.items():
        source = (repository / path).read_text()
        assert all(seam in source for seam in seams), path
    assert future == {
        "backend/app/services/tenants/organizations.py",
        "backend/app/services/tenants/members.py",
        "backend/app/services/tenants/roles.py",
        "backend/app/services/tenants/invitations.py",
    }
