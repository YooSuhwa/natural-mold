# CHECKPOINT — System LLM Settings (ADR-019)

worktree: `.claude/worktrees/system-llm-settings` (branch `feature/system-llm-settings`)
backend cwd: `.../system-llm-settings/backend`, frontend cwd: `.../system-llm-settings/frontend`
ADR: `docs/design-docs/adr-019-system-llm-settings.md`

## M1: DB — system_llm_settings 테이블 + Alembic M45
- [ ] `app/models/system_llm_setting.py` 신규 (role UNIQUE, credential_id FK SET NULL nullable, model_name nullable, updated_at)
- [ ] `models/__init__.py` export
- [ ] Alembic `m45_system_llm_settings.py` — create table + CHECK(role IN ...) + 3 role seed row (NULL cred/model)
- 검증: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
- done-when: 마이그레이션 왕복 성공
- 상태: pending

## M2: resolver — resolve_system_model(role)
- [ ] `system_credential_resolver.py`에 `resolve_system_model(db, role) -> ResolvedSystemModel(provider, model_name, api_key, base_url)`
- [ ] `SystemModelNotConfiguredError(role)` (credential_id 또는 model_name NULL 시)
- [ ] credential payload에서 base_url 추출
- 검증: `uv run pytest tests/ -k system_llm -q`
- done-when: 신규 단위테스트 통과
- 상태: pending

## M3: 배선 — builder/assistant/image 호출부 교체
- [ ] `assistant_agent.py` → text_primary, base_url 전달
- [ ] `builder/sub_agents/helpers.py` _get_builder_model→text_primary, _get_fallback_model→text_fallback, @functools.cache 제거
- [ ] `image_service.py` + `builder_v3/image_gen.py` → image role
- 검증: `uv run ruff check . && uv run pytest -q`
- done-when: 회귀 0, ruff 통과
- 상태: pending

## M4: API — system-llm-settings 라우터 (super_user)
- [ ] `routers/system_llm_settings.py`: GET (3 role), PUT /{role}
- [ ] credential이 is_system=True LLM credential인지 검증 (없음/권한없음 응답 통일)
- [ ] `schemas/system_llm_setting.py` + main.py 등록
- 검증: `uv run pytest tests/ -k system_llm -q`
- done-when: API 테스트 통과, require_super_user 가드 확인
- 상태: pending

## M5: 프론트 — System LLM 설정 화면
- [ ] api client + TanStack Query hooks
- [ ] 운영자 메뉴에 화면 추가 (System Credentials 동일 권한)
- [ ] 슬롯 3개: credential select → discover-models → model select → 저장
- 검증: `pnpm build && pnpm lint`
- done-when: 타입체크/빌드 통과
- 상태: pending

## M6: 통합 검증
- [ ] backend: `uv run ruff check . && uv run pytest`
- [ ] frontend: `pnpm build && pnpm lint`
- done-when: 전체 그린
- 상태: pending
