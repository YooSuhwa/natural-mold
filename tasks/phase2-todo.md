# Phase 2 — 레이어링·경계 (refactor/phase2-layering, base f27e3453)

> 상세 방안: docs/refactoring-plan-2026-07.md §4(BE-S2·S7)·§6(BE-D1·D2·D4)
> 원칙: 항목당 커밋, 기능 변화 0, 트랜잭션 정책 = 서비스 flush / 라우터 commit(기존 create 패턴)

- [ ] BE-S2a: `services/mcp_service.py` 신설 — routers/mcp.py(825줄, raw DB 28건)의 `_load_owned`/`_load_tools_for`/CRUD/import·export DB 조작부 이동. 라우터는 서비스 호출+스키마 변환+가드만. `_invalidate_runtime_mcp_cache`/`_record_mcp_audit` 사이드이펙트도 서비스로
- [ ] BE-S2b: `tool_service.py` 확장 — routers/tools.py의 create(db.add+commit :135-136)·run_tool_endpoint(:251) 로직 이동
- [ ] BE-S2c: `model_service.py` 신설 — routers/models.py의 in-use 체크(:254 count) 등 이동
- [ ] BE-S7: `credentials/oauth_service.py` 신설 — routers/credentials.py의 OAuth ~286줄(`_prepare_mcp_oauth_data` :491, `_persist_credential_payload` :574, `_gc_oauth_states` :584, `oauth2_auth_start` :617, `oauth2_callback` :708) 이동. `start_oauth(db,*,user,credential_id)`/`handle_callback(db,*,code,state)` 시그니처. mcp_oauth_client=저수준 HTTP, oauth_service=DB 오케스트레이션 역할 정리
- [x] BE-D2 ✅ def12f1b: raw `HTTPException(404|403)` 24곳 → error_codes 팩토리 치환 (credentials.py:110,247,422,735 / models.py:282,324,110 / mcp.py:99,357,365 / tools.py:73 / health.py:226,229 등). 미존재 팩토리만 소량 추가. 프론트 detail 문자열 의존 여부 grep 확인
- [ ] BE-D1: conversation 계열 소유권 3줄 블록 30곳 → `Depends(owned_conversation)` (dependencies.py 팩토리, 404 단일 응답 봉인) → 검증 후 agents 6곳 `owned_agent` 확산
- [ ] BE-D4: system-or-owned 술어 7곳 → `Tool.visible_to(user_id)` 모델 헬퍼 (제네릭 load_owned 금지)
- [x] (S) ✅ 15b24557 integration 워커 테스트 2s 타임아웃 완화 (tests/integration/test_conversation_run_lifecycle.py — CI 플레이크)

검증: 항목별 `uv run pytest tests/test_<도메인>*.py` + 최종 `SKILL_EVALUATION_ENABLED=true uv run --with pytest-xdist pytest -q -n 4` + ruff + 수정 파일 pyright. PR은 main 대상.
