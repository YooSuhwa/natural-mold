"""Reserved name prefixes used to identify rows created by migrations.

Split from schemas.connection to avoid circular imports between
schemas/connection ↔ services/env_var_resolver ↔ services/credential_service
↔ schemas/credential.
"""

# m10 마이그레이션이 env 기반 자동 시드한 connection/credential에 부여하는 프리픽스.
# downgrade가 이 프리픽스로 seeded row를 식별해 역삭제하므로, API 경계에서 사용자가
# 이 프리픽스를 직접 쓰지 못하도록 예약 (display_name / name 두 필드 모두).
M10_SEED_MARKER = "[m10-auto-seed]"
