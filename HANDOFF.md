# 작업 인계 문서

## 최근 완료 (2026-04-18)

**브랜치 `feature/credentials-field-keys-cache` — credentials list N+1 복호화 제거 (백로그 C)** — 커밋 `238f645`
- `credentials.field_keys` (sa.JSON, nullable=True) 캐시 컬럼 + Alembic `m7_add_credential_field_keys` (upgrade에서 기존 row backfill, ENCRYPTION_KEY 없으면 skip + 경고)
- `credential_service`: create 저장, update(data 변경 시만) 동기화, `extract_field_keys` 캐시 우선 + legacy NULL fallback (ADR-007)
- 신규 `backend/tests/test_credentials.py` 5 시나리오 (create/update sync/name-only preserve/list decrypt=0/legacy fallback)
- 응답 스키마 불변 (`CredentialResponse.field_keys: list[str]`)
- 검증: ruff PASS, pytest **545 passed**, alembic upgrade/downgrade/upgrade 왕복 PASS

**이전 머지**: PR #50 (백로그 재정렬 E 우선), PR #49 (백로그 C 완료), #48/#47/#46 (credential/MCP 시스템)

## 다음 작업 — 백로그 E: Connection 엔티티 통합 (M0부터)

**루트 계획**: `docs/exec-plans/active/backlog-e-connection-refactor.md` (6 마일스톤)

**새 세션 진입**:
```
docs/exec-plans/active/backlog-e-connection-refactor.md 읽고 M0 시작해줘.
/spec으로 ADR-008 (Connection 엔티티) 설계 인터뷰 진행하자.
```

**마일스톤별 권장 스킬**:
| M | 작업 | 스킬 |
|---|------|------|
| M0 | ADR-008 + 스펙 확정 (문서) | `/spec` |
| M1 | `connections` 테이블 + CRUD (parallel run) | 단독 or `/tth` |
| M2 | MCP → Connection 이관 (**고위험**) | `/tth` |
| M3 | PREBUILT per-user Connection | 단독 or `/tth` |
| M4 | CUSTOM Connection 통합 | 단독 or `/tth` |
| M5 | UI 통합 + F 흡수 | `/frontend` or `/tth` |
| M6 | Cleanup (legacy drop) | `/tth` or 단독+`/review` |

각 마일스톤마다 새 worktree + 새 브랜치 권장 (컨텍스트 캐시 효율).

## 백로그 (추천 순서)

> **순서 결정 (2026-04-18)**: 멀티 유저 인증 도입 **전에** Connection 리팩토링(E)을 먼저 완료. 이유: 인증 활성화 순간 PREBUILT 공유 행의 credential 뒤엉킴이 프로덕션 incident로 전환됨. 스키마를 먼저 정리하면 인증 도입 PR은 `get_current_user` 교체 + 세션만 얹는 작은 단위로 축소 가능.

| # | 항목 | 규모 | 비고 |
|---|------|------|------|
| ~~C~~ | ~~credentials list N+1 복호화 제거~~ | - | **완료** (PR #49) |
| **E** | **Connection 엔티티 통합 리팩토링** | 큼 | **멀티 유저 선행 필수**. PREBUILT 공유 행 per-user binding + F(다이얼로그 공통 셸) 흡수. 새 `connections(user_id, provider_name, credential_id, …)` 엔티티로 MCP/PREBUILT/CUSTOM credential 바인딩 통일. 별도 `/plan` 세션에서 ADR부터 착수 권장 |
| **D** | **`lazy="joined"` → `selectinload`** | 중 | 범용 성능 개선. E와 독립적이므로 병렬/선행 가능 |
| G | ToolCard CardFooter 3-way 분기 sub-component | 작음 | 가독성 개선 |
| H | `credentials.is_active` dead column 처리 | 작음 | 토글 API 없음 (C 삭제분석 보류 #2) |
| I | `CredentialResponse.has_data` 필드 재검토 | 작음 | 항상 True — 프론트 영향 조사 후 결정 (C 삭제분석 보류 #3) |
| ~~F~~ | ~~`CredentialPickerDialog` 공통 셸 추출~~ | - | **E에 흡수** (Connection 통합 시 UI 재편과 함께 처리) |
| (후속) | 멀티 유저 인증 도입 | 중 | E 완료 **후** 진행 — 로그인/세션 + `get_current_user` 실제화 |

## 주의사항

- **ENCRYPTION_KEY 필수** — 없으면 credential create 503. backfill 마이그레이션은 미설정 시 실패 없이 skip + 경고 (legacy fallback으로 처리)
- **from-import patch 경로** — `decrypt_api_key` spy는 반드시 `app.services.credential_service.decrypt_api_key`에 걸 것. `app.services.encryption.decrypt_api_key` patch는 서비스 모듈의 이미 바인딩된 이름에 적용 안 됨
- **SQLite batch 모드** — `drop_column`은 `op.batch_alter_table` 래핑 필요 (SQLAlchemy 2.0 + SQLite)
- pre-existing 깨진 프론트엔드 테스트: `tests/components/chat/*`, `tests/pages/chat.test.tsx`, `tests/pages/agent-*`

## 관련 파일 (백로그 C)

| 목적 | 경로 |
|------|------|
| 모델 | `backend/app/models/credential.py` (`field_keys` 추가) |
| 서비스 | `backend/app/services/credential_service.py` (create/update/extract) |
| 마이그레이션 | `backend/alembic/versions/m7_add_credential_field_keys.py` (head) |
| 테스트 | `backend/tests/test_credentials.py` (신규, 5 시나리오) |
| ADR | `docs/design-docs/adr-007-credentials-field-keys-cache.md` |
| 라우터/스키마 | 변경 없음 (응답 스키마 불변) |

## 마지막 상태

- **브랜치**: `feature/credentials-field-keys-cache` (worktree `.claude/worktrees/backlog-c`)
- **Base**: main @ `3a95a9b` (PR #48 머지)
- **커밋**: 1개 (`238f645`) — 단일 feat 커밋, push 대기
- **DB head**: `m7_add_credential_field_keys`
- **남은 결정**: HANDOFF 행 추가(H, I) 미커밋 — 별도 `[docs]` 커밋 또는 다음 브랜치로 이월
