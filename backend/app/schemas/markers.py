"""Reserved name prefixes used to identify rows created by migrations.

Split from schemas.connection to avoid circular imports between
schemas/connection ↔ services/env_var_resolver ↔ services/credential_service
↔ schemas/credential.
"""

# m10 마이그레이션이 env 기반 자동 시드한 connection/credential에 부여하는 프리픽스.
# downgrade가 이 프리픽스로 seeded row를 식별해 역삭제하므로, API 경계에서 사용자가
# 이 프리픽스를 직접 쓰지 못하도록 예약 (display_name / name 두 필드 모두).
M10_SEED_MARKER = "[m10-auto-seed]"


def check_reserved_marker(value: str | None, field_name: str) -> str | None:
    """API 경계에서 `M10_SEED_MARKER` 프리픽스 사용을 차단.

    connection.display_name 과 credential.name 모두 m10 auto-seed downgrade의
    LIKE 매칭 대상이므로, 사용자가 이 프리픽스를 직접 쓸 수 있으면 rollback 시
    사용자 수동 생성분까지 삭제되는 데이터 손실이 발생한다.

    None은 패스스루 (PATCH 미전송 / Optional 필드 고려).
    """
    if value is None:
        return value
    if value.startswith(M10_SEED_MARKER):
        raise ValueError(
            f"{field_name} cannot start with the reserved marker "
            f"'{M10_SEED_MARKER}' — reserved for m10 auto-seeded rows so "
            "rollback can safely identify them."
        )
    return value
