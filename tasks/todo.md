# Stage 3 — 갓 모듈 분해 (한 PR, 항목별 커밋 + 항목별 리뷰)

브랜치: `refactor/stage3-god-modules` (origin/main = 5c7a6c01 기준)
원칙: 기능 변화 0, 순수 이동 + facade re-export, 항목당 작은 커밋, 항목 완료마다 code-review → 수정 커밋 → 다음 항목.
검증(항목별): `ruff check` + 타깃 pytest → 커밋 전 `SKILL_EVALUATION_ENABLED=true pytest -q -n 4 --ignore=tests/integration`
최종: integration 직렬(`-m integration` 필수) + 푸시 시 `SKILL_EVALUATION_ENABLED=true`

- [x] 0. 기준선: 2702 passed / 5 failed = 전부 SKILL_EVALUATION_ENABLED=false 기인(플래그 켜면 통과) → 실질 그린
- [x] 1. BE-S1 — chat_service.py(1810줄→104줄 facade) → `app/services/chat/` 7모듈 분해 (a901b319)
  - [x] 구현 커밋 → [x] 리뷰: 승인, 발견 0 (AST 전수 대조로 순수 이동 확증, 함수-로컬 import 4곳 유지 사유 실증) → 수정 불필요
- [ ] 2. BE-S3 — install_service.py(1365줄) → `app/marketplace/install/` 분해 + facade 디스패처
  - snapshot / bindings / skill / mcp / agent_blueprint
  - [ ] 구현 커밋 → [ ] 리뷰 → [ ] 수정 커밋
- [ ] 3. BE-S5 — write_tools.py(1091줄) → 그룹별 서브 빌더 분해 (tool_links / composition / agent_config / cron)
  - [ ] 구현 커밋 → [ ] 리뷰 → [ ] 수정 커밋
- [ ] 4. BE-S8 — artifact_service.py(1035줄) → `app/services/artifacts/` (recorder / library / content / summary) + facade
  - [ ] 구현 커밋 → [ ] 리뷰 → [ ] 수정 커밋
- [ ] 5. BE-S9 — scheduler.py(805줄) 잡 로직 → 도메인 서비스 이관, scheduler는 등록만
  - [ ] 구현 커밋 → [ ] 리뷰 → [ ] 수정 커밋
- [ ] 6. BE-S10 — runtime_component_builder.py(930줄) → `agent_runtime/runtime/` 5-관심사 분해
  - models / reliability / interrupts / prompts / memory_context
  - [ ] 구현 커밋 → [ ] 리뷰 → [ ] 수정 커밋
- [ ] 7. 최종 전체 검증 (ruff + full pytest + integration 직렬) + 플랜 문서 갱신
- [ ] 8. 푸시 (SKILL_EVALUATION_ENABLED=true) + PR 생성
