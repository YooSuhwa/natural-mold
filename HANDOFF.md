# HANDOFF — Builder v2 정리 + 후속 작업

**최종 업데이트**: 2026-04-27
**Base**: `main @ 38961b6`
**열린 PR**: [#69 — Builder v2 코드 제거](https://github.com/YooSuhwa/natural-mold/pull/69) (review 대기)

---

## 이번 세션 (2026-04-27) 요약

### 완료
- [x] **PR #69 생성** — Builder v2 dead code 제거 (+4/-1812 lines, 8 files)
  - 삭제: `builder/orchestrator.py`, `test_builder_orchestrator.py`, `test_builder_service_stream.py`
  - 부분 제거: `routers/builder.py::stream_build` GET, `builder_service::run_build_stream` 외 v2 헬퍼 4종, 관련 테스트 17개
  - 보존: `sub_agents/*`, `prompts/*` (v3 import), `BuilderStatus.STREAMING` enum (DB 안전성), `confirm_build`의 v2/v3 image 분기
- [x] **검증** — pytest 624 passed (회귀 0, -47 v2 테스트), ruff clean, frontend tsc/lint/build 통과

### 검증 명령
```bash
cd backend && uv run ruff check . && uv run pytest
cd frontend && pnpm exec tsc --noEmit && pnpm lint && pnpm build
```

---

## 다음에 해야 할 작업

### 즉시
1. **PR #69 머지** — review 후 main 머지
2. **main sync** — `git checkout main && git pull` 후 worktree 정리

### 필수 (사용자 직접)
3. **브라우저 E2E 시나리오 검증** (PRD-screens 기반)
   - mockup 1~4 흐름
   - 수정 요청 분기 (recommendation/prompt approval)
   - 이미지 skip / regenerate
   - HiTL stale interrupt 처리

### 선택 (refactor)
4. **Tool UI HiTL form 헬퍼 추출** — `useApprovalForm` hook + `ApprovalCard` wrapper로 5종 approval 컴포넌트 ~40% 감소
5. **Frontend `buildStreamState` memoization** — SSE 이벤트 빈도 높을 때 re-render 비용 감소
6. **(선택) v2 enum 정리** — `BuilderStatus.STREAMING` 사용처 0이 된 후, DB 마이그레이션과 함께 enum 제거 가능

---

## 주의사항

- **`BuilderStatus.STREAMING` enum 보존 이유**: 운영 DB에 STREAMING 상태로 남아있을 수 있는 레코드 안전성. enum 제거하려면 마이그레이션 필요
- **`confirm_build`의 v2 image 분기 (`services/builder_service.py:208-214`)**: `image_url` 키 없으면 `image_service.generate_agent_image` 자동 호출. v3은 항상 키 포함하므로 dead code지만 HANDOFF 범위 외라 보존. PR #69 머지 후 별도 정리 권장
- **frontend는 변경 없음** — `stream-builder.ts`, `use-builder-runtime.ts`는 PR #68에서 이미 v3로 통합됨

---

## 관련 파일

- 분석 문서: `tasks/deletion-analysis.md` (베조스 분석, K/M/D 분류)
- 아키텍처: `docs/design-docs/builder-v3-architecture.md`
- 이전 HANDOFF: `e02dc2b [docs] HANDOFF — 2026-04-26 세션 종료`

---

## 마지막 상태

- 브랜치: `main` (PR #69 review 대기, worktree `feature/cleanup-builder-v2`)
- 검증: pytest 624 / ruff / tsc / lint / build 모두 pass

새 세션에서 "HANDOFF.md 읽고 PR #69 review 결과 반영해줘" 또는 "E2E 검증 도와줘" 등으로 이어가면 됩니다.
