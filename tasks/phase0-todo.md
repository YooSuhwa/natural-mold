# Phase 0 — 안전망 (refactor/phase0-safety-net) ✅ 완료

> 근거 문서: docs/refactoring-plan-2026-07.md §1(P0)·§3(보안 추적)·§9(IX-1)
> 원칙: 항목당 커밋 1개, 기능 변화는 수정 대상에 한정

## 착수 전 확인
- [x] 스코프 확정 (사용자 확인): Phase 0 전체 6건, 항목별 커밋 + PR 1개

## 항목

### SEC-1: web_scraper SSRF 차단 — ✅ 41f4e4a9
- [x] `url_guard.py` 신설 — scheme http/https 제한 + literal/resolved IP non-global 거부 (getaddrinfo non-blocking)
- [x] 수동 리다이렉트 최대 5 hop, hop마다 재검증 (공유 클라이언트 설정 불변)
- [x] 스트리밍 바디 2MB 상한 (청크 내부 절단 포함 — 테스트가 결함 잡음)
- [x] tests/test_web_scraper_ssrf.py 18케이스 (metadata/loopback/RFC-1918/CGNAT/IPv6/file/redirect/cap)

### SEC-2: rotate_credentials no-progress 루프 가드 — ✅ 734f920a
- [x] 실패 id 축적 → 다음 fetch에서 `notin_` 제외 (진행 보장으로 종료 증명)
- [x] 회귀 테스트: 배치=2·실패 3행에서 유한 종료 (wait_for 타임아웃 가드)

### SEC-3: 트리거 run-now 중복실행 가드 — ✅ 75d48655
- [x] execute_trigger 진입 시 트리거 행 FOR UPDATE + non-stale running run 클레임 체크
- [x] run-now는 `TRIGGER_ALREADY_RUNNING`(409, error_codes 팩토리), 스케줄 경로는 조용히 스킵
- [x] 스테일 바운드 1h — 크래시로 running 고착된 run이 영구 차단하지 않음
- [x] 테스트 3건 (스킵/409/스테일 무시)

### BE-P4: bcrypt 이벤트 루프 블로킹 해소 — ✅ c64ce4e0
- [x] auth_service 3곳(register hash, 타이밍 패드 verify, 로그인 verify) `asyncio.to_thread`
- [x] 시드(e2e_user)는 부팅 1회라 유지 (Minimal Impact)

### FE-D1: 라우트 에러 바운더리 — ✅ 8cc7f430
- [x] `app/error.tsx`, `app/agents/error.tsx`, `app/shared/error.tsx` (기존 ErrorState 패턴 재사용)
- [x] `app/global-error.tsx` — 루트 레이아웃 대체라 globals.css 직접 import + 정적 영문 카피(i18n 프로바이더 부재)

### IX-1: CI 파이프라인 — ✅ 6fd40da4
- [x] `.github/workflows/ci.yml` — backend(ruff+pytest -n 4) / frontend(lint+vitest+build) 필수 2-job
- [x] backend-typecheck 잡은 non-blocking — **전체 pyright 968 기존 에러** (초기 "통과" 판단은 파이프라인 exit 코드 착오였음; 계획 문서 교정 완료)
- [ ] PR에서 잡 그린 실측 확인 (PR 생성 후)

## 완료 조건
- [x] backend: ruff clean / 수정 파일 pyright clean / **전체 pytest 2,558 통과** (1회 test_default_image_skill_seed 병렬 플레이크 — 단독·xdist 재실행 통과, 변경 무관)
- [x] frontend: lint(에러 0, 기존 경고 4) / vitest 1,230 통과 / build 성공
- [x] docs/refactoring-plan-2026-07.md 매트릭스 완료 표시 + pyright 오류 교정
- [ ] 푸시(`SKILL_EVALUATION_ENABLED=true`) + PR

## 잔여/후속
- pyright 968 에러 번다운 트랙 (별도)
- IX-2(pre-commit)는 계획 문서 재평가 필요: husky+lint-staged가 이미 staged ruff/eslint 실행 중임을 커밋 과정에서 확인 — 사실상 커버됨
