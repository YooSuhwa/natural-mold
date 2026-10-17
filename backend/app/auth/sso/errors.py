"""Typed SSO errors; messages never echo IdP responses, secrets or directory data."""

from typing import Final

from app.exceptions import AppError

_ERRORS: Final[dict[str, tuple[int, str]]] = {
    "domain_invalid": (422, "도메인 형식이 올바르지 않습니다"),
    "domain_taken": (409, "이미 이 회사에 등록한 도메인입니다"),
    "domain_not_verified": (409, "DNS TXT 레코드에서 확인 값을 찾지 못했습니다"),
    "ldap_plaintext_refused": (422, "LDAP은 ldaps:// 또는 StartTLS 연결만 허용합니다"),
    "sso_config_invalid": (422, "SSO 연결 설정이 올바르지 않습니다"),
    "sso_connection_not_tested": (409, "시험 로그인을 통과한 연결만 활성화할 수 있습니다"),
    "sso_policy_incomplete": (
        409,
        "SSO 필수에는 활성 연결, 확인된 도메인, 비상 관리자 1~2명이 필요합니다",
    ),
    "sso_required": (403, "이 회사는 회사 계정(SSO)으로만 로그인할 수 있습니다"),
    "sso_unavailable": (404, "사용할 수 없는 SSO 연결입니다"),
    "sso_state_invalid": (400, "로그인 요청이 만료되었거나 올바르지 않습니다"),
    "sso_discovery_failed": (502, "IdP 설정을 불러오지 못했습니다"),
    "sso_issuer_mismatch": (400, "IdP 발급자(issuer)가 연결 설정과 다릅니다"),
    "sso_token_invalid": (400, "IdP 토큰을 확인하지 못했습니다"),
    "sso_idp_error": (400, "IdP가 로그인을 거부했습니다"),
    "sso_email_missing": (403, "IdP가 확인된 이메일을 보내지 않았습니다"),
    "sso_email_unverified": (
        403,
        "IdP가 이메일 확인 여부를 보내지 않아 기존 계정에 연결할 수 없습니다",
    ),
    "sso_link_pending": (409, "기존 계정의 비밀번호로 한 번 로그인해 회사 계정 연결을 확인하세요"),
    "sso_signup_blocked": (403, "이 도메인은 회사 계정(SSO)으로 로그인합니다"),
    "sso_domain_unverified": (403, "회사가 확인한 도메인의 계정이 아닙니다"),
    "sso_not_invited": (403, "초대받은 구성원만 로그인할 수 있습니다"),
    "sso_membership_blocked": (403, "이 회사의 구성원 자격이 정지되었거나 제거되었습니다"),
    "sso_no_organization": (409, "가입할 조직이 없습니다"),
    "invalid_credentials": (401, "아이디 또는 비밀번호가 올바르지 않습니다"),
    "login_rate_limited": (429, "로그인 시도가 많습니다. 잠시 후 다시 시도하세요"),
    "ldap_unavailable": (503, "디렉터리 서버에 연결할 수 없습니다"),
    "login_csrf_invalid": (403, "로그인 요청을 확인하지 못했습니다. 다시 시도하세요"),
}


def sso_error(code: str) -> AppError:
    status, message = _ERRORS[code]
    return AppError(code=code, message=message, status=status)
