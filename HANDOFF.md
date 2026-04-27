# HANDOFF — Builder v3 cleanup + 후속 작업 (세션 2)

**최종 업데이트**: 2026-04-27 (세션 2)
**Base**: `main @ 877a77b`

---

## 이번 세션 (2026-04-27 #2) 머지 결과

| PR | 내용 | diff |
|---|---|---|
| [#69](https://github.com/YooSuhwa/natural-mold/pull/69) | Builder v2 dead code 제거 | +4 / -1812 |
| [#70](https://github.com/YooSuhwa/natural-mold/pull/70) | HANDOFF 업데이트 (세션 1) | docs |
| [#71](https://github.com/YooSuhwa/natural-mold/pull/71) | HiTL approval form 헬퍼 추출 (Phase 3/4/5/8) | +189 / -174 |
| [#72](https://github.com/YooSuhwa/natural-mold/pull/72) | buildStreamState 참조 안정화 | +15 / -2 |
| [#73](https://github.com/YooSuhwa/natural-mold/pull/73) | confirm_build v2 image 분기 제거 | +11 / -25 |

검증: pytest **624 passed** / ruff clean / frontend tsc·lint·build 모두 pass

---

## 다음에 해야 할 작업

### 사용자 직접 (필수)
1. **브라우저 E2E 시나리오 검증** — Builder v3 흐름 확정
   - mockup 1~4 (네 가지 시작 패턴)
   - recommendation/prompt approval 수정요청 분기
   - 이미지 skip / regenerate
   - HiTL stale interrupt 처리

### 보류 (의도적)
2. **`BuilderStatus.STREAMING` enum 제거 + DB 마이그레이션** — 사용자 결정으로 보류
   - **이유**: PoC 단계에서 효용 < 위험. 사용처 0건이지만 enum 잔존 자체는 무해
   - **재개 시점**: production 출시 전 본격 정리 라운드, 또는 다른 DB 스키마 변경과 묶어서

### 신규 후보
3. **새 기능** — TASKS.md의 Phase 6 잔여 또는 PRD 신규 항목
4. **(선택) approval-card.tsx와 useApprovalForm 통합** — 별개 도구(`request_approval`, 3-way decision)지만 하단 textarea+버튼 패턴은 유사. 추가 추출 여지 있음

---

## 주의사항

- **`BuilderStatus.STREAMING` enum 보존** — DB 안전성 + 사용자 결정. 코드에서 사용처는 0건
- **`image_service.generate_agent_image`** — `routers/agents.py:139`에서 사용 중. builder_service에서만 dead였음
- **`builder/sub_agents/*.py` + `prompts/*.md`** — v3 노드가 import. 변경 금지
- **frontend `stream-builder.ts` / `use-builder-runtime.ts`** — PR #68에서 v3 통합 시 이미 제거됨

---

## 관련 파일

- 분석 문서: `tasks/deletion-analysis.md` (Builder v2→v3 K/M/D 분류)
- 아키텍처: `docs/design-docs/builder-v3-architecture.md`
- 이전 HANDOFF: `5d8a61d` (세션 1)

---

## 마지막 상태

- 브랜치: `main @ 877a77b` (5건 PR 머지 후)
- 검증: pytest 624 / ruff / tsc / lint / build 모두 pass
- 열린 worktree: 없음 (모두 정리)

새 세션에서 "HANDOFF.md 읽고 E2E 검증 도와줘" 또는 새 작업 지시로 이어가면 됩니다.
