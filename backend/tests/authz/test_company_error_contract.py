"""Company administration errors retain their typed HTTP contract."""

import pytest

from app.authz import audit_actions
from app.authz.error_codes import ERRORS, ErrorCode, authorization_error


@pytest.mark.parametrize(
    ("code", "status", "message"),
    [
        (
            "default_organization_archive_forbidden",
            409,
            "기본 조직은 보관할 수 없습니다",
        ),
        ("tenant_namespace_collision", 500, "회사 내부 식별자 충돌이 발생했습니다"),
        ("duplicate_tenant_slug", 409, "같은 회사 슬러그가 이미 있습니다"),
        ("last_tenant_admin", 409, "마지막 회사 관리자를 제거할 수 없습니다"),
        ("last_organization", 409, "마지막 조직은 보관할 수 없습니다"),
        ("organization_archive_name_mismatch", 409, "조직 이름 확인이 일치하지 않습니다"),
        ("organization_restore_expired", 410, "조직 복원 가능 기간이 만료되었습니다"),
        ("tenant_invitation_invalid", 404, "사용할 수 없는 회사 초대 링크입니다"),
        ("tenant_invitation_used", 409, "이미 사용한 회사 초대 링크입니다"),
        ("tenant_invitation_expired", 410, "회사 초대 링크가 만료되었습니다"),
        (
            "tenant_invitation_email_mismatch",
            403,
            "초대받은 이메일의 계정으로 로그인해 주세요",
        ),
        ("tenant_invitation_revoked", 404, "사용할 수 없는 회사 초대 링크입니다"),
        ("tenant_status_conflict", 409, "현재 회사 상태에서는 처리할 수 없습니다"),
    ],
)
def test_company_error_code_has_exact_typed_contract(
    code: ErrorCode, status: int, message: str
) -> None:
    # Given: a Task-3 company error required by the approved route contract.
    # When: the shared authorization error factory creates its envelope error.
    assert code in ERRORS
    error = authorization_error(code)
    # Then: status, code, and redacted message are exact.
    assert (error.status, error.code, error.message) == (status, code, message)


def test_tenant_namespace_collision_error_does_not_disclose_namespace() -> None:
    # Given: the internal namespace uniqueness failure required by B12 creation.
    # When: the shared typed error is constructed.
    error = authorization_error("tenant_namespace_collision")
    # Then: the message contains no namespace representation.
    assert "tenants/" not in error.message


def test_company_audit_actions_have_exact_stable_names() -> None:
    # Given: B11/B12 changes that must emit auditable events.
    # When: their action constants are read by a service.
    actions = (
        audit_actions.TENANT_CREATE,
        audit_actions.TENANT_UPDATE,
        audit_actions.TENANT_SUSPEND,
        audit_actions.TENANT_RESUME,
        audit_actions.ORG_CREATE,
        audit_actions.ORG_ARCHIVE,
        audit_actions.ORG_RESTORE,
        audit_actions.TENANT_INVITATION_CREATE,
        audit_actions.TENANT_INVITATION_ACCEPT,
        audit_actions.TENANT_INVITATION_REVOKE,
        audit_actions.TENANT_INVITATION_RESEND,
        audit_actions.TENANT_INVITATION_REISSUE,
    )
    # Then: each value remains the machine-consumed audit action identifier.
    assert actions == (
        "tenant.create",
        "tenant.update",
        "tenant.suspend",
        "tenant.resume",
        "org.create",
        "org.archive",
        "org.restore",
        "tenant.invitation_create",
        "tenant.invitation_accept",
        "tenant.invitation_revoke",
        "tenant.invitation_resend",
        "tenant.invitation_reissue",
    )
