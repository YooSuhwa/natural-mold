# 작업 인계 문서

## 최근 완료 (2026-04-18)

**PR 대기 — 백로그 E M2: MCP → Connection 이관** · worktree `.claude/worktrees/backlog-e-m2` / 브랜치 `feature/connection-mcp-migration` / base main@`29678a1`

핵심 변경:
- Alembic `m9_migrate_mcp_to_connections`: `mcp_servers` → `connections(type='mcp')` 이관, `tools.connection_id` 추가, `_m9_migrated_connections` 추적 테이블, credential-backed server의 env_vars 템플릿 자동 생성 (복구 불가 시 legacy path 유지)
- `chat_service.build_tools_config` MCP 분기 재작성: `tool.connection_id` → connection 경유, legacy는 `resolve_server_auth` 재사용, `mcp_transport_headers`는 top-level 필드로 분리
- 신규 `app/services/env_var_resolver.py`: `${credential.<field>}` 해석 + `ToolConfigError(AppError)` + ownership 가드
- 스키마 write/read 분리: `ConnectionExtraConfig`(strict, template-only) vs `ConnectionExtraConfigResponse`(env_var_keys/header_keys만 노출, secret 은닉)
- executor `_build_mcp_tools`: `(url, sha256(headers)[:8])` deterministic key
- `update_connection` 강화: credential 소유권 검증 + PATCH invariant 재검증 (credential_id 변경 포함)
- `_commit_or_409` 헬퍼 + `_promote_default_if_orphaned` SQL-native

검증: ruff PASS / **pytest 604 passed** (+32) / Alembic PG 왕복 PASS / 1 integration deselected

Codex adversarial 8라운드 + /simplify 3-agent 리뷰 전부 반영 (cross-tenant leak, 500 handler, partial unique, env_vars/headers 응답 redact, credential-backed auth 복구, deterministic key 등)

**이전 머지**: PR #53(M1), #52(M0), #51/#50/#49

## 다음 작업 — **M3: PREBUILT per-user Connection**

**ADR**: `docs/design-docs/adr-008-connection-entity.md` §3
**계획**: `docs/exec-plans/active/backlog-e-connection-refactor.md` (M3)

스코프:
- PREBUILT 해석: `tool.provider_name + current_user_id` → `connections` default 조회
- `tools.credential_id`는 PREBUILT에서 무시 (M6까지 legacy fallback)
- env fallback (`settings.naver_*`): 시스템 내부만, **유저 경로 제거**
- Frontend `/connections`에서 PREBUILT connection 생성 지원
- **핵심 검증**: mock user 2명이 같은 PREBUILT 도구 → 각자 다른 credential 사용 (공유 행 뒤엉킴 해소)

새 세션 진입:
```
docs/exec-plans/active/backlog-e-connection-refactor.md (M3) + adr-008 읽고 M3 시작. worktree/브랜치 새로.
```

## 마일스톤 진행

| M0 ADR | M1 테이블+CRUD | M2 MCP 이관 | M3 | M4 | M5 | M6 |
|---|---|---|---|---|---|---|
| PR #52 | PR #53 | **PR 대기** | 다음 | | | |

## 주의사항 (M3+ 재사용)

- **ENCRYPTION_KEY 필수** (없으면 credential create 503)
- **M1/M2 학습**:
  - aiosqlite FK 테스트: 전용 engine(StaticPool+PRAGMA) + 별도 session, `session.expire_all()`로 identity map 회피
  - `is_default` 관련 UPDATE는 ORM mutation 이전에 실행(autoflush 충돌)
  - PG 전용 SQL 마이그레이션은 unit-level 격리 + `tests/integration/` (marker) 분리
  - 응답 스키마: write(strict) / read(tolerant) 분리. `model_validator(mode="before")`에서 입력 dict **복사 후** mutate
  - deterministic key: `hashlib.sha256(json.dumps(..., sort_keys=True)).hexdigest()[:8]` (`hash()`는 PYTHONHASHSEED 의존)
  - `HTTPException.detail=exc.errors()`는 UUID 있으면 직렬화 실패 → `jsonable_encoder` 필요
  - `auth_config`는 executor interceptor가 매 tool call args에 주입 → transport 메타는 별도 top-level 필드
  - credential 소유권 가드는 런타임(build_tools_config)에도 배치
- **Alembic downgrade**: `op.execute("DROP INDEX IF EXISTS …")` + `information_schema` 존재 체크
- **SQLite batch**: `drop_column`은 `op.batch_alter_table` 래핑
- pre-existing 깨진 프론트 테스트: `tests/components/chat/*`, `tests/pages/chat.test.tsx`, `agent-*`

## 마지막 상태

- 브랜치: `feature/connection-mcp-migration` @ `2f3b1d2` (single feat, push 대기)
- Base: main @ `29678a1` (PR #53 머지)
- DB head: `m9_migrate_mcp_to_connections`
- 보존 worktree: `.claude/worktrees/backlog-e-m1` (M1, 정리 가능)
