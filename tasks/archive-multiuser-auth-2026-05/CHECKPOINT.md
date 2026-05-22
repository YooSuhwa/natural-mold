# CHECKPOINT — Multi-User Authentication

**Project Owner**: 사티아 (Satya)
**Branch**: `feature/multiuser-auth`
**Plan**: `~/.claude/plans/replicated-crunching-lark.md` (approved)

---

## 핵심 결정사항 (User-Approved)

| 항목 | 선택 |
|------|------|
| 토큰 저장 | HttpOnly Cookie (Access + Refresh) + CSRF body token |
| 인증 알고리즘 | JWT HS256 — Access 1h, Refresh 30d (DB whitelist) |
| 비밀번호 해싱 | bcrypt (passlib) |
| 권한 모델 | `is_super_user` boolean 단일 플래그 |
| 테넌시 단위 | User-only (Workspace 확장 hook은 service layer 추상화로) |
| 인증 방식 | Email + Password (Google OAuth는 Phase 2 이연) |
| System credentials | super_user 전용 (조회/사용/관리 모두) |
| 첫 가입자 | 자동 super_user |

---

## M1: 사일로 셋업 (S0 + 삭제 분석 + 아키텍처)
- [ ] S0 (피차이): docs/design-docs/adr-016-multiuser-auth.md 작성
- [ ] S1 (베조스): tasks/deletion-analysis.md — Mock User 흔적, 시드 종속성, FK 정책 매트릭스
- [ ] S2 (피차이): User/RefreshToken 모델 + Alembic 마이그레이션 m22 스펙
- 검증: `test -f docs/design-docs/adr-016-multiuser-auth.md && test -f tasks/deletion-analysis.md`
- done-when: 아키텍처 결정 명문화 + 삭제 분석 보고서
- 상태: pending

## M2: 백엔드 인증 코어 (Phase 1 + 2 + 3)
- [ ] 젠슨: User 컬럼 추가, RefreshToken 모델, Tool/Credential `is_system`, FK ON DELETE 정리
- [ ] 젠슨: Alembic 마이그레이션 m22 작성 + 백필
- [ ] 젠슨: `app/auth/{password,jwt,cookies}.py` 모듈
- [ ] 젠슨: `dependencies.py` 재작성 (JWT-based `get_current_user`, `verify_csrf`, `require_super_user`)
- [ ] 젠슨: `routers/auth.py` (register, login, logout, refresh, me) + rate limiting
- [ ] 젠슨: `services/{auth_service,user_service}.py`
- 검증: `cd backend && uv run pytest tests/test_auth_*.py -v && uv run ruff check . && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
- done-when: 모든 인증 엔드포인트 동작, 마이그레이션 reversible, 테스트 통과
- 상태: pending

## M3: 시드 정리 + 라우터 audit (Phase 4 + 5)
- [ ] 젠슨: main.py mock user 자동 생성 제거
- [ ] 젠슨: bootstrap → system credentials (is_system=True)
- [ ] 젠슨: credential_service.list_for_user super_user 분기
- [ ] 젠슨: tool_factory 정책 (일반 user는 system credential 거부)
- [ ] 젠슨: templates/models mutation에 require_super_user
- [ ] 젠슨: 모든 mutation 라우터에 CSRF 검증
- 검증: `cd backend && uv run pytest tests/test_csrf.py tests/test_multiuser_isolation.py -v`
- done-when: 격리 매트릭스 7개 시나리오 모두 통과
- 상태: pending

## M4: 디자인 + 프론트엔드 인증 (Phase 7)
- [ ] 팀쿡: 로그인/회원가입/사이드바 디자인 스펙 (와이어프레임 + 컴포넌트 목록)
- [ ] 저커버그: `(auth)` route group + login/register 페이지
- [ ] 저커버그: `lib/auth/{csrf,session}.ts` + useAuth hook + AuthGuard
- [ ] 저커버그: `lib/api/client.ts` 수정 — credentials:include, CSRF, 401 auto-refresh deduplication
- [ ] 저커버그: `middleware.ts` — 보호 라우트 + login/register 양방향 redirect
- [ ] 저커버그: 사이드바/헤더 user 정보 + 로그아웃
- 검증: `cd frontend && pnpm build && pnpm lint`
- done-when: 빌드 통과 + 수동 로그인 플로우 동작
- 상태: pending

## M5: AI runtime 정비 + 마이그레이션 스크립트 (Phase 6 + 8)
- [ ] 젠슨: AgentConfig.user_id 필수화
- [ ] 젠슨: services/user_service.cleanup_user_resources (LangGraph checkpoint 정리)
- [ ] 젠슨: scripts/migrate_mock_to_real_user.py 작성
- 검증: `cd backend && uv run pytest tests/test_user_cleanup.py -v`
- done-when: 사용자 삭제 시 conversation/checkpoint까지 정리, 마이그레이션 스크립트 dry-run 성공
- 상태: pending

## M6: 통합 검증 + 보안 체크 (Phase 9)
- [ ] 베조스: multi-user 격리 매트릭스 자동 테스트 (7개 시나리오)
- [ ] 베조스: CSRF mutation 테스트
- [ ] 베조스: refresh token replay 감지 테스트
- [ ] 베조스: 보안 체크리스트 (cookie secure, CORS, JWT secret, OWASP)
- [ ] 베조스: tasks/lessons.md + docs/QUALITY_SCORE.md 업데이트
- 검증: `cd backend && uv run pytest -v && cd ../frontend && pnpm build && pnpm lint`
- done-when: 전체 테스트 그린 + 보안 체크 PASS
- 상태: pending

## M7: 운영 준비 + HANDOFF (Phase 10)
- [ ] 사티아: `.env.example` 업데이트 (JWT secret, cookie 설정 등)
- [ ] 사티아: CORS 운영 설정 강화
- [ ] 사티아: HANDOFF.md 작성
- [ ] 사티아: docs/ARCHITECTURE.md 멀티유저 섹션 추가
- 검증: `test -f HANDOFF.md && grep -q multiuser docs/ARCHITECTURE.md`
- done-when: 다음 세션이 컨텍스트 없이 이어받을 수 있음
- 상태: pending

---

## 🚦 마일스톤 의존 그래프

```
M1 (셋업)
 ├── M2 (백엔드 코어) ──┐
 │                      ├── M5 (AI runtime + 마이그)
 ├── M3 (시드 + audit) ─┤
 │                      │
 └── M4 (FE) ───────────┴── M6 (통합 검증) ── M7 (운영 + HANDOFF)
```

**병렬 가능**: M1 완료 후 M2/M3/M4를 병렬 진행 (파일 경계 분리)
**Critical Path**: M1 → M2 → M5 → M6 → M7
