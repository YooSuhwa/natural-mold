# 작업 인계 문서

## 최근 완료 (2026-04-18)

**브랜치 `feature/connections-table` — 백로그 E M1: Connections 테이블 + CRUD API** — worktree `.claude/worktrees/backlog-e-m1`
- 신규 `connections` 테이블 (Alembic `m8_add_connections`) — user_id NOT NULL, type/provider_name/display_name, credential_id FK ON DELETE SET NULL, extra_config JSON, is_default, status, 인덱스 `(user_id, type, provider_name)`. UNIQUE 제약 없음 (ADR-008)
- 신규 `app/models/connection.py`, `app/schemas/connection.py` (Pydantic validator: PREBUILT는 credential_registry enum 5종, MCP/CUSTOM은 `^[a-z0-9_]+$`, MCP extra_config.url+auth_type 필수)
- 신규 `app/services/connection_service.py` — list/get/create/update/delete + `is_default` 원자 토글 (첫 connection 강제 default, 기존 default 자동 해제)
- 신규 `app/routers/connections.py` — `/api/connections` CRUD (GET/POST/PATCH/DELETE, user_id 필터 get_current_user)
- 신규 `backend/tests/test_connections.py` — ADR-008 §M1 8 시나리오 (CRUD, MCP validator, PREBUILT validator, is_default 자동/토글, IDOR, SET NULL, extra_config 타입 불일치)
- **parallel run**: chat_service, executor, tool 해석 경로는 건드리지 않음. 기존 시스템 영향 0
- PATCH 시맨틱: `ConnectionUpdate`에 `extra="forbid"` + 서비스가 `model_dump(exclude_unset=True)` 사용 → `credential_id=None` / `extra_config=None` 명시 전송은 해제로 반영, 미전송은 기존 값 보존. `type` 변경 시도는 extra field로 422
- **credential cross-tenant 방어**: `create_connection` / `update_connection` 모두 `credential_id` 설정 전 `credential_service.get_credential(db, cred_id, user_id)` 호출로 소유권 검증. 타 유저의 credential 참조 시 404 (Codex 리뷰 P1-1)
- **single-default invariant 보강**: PATCH로 `provider_name`이 바뀌어 scope가 이동할 때도 (업데이트된) 현재 scope에서 자기 제외 default 원자 해제 (Codex 리뷰 P1-2)
- **PATCH 재검증**: `update_connection`이 commit 전 `ConnectionCreate`로 post-update 상태를 재검증 → POST가 거부할 조합(prebuilt+non-enum, mcp+extra_config 누락 등)을 PATCH로 우회 불가. Pydantic `ValidationError`는 `HTTPException(422, errors(include_url=False, include_context=False))`로 변환 (main.py:208 핸들러 버그 로컬 회피) (Codex 리뷰 P2)
- **default orphan 방지 (대칭)**: PATCH/DELETE로 default가 고립된 scope는 `_promote_default_if_orphaned`가 가장 최근 row를 자동 승격. update_connection은 **옛 scope와 새 scope 양쪽에 호출**해 rename-out/rename-to-empty 모두 커버 (헬퍼 idempotent). POST의 "첫 row 자동 default"와 대칭. ADR-008 §5 invariant "scope에 row가 있으면 default도 있다" 자체 강제 (Codex 2차·3차 리뷰 P2)
- **global 422 handler 수정**: `main.py:208` validation_error_handler가 `jsonable_encoder(exc.errors())` 사용하도록 변경 → Pydantic ctx.error(ValueError) 직렬화 실패로 500이 나던 잠복 버그 해소. field_validator/model_validator 기반 422 경로 전체 정상화 (Codex adversarial Finding 1)
- **partial unique index (default race 방어)**: Alembic `m8`에 `uq_connections_one_default_per_scope` `(user_id, type, provider_name)` WHERE `is_default=true`. 동시 요청이 같은 scope에 default=true로 insert/update 하려 하면 DB가 거부 → 서비스가 `IntegrityError`를 409로 변환. `_clear_default_in_scope`를 in-memory mutation 이전으로 재배치해 자체 autoflush 충돌도 회피. PG/SQLite 호환 (Codex adversarial Finding 2)
- **`extra_config` 평문 시크릿 채널 제거**: `ConnectionExtraConfig` Pydantic 모델로 strict typing (url/auth_type/headers/env_vars/transport/timeout, `extra="forbid"`). **`env_vars` 값은 `${credential.<field>}` 템플릿만 허용** — 평문 문자열은 422. PREBUILT/CUSTOM은 extra_config 자체를 422로 거부 (비밀은 credential 경유). ADR-008 §2 업데이트 (Codex adversarial Finding 3)
- 검증: ruff PASS, **pytest 572 passed** (545→572, +27 신규 시나리오 포함), alembic upgrade/downgrade/upgrade 왕복 PASS

