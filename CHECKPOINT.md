# CHECKPOINT — 스킬 스튜디오 Phase 3: 실측 A/B 벤치마크 · 비용 실회계 · 버전별 통과율 · 휴먼 피드백

> 이전 내용(Phase 2 6탭 스튜디오)은 완료·머지되어 교체함 (PR #293).
> Phase 1.5/2 잔여 백로그는 문서 하단에 보존.

스펙: `docs/design-docs/skill-studio-phase3-benchmark-cost-spec.md`
브랜치: `feature/skill-studio-phase3` (worktree `.claude/worktrees/feature+skill-builder-chat`, origin/main 5c7a6c01 기준)
원칙: 마일스톤 완료마다 커밋. push 검증 시 `SKILL_EVALUATION_ENABLED=true`.
확정 결정: D1 A/B=싱글턴 2-arm(3콜/케이스) · D2 피드백=케이스별+스킬 단위 둘 다(표시 전용) · D3 usage 귀속=실측만.

## M0: 스펙 문서
- [x] `skill-studio-phase3-benchmark-cost-spec.md` + CHECKPOINT 교체
- 상태: done (2026-07-12)

## M1: 마이그레이션 m70 + ORM 3종
- [x] `skill_usage_events` / `skill_evaluation_runs.usage` JSON / `skill_feedbacks` / `skill_evaluation_case_feedbacks`
- [x] ORM 모델 + `models/__init__.py` 등록, aiosqlite 호환
- 검증: `cd backend && uv run pytest -q tests/test_migrations*.py -k m70; uv run alembic upgrade head`(로컬 PG)
- done-when: 마이그레이션 왕복(upgrade/downgrade) + 모델 임포트 그린
- 상태: done (2026-07-12)

## M2: 스킬 축 usage 소스 (백엔드)
- [x] `skill_usage_service.py`: record_evaluation_usage / record_chat_execution / get_skill_usage_summary
- [x] execute_in_skill 성공 경로 chat_execution 기록(비파괴, 자체 세션, draft/eval 내부 skip)
- [x] `GET /api/skills/{id}/usage` (ownership enumeration-safe)
- [x] LLM usage_metadata 캡처 유틸 + Model 단가 lookup
- 검증: `uv run pytest -q -k "skill_usage"`
- done-when: 이벤트 기록/집계/API 테스트 그린
- 상태: done (2026-07-12)

## M3: 비용 실회계
- [x] estimate_run 실단가 계산 + pricing_available
- [x] 워커: run.usage 저장 + skill_usage_events 기록
- [x] 스키마: RunResponse.usage / RunEstimate 필드
- 검증: `uv run pytest -q -k "estimate or skill_evaluation_worker"`
- done-when: 실측 rollup 영속 + estimate 실계산 테스트 그린
- 상태: done (2026-07-12)

## M4: 실측 A/B 벤치마크 (러너 llm-2)
- [x] with-arm/without-arm/grader 3콜 실측, benchmark measured:true + token_delta/duration_delta_ms
- [x] arm 단위 취소 체크포인트 + case timeout, run_config.baseline_comparison
- [x] e2e_scripted_model grader/arm 시나리오
- 검증: `uv run pytest -q -k "skill_evaluation_llm or ab_arm"`
- done-when: 실측 벤치마크 단위 테스트 + scripted 결정론 그린
- 상태: done (2026-07-12)

## M5: 버전별 통과율 API
- [x] `GET /api/skills/{id}/evaluations/version-stats`
- 검증: `uv run pytest -q -k "version_stats"`
- 상태: done (2026-07-12)

## M6: 휴먼 피드백 백엔드
- [x] 케이스 피드백 PUT/DELETE + run 응답 동봉, 스킬 피드백 GET/PUT/DELETE + 집계
- [x] CSRF + ownership + enumeration-safe
- 검증: `uv run pytest -q -k "feedback"`
- 상태: done (2026-07-12)

## M7: 프론트 평가 탭 개편 + 버전 탭 배지
- [x] A/B 차트(chart.js) + measured/추정 라벨 + 레거시 키 정합
- [x] run detail 실비용, estimate 다이얼로그 실단가
- [x] 버전별 통과율 추이 차트 + 히스토리 탭 배지
- [x] usage 카드, 케이스/스킬 피드백 UI
- [x] api/hooks/types + i18n ko/en
- 검증: `pnpm vitest run` + tsc + lint + build + lint:i18n + lint:design-system
- 상태: done (2026-07-12)

## M8: E2E + 캡처 spec
- [x] mock `skill-studio-phase3.spec.ts`, live `skill-evaluation-actions` 확장
- [x] `captures-skill-studio-phase3.spec.ts` 투어 7장
- 검증: mock 모드 + throwaway 라이브 스택
- 상태: done (2026-07-12)

## M9: 전체 검증 + 적대 리뷰
- [x] backend pytest(SKILL_EVALUATION_ENABLED=true)+ruff / vitest/tsc/eslint/build/i18n/design-system / mock+live E2E
- [x] /code-review 적대 리뷰 — 발견 0 수렴까지(최소 2라운드)
- 상태: done (2026-07-12)

## M10: 실서버 캡처 투어 → 사용자 보고
- [x] throwaway 스택 → 캡처 실행 → PNG 전송
- 상태: done (2026-07-12)

## 마일스톤 의존
M0 → M1 → (M2, M3) → M4 → M5 → M6 → M7 → M8 → M9 → M10. 이후 PR.

---

## (보존) Phase 1.5/2 잔여 백로그
- 레일 소스 뷰 파일 목록에 바이너리 asset 미표시(표시 계층 fail-closed 유지 — 필요 시 목록만 노출+내용 404)
- improve 충돌 re-seed, 리로드 동의 플래그, dead 세션 대화 재생성
- 리로드-중-첫POST 극소 창의 2차 자동발화 시도(서버 unique active run 제약이 409로 거부해 실중복은 불가 — 서버측 first-message idempotency로 수렴 가능)
- 일괄 패키지 내보내기(D3 드랍), 스킬 복제(목업 행 메뉴), used_by_count 컬럼 제거(마이그레이션 필요)
- Phase 2 리뷰 리포트-온리: 파일 목록+뷰어 3중 사본 공용화, SettingsSectionCard 재사용, columns useMemo, serialize_skill 스칼라 서브쿼리화, 셸 useSelectedLayoutSegments, System LLM 미설정 안내 재작성, content path max_length 비대칭
- 리비전 >100 절단 UI affordance, 텍스트 rollback 변이-후 실패 창 원자화, 탭 enabled/breadcrumb UUID/빌더 422-as-empty UX 엣지
