# Phase 0 — 안전망 (refactor/phase0-safety-net)

> 근거 문서: docs/refactoring-plan-2026-07.md §1(P0)·§3(보안 추적)·§9(IX-1)
> 원칙: 항목당 커밋 1개, 기능 변화는 수정 대상에 한정, 각 항목 검증 그린 후 체크

## 착수 전 확인
- [ ] 스코프 확정 (사용자 확인): Phase 0 전체 6건 vs 분할

## 항목

### SEC-1: web_scraper SSRF 차단 (P0, S~M)
- [ ] `tool_factory.py` scrape_url — scheme http/https 제한 + resolve된 IP private/loopback/link-local 거부
- [ ] 리다이렉트 hop마다 재검증 (follow_redirects 수동 처리 또는 event hook)
- [ ] 응답 크기 상한
- [ ] 테스트: 169.254.169.254 / localhost / 사설 IP 리다이렉트 차단 pytest
- 검증: `uv run pytest tests/ -k scraper` + ruff + pyright

### SEC-2: rotate_credentials no-progress 루프 가드 (P0, S)
- [ ] `scheduler.py:235-251` — 실패 id 제외 축적 또는 no-progress break + max-iter 캡
- [ ] 테스트: 복호 불가 credential 대량 시드 후 잡 종료 확인
- 검증: `uv run pytest tests/test_scheduler*.py tests/test_credentials*.py`

### SEC-3: 트리거 run-now 중복실행 가드 (P0, S~M)
- [ ] `execute_trigger` 진입 시 status→running 클레임 (`SELECT … FOR UPDATE` 또는 부분 유니크 인덱스)
- [ ] run-now 라우터: 이미 running이면 409 (error_codes 팩토리 경유)
- [ ] 동시성 테스트 (PG 필요 시 integration 마커)
- 검증: `uv run pytest tests/test_trigger*.py`

### BE-P4: bcrypt 이벤트 루프 블로킹 해소 (P0, S)
- [ ] `auth_service.py:84,113,131` — verify/hash를 `asyncio.to_thread`로 오프로드
- [ ] 타이밍 패드(:113) 경로도 동일 처리
- 검증: `uv run pytest tests/test_auth*.py` 전체 그린

### FE-D1: 라우트 에러 바운더리 (P0, M)
- [ ] `app/global-error.tsx` + `app/error.tsx` 추가
- [ ] `app/agents/error.tsx`, `app/shared/error.tsx` (reset 버튼 + i18n)
- [ ] 기존 5개 error.tsx와 톤 통일 (공용 RouteError 프리미티브 검토)
- 검증: `pnpm vitest run` + `pnpm build` + 의도적 throw 확인

### IX-1: CI 파이프라인 도입 (P0, M)
- [ ] `.github/workflows/ci.yml` — backend(ruff+pyright+pytest -n 4) / frontend(lint+vitest+build) 2-job, uv/pnpm 캐시, path filter
- [ ] PR에서 두 job 그린 확인
- 검증: 게이트 작동 확인

## 완료 조건
- [ ] 전체 검증: backend `ruff + pyright + pytest -n 4` / frontend `lint + vitest run + build` 그린
- [ ] docs/refactoring-plan-2026-07.md 매트릭스에 완료 표시 갱신
- [ ] 푸시 시 `SKILL_EVALUATION_ENABLED=true` (pre-push pytest)