**알려진 이슈 (M1 스코프 외, 별도 후속)**:
- `backend/app/main.py:208-221` `validation_error_handler`가 `RequestValidationError.errors()`의 `ctx.error` (ValueError 객체)를 JSONResponse에 직접 실어 JSON 직렬화 실패 → field_validator/model_validator 기반 422 경로가 500으로 깨짐. 기존 테스트에 이 경로가 없어 잠복. connections validator 도입으로 처음 노출
- **영향**: 모든 Pydantic field_validator/model_validator의 API 계층 422 응답
- **권고 수정**: `jsonable_encoder(exc.errors())` 또는 `exc.errors(include_context=False)`
- **현재 우회**: S4 시나리오 2/3을 `pytest.raises(ValidationError)` 스키마 직접 검증으로 작성. API HTTP 422 회귀 테스트는 핸들러 수정 후 추가 필요
- **원칙**: drive-by 금지로 M1에서 수정 안 함. 별도 소규모 PR로 처리

**aiosqlite FK 테스트 패턴 (S4 학습, M2~M4에서 재사용)**:
- ON DELETE SET NULL/CASCADE 실제 SQL 동작 검증은 **전용 engine(StaticPool + PRAGMA foreign_keys=ON connect listener)** + 별도 `async_sessionmaker`를 테스트 로컬에 구성할 것
- 공유 `conftest` engine의 PRAGMA를 건드리면 다른 테스트 FK violation
- ORM identity map 때문에 SET NULL 검증 시 `session.expire_all()` 또는 재-select 필수

**이전 머지**: PR #52 (ADR-008 + M0), PR #51 (exec-plan), PR #50 (백로그 재정렬), PR #49 (백로그 C), #48/#47/#46

## 다음 작업 — 백로그 E **M2**: MCP → Connection 이관

**루트 계획**: `docs/exec-plans/active/backlog-e-connection-refactor.md` (M2 섹션)
**ADR**: `docs/design-docs/adr-008-connection-entity.md` §6 이행 전략

**M2 스코프** (요약):
- Alembic `m9_migrate_mcp_to_connections`: 기존 `mcp_servers` 각 row → `connections` (type='mcp', extra_config에 url/auth_type/headers/env_vars 이관)
- `tools.connection_id` 컬럼 추가 (nullable FK)
- `tools.mcp_server_id` 기준 `tools.connection_id` 매핑
- `chat_service.build_tools_config` MCP 분기를 connection 경유로 재작성
- `mcp_servers` 테이블은 **deprecate (read-only)** 유지 (M6에서 drop)
- 기존 `test_mcp_connection`, `test_tools_router_extended` 회귀 통과

**새 세션 진입 (M2)**:
```
docs/exec-plans/active/backlog-e-connection-refactor.md (M2 섹션) 와 docs/design-docs/adr-008-connection-entity.md 읽고 M2 시작해줘.
```

**M0 완료 (PR #52 머지됨)** — 브랜치 `docs/adr-008-connection-entity`
- ADR-008 작성: 맥락/결정/대안(A~D)/결과 + 테스트 시나리오
- exec-plan 보강: M0 합의 사항 요약표 추가
- design-docs index 갱신

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
