# Stage 3 — 갓 모듈 분해 (한 PR, 항목별 커밋 + 항목별 리뷰)

브랜치: `refactor/stage3-god-modules` (origin/main = 5c7a6c01 기준)
원칙: 기능 변화 0, 순수 이동 + facade re-export, 항목당 작은 커밋, 항목 완료마다 code-review → 수정 커밋 → 다음 항목.
검증(항목별): `ruff check` + 타깃 pytest → 커밋 전 `SKILL_EVALUATION_ENABLED=true pytest -q -n 4 --ignore=tests/integration`
최종: integration 직렬(`-m integration` 필수) + 푸시 시 `SKILL_EVALUATION_ENABLED=true`

- [x] 0. 기준선: 2702 passed / 5 failed = 전부 SKILL_EVALUATION_ENABLED=false 기인(플래그 켜면 통과) → 실질 그린
- [x] 1. BE-S1 — chat_service.py(1810줄→104줄 facade) → `app/services/chat/` 7모듈 분해 (a901b319)
  - [x] 구현 커밋 → [x] 리뷰: 승인, 발견 0 (AST 전수 대조로 순수 이동 확증, 함수-로컬 import 4곳 유지 사유 실증) → 수정 불필요
- [x] 2. BE-S3 — install_service.py(1365줄→400줄 facade+디스패처) → `app/marketplace/install/` 분해 (커밋 2번째)
  - common / snapshot / bindings / skill / mcp / agent_blueprint. 추출 seam 2곳(skill create/overwrite)은 문장 단위 AST 동일 검증
  - [x] 구현 커밋 → [x] 리뷰: 승인, 발견 0 → 수정 불필요
- [x] 3. BE-S5 — write_tools.py(1091줄) → `write_tools/` 패키지 + WriteToolContext (9eee9bea)
  - 23개 도구 schema 바이트 동일, patch 표면(async_session_factory) call-time 주입으로 보존
  - [x] 구현 커밋 → [x] 리뷰: 승인, 발견 0 → 수정 불필요
- [x] 4. BE-S8 — artifact_service.py(1035줄→137줄 facade) → `app/services/artifacts/` recorder/library/content/summary/errors (1e7d519f)
  - [x] 구현 커밋 → [x] 리뷰: 승인, Low 1건(hot path call-time facade import — patch 계약 보존 위한 의도적 설계, 수정 불필요 판정)
- [x] 5. BE-S9 — scheduler.py(805→706줄) 인라인 잡 4건 → credentials/rotation·mcp_service·conversation_run_service·skill_runtime (ccf4d25b)
  - 동명 wrapper 잔존(영속 jobstore module:qualname + 테스트 patch 표면 call-time 주입 보존)
  - [x] 구현 커밋 → [x] 리뷰: 승인, 차단 0 (정보성 1: _DATA_DIR import-time 바인딩 — 기능 무영향 판정)
- [x] 6. BE-S10 — runtime_component_builder.py(930→600줄) → `agent_runtime/runtime/` models/reliability/interrupts/prompts/memory_context (9fa1facc)
  - monkeypatch 12종 투명성 보존(create_chat_model만 call-time builder import 패턴), executor facade 무수정
  - [x] 구현 커밋 → [x] 리뷰: 승인, 발견 0
- [x] 7. 최종 전체 검증: ruff 0 / full 2707 passed / integration 29 passed·1 skipped / frontend vitest 1294 passed + 플랜 문서 ✅ 갱신
- [x] 8. 푸시 + **PR #296** 생성 (pre-push 게이트: 워크트리 node_modules에 diff@9 미설치가 원인이던 vitest 실패는 클린 pnpm install로 해결 — 코드 무관)
