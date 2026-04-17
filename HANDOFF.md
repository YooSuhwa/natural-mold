# 작업 인계 문서

## 최근 완료 (2026-04-17)

**브랜치 `feature/custom-tool-credentials` — 커스텀 도구 credential 통합 (백로그 B)**
- Backend: `update_tool_auth_config`가 CUSTOM 타입 허용 + owner 체크 (MCP/CUSTOM 둘 다)
- Backend 테스트: `test_update_custom_tool_credential` / `test_update_custom_tool_unset_credential` 신규 2건 + IDOR 의미 반전 테스트 1건 갱신
- Frontend: 신규 `custom-auth-dialog.tsx` (provider 필터 없음, 모든 credential 노출)
- Frontend: `add-tool-dialog.tsx` 커스텀 탭 inline `customAuthType`/`customApiKey` 제거 + `CredentialSelect` 통합
- Frontend: `tools/page.tsx` ToolCard isCustom 분기에 "인증 설정" 버튼 + 상태 배지 (Prebuilt와 동일 UX)
- Frontend: `add-tool-dialog.test.tsx` 신규 credential UI에 맞게 갱신 (11/11 PASS)
- i18n: `tool.customAuth.*` 5개 key 추가
- DB 마이그레이션: 없음 (기존 `tool.credential_id` 컬럼 재사용)
- 검증: backend ruff PASS, pytest **539 passed**; frontend lint PASS (0 errors, 1 pre-existing warning), build PASS (14 routes, 3.8s)

**PR #47 머지** — MCP 서버 단위 그룹화 + 서버 단위 인증 (`8dee7e0`)
- Backend: `GET/PATCH/DELETE /api/tools/mcp-servers[/{id}]` 신규 라우트 3개
- Backend: `chat_service.build_tools_config` MCP precedence 분리 (server-level만, tool-level 무시)
- Backend 보안: `ToolResponse.auth_config` 마스킹 (`***`) + round-trip 거부 validator
- Frontend: `MCPServerGroupCard`(자체 Collapsible) + 서버 단위 Auth/Rename 다이얼로그
- DB 마이그레이션: 없음

**PR #46 머지 완료 (이전)** — 중앙 크리덴셜 관리 (n8n 스타일, Fernet 암호화, `/connections` 페이지)

## 다음 작업 — credentials list N+1 복호화 제거 (백로그 C)

- 현재 `GET /api/credentials`는 행마다 Fernet 복호화로 field key 목록을 조회 → N+1
- 해결: `credentials.field_keys`에 비암호화 캐시 컬럼 추가 (값은 여전히 암호화)
- Alembic 마이그레이션 필요 (`field_keys: ARRAY[String]` 또는 `JSONB`)
- 작성/갱신 시 cache 동기화 (`credential_service.create_credential`, `update_credential`)
- 목록 응답 스키마는 그대로 (캐시는 내부 최적화)

**참조 파일**:
- `backend/app/services/credential_service.py` — 현재 list/get 로직
- `backend/app/models/credential.py` — 컬럼 추가 위치
- `backend/alembic/versions/` — 새 마이그레이션 파일

## 백로그 (추천 순서)

| # | 항목 | 규모 | 비고 |
|---|------|------|------|
| **C** | **credentials list N+1 복호화 제거** | 작음 | `field_keys` 캐시 컬럼 (Alembic 필요) |
| D | `lazy="joined"` → `selectinload` 전환 | 중 | 범용 성능 개선 |
| E | PREBUILT 공유 행 per-user credential binding | 큼 | 아키텍처 변경 (PoC라 우선순위 낮음) |
| F | `CredentialPickerDialog` 공통 셸 추출 | 중 | prebuilt/mcp-server/custom auth 다이얼로그 3개 중복 제거 |

## 주의사항

- **ENCRYPTION_KEY 필수** — `.env`에 설정 (없으면 503). `0YHrH9wDgLoJ...JYM=` 사용 중 (이 키 백업/관리 필요)
- **pre-existing 깨진 테스트** — `tests/components/chat/*`, `tests/pages/chat.test.tsx`, `tests/pages/agent-*` (별도 정리 필요)
- `.claude/worktrees/` 는 .gitignore 추가됨 (PR #46)
- 보안 마스킹 sentinel `***` 는 PATCH로 다시 보내면 422 — UI는 sentinel 값을 다시 제출하지 않음
- 백로그 B 작업 중 발견된 패턴: 의미 반전 변경 시 매칭되는 기존 테스트를 keyword grep으로 먼저 찾는 것이 효율적 (예: `non_prebuilt`, `not in.*PREBUILT`)
- `AddToolDialog`는 `CredentialFormDialog`를 MCP/custom 두 탭이 공유 — `credentialTarget: 'mcp' | 'custom'` state로 분기

## 관련 파일 (백로그 C 작업 시)

| 목적 | 경로 |
|------|------|
| 크리덴셜 서비스 (list/get) | `backend/app/services/credential_service.py` |
| 크리덴셜 모델 | `backend/app/models/credential.py` |
| 크리덴셜 라우터 | `backend/app/routers/credentials.py` |
| 응답 스키마 | `backend/app/schemas/credential.py` |
| Alembic 마이그레이션 | `backend/alembic/versions/` |
| 크리덴셜 테스트 | `backend/tests/test_credentials*.py` |

## 마지막 상태

- **브랜치**: `feature/custom-tool-credentials` (커밋 미완료, working tree에 변경 12파일 + 신규 1파일)
- **최근 main 커밋**: `8dee7e0` Merge pull request #47 (백로그 B 작업의 base)
- **검증**: Backend ruff PASS, pytest **539 passed** (신규 2건); Frontend `lint`/`build` PASS, `add-tool-dialog.test.tsx` 11/11 PASS
- **DB 상태**: 스키마 변경 없음 (`alembic upgrade head` 변동 없음)
- **PR 준비**: 단일 커밋으로 묶어 `feat(tools): custom 도구 credential 통합` 권장 (백엔드 + 프론트 함께 머지되어야 의미 있음)
