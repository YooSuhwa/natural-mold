"""Ratchet: every membership-family writer queues its tuples (plan 7.2.1, C-04 follow-up).

A module that constructs or bulk-deletes organization/company membership, role, group or
capability rows (or flips a member's status) must capture and queue its subjects through
``app.authz.membership_sync``. A new writer fails this test until it is wired and listed.
"""

import re
from pathlib import Path

APP = Path(__file__).parents[2] / "app"
_MODELS = (
    "OrganizationMember|TenantMember|GroupMember|OrganizationRoleGrant|TenantRoleGrant"
    "|OrgCapabilityGrant|Group|Organization"
)
WRITE = re.compile(rf"\b({_MODELS})\(|delete\(({_MODELS})\)|member\.status = |member\.status, ")
SYNC = re.compile(r"MembershipSync|LazySync|capture_(user|company|account)|queue_created")
EXEMPT = {
    "models/organization.py": "ORM definitions",
    "models/organization_group.py": "ORM definitions",
    "migrations/org_authz_v1/rls_policies.py": "policy SQL text, no writes",
    "authz/org_member_exit.py": "exit steps; callers (orgs/members, tenants/member_exit) sync",
}
WRITERS = {
    "auth/sso/membership.py",
    "seed/e2e_scope.py",
    "services/orgs/capabilities.py",
    "services/orgs/group_members.py",
    "services/orgs/groups.py",
    "services/orgs/invitation_membership.py",
    "services/orgs/members.py",
    "services/orgs/roles.py",
    "services/platform/tenant_creation.py",
    "services/registration_membership.py",
    "services/registration_personal.py",
    "services/tenants/invitation_membership.py",
    "services/tenants/member_exit.py",
    "services/tenants/members.py",
    "services/tenants/organization_administrators.py",
    "services/tenants/organizations.py",
    "services/tenants/roles.py",
}


def _writers() -> set[str]:
    return {
        str(path.relative_to(APP))
        for path in APP.rglob("*.py")
        if WRITE.search(path.read_text(encoding="utf-8"))
    }


def test_membership_writers_are_pinned_and_queue_their_tuples() -> None:
    found = _writers() - set(EXEMPT)
    assert found == WRITERS, "wire the new writer through membership_sync, then list it"
    unsynced = sorted(p for p in WRITERS if not SYNC.search((APP / p).read_text("utf-8")))
    assert unsynced == []


def test_ratchet_detects_a_new_unsynced_writer() -> None:
    sample = "db.add(OrganizationMember(org_id=o, user_id=u))\n"
    assert WRITE.search(sample) and not SYNC.search(sample)
