# 스킬 스튜디오 Phase 3 — 실측 A/B 벤치마크 · 비용 실회계 · 버전별 통과율 · 휴먼 피드백

상태: 확정 (2026-07-12) · 브랜치 `feature/skill-studio-phase3` (origin/main 5c7a6c01 기준)
선행: Phase 2 6탭 스튜디오 (PR #293, `skill-studio-phase2-studio-spec.md`)

---

## 1. 배경과 목표

Phase 2는 "가짜 데이터 금지 원칙"에 따라 아래 4개를 범위 밖(§1.4)으로 미뤘다.
Phase 3는 이 항목들을 **실측 데이터 기반**으로 구현한다.

| # | 항목 | 현재 상태 (가짜/부재) |
|---|------|----------------------|
| 1 | 스킬 축 usage/cost 데이터 소스 | 없음 — `token_usages`/`daily_spend_*`는 user/agent/model 축만 |
| 2 | with/without A/B 벤치마크 | `benchmark` 컬럼은 존재하나 baseline이 실측 아님 — deterministic 러너는 전부 `passed=False` 플레이스홀더, LLM 러너는 grader의 **추정**("Also estimate the baseline") |
| 3 | 비용 실회계 | `estimate_run()`이 `estimated_cost_usd=0` 하드코딩, 평가 LLM 콜 usage 미기록 |
| 4 | 버전별 통과율 | 재료(`runs.skill_version`/`skill_content_hash`+`summary.pass_rate`)는 쌓이나 집계 API/UI 없음 |
| 5 | 휴먼 피드백 UI | 없음 (`message_feedbacks`는 채팅 전용) |

## 2. 확정된 제품 결정 (2026-07-12 사용자)

| # | 결정 | 내용 |
|---|------|------|
| D1 | A/B = 싱글턴 모델 콜 2-arm | 케이스당 with-arm 1콜 + without-arm 1콜 + grader 1콜. 기존 estimate의 3콜/케이스 모델과 정합. 풀 에이전트 런은 범위 밖(후속) |
| D2 | 휴먼 피드백 = 둘 다 | ① 평가 런 케이스별 agree/disagree+코멘트(grader 판정 검증) ② 스킬 단위 up/down+코멘트. **표시 전용** — pass_rate/health 계산에 미반영(반영은 후속) |
| D3 | usage 귀속 = 실측만 | ① 평가 런의 실제 LLM 토큰/비용(전량 해당 스킬 귀속 — 정확) ② 채팅 `execute_in_skill` 실행 횟수. "스킬이 연결된 대화의 전체 LLM 비용" 귀속은 다중 스킬 중복 계상·무관 턴 포함으로 왜곡되므로 **제외** |

## 3. 데이터 모델 (마이그레이션 m70)

### 3.1 `skill_usage_events` — 스킬 축 usage 원장

| 컬럼 | 타입 | 비고 |
|------|------|------|
| id | UUID PK | |
| skill_id | FK skills CASCADE, index | |
| user_id | FK users CASCADE | |
| source_kind | String(30) | `evaluation_run` \| `chat_execution` |
| evaluation_run_id | FK skill_evaluation_runs SET NULL, nullable | eval 소스만 |
| conversation_id | UUID nullable (FK conversations SET NULL) | chat 소스만 |
| agent_id | UUID nullable (FK agents SET NULL) | chat 소스만 |
| model_name | String(160) nullable | eval 소스만 |
| tokens_in / tokens_out | Integer, default 0 | eval 소스만 실값 |
| cost_usd | Numeric(12,6) nullable | 단가 없으면 NULL(0 아님 — "모름"과 "무료" 구분) |
| execution_count | Integer, default 1 | chat 소스 실행 횟수 |
| created_at | DateTime | Index `(skill_id, created_at)` |

집계 테이블(`daily_spend_skill`)은 만들지 않는다 — 스킬당 이벤트 볼륨이 낮아
(평가 런 단위 + 스킬 실행 단위) 온디맨드 집계로 충분. 필요 시 후속.

### 3.2 `skill_evaluation_runs.usage` JSON (nullable)

실측 rollup: `{"model_calls": n, "tokens_in": n, "tokens_out": n, "cost_usd": f|null, "measured": true}`.
과거 런은 NULL → 프론트 "실측 없음" 처리.

### 3.3 `skill_feedbacks` — 스킬 단위 휴먼 피드백

`message_feedback.py` 패턴. id, skill_id FK CASCADE, user_id FK CASCADE,
rating String(8) (`up`|`down`), comment Text nullable, created_at/updated_at,
**unique(skill_id, user_id)**.

### 3.4 `skill_evaluation_case_feedbacks` — 케이스별 판정 피드백

id, run_id FK skill_evaluation_runs CASCADE, user_id FK CASCADE,
case_index Integer, verdict String(10) (`agree`|`disagree`), comment Text nullable,
created_at/updated_at, **unique(run_id, user_id, case_index)**.

## 4. 실측 A/B 벤치마크 (러너 `llm-2`)

### 4.1 케이스 실행 (D1)

케이스 스키마는 기존 그대로 `{name, input, expected, metadata?}`. 케이스당:

1. **with-arm**: 모델 1콜 — system: "이 스킬을 활용해 과제를 수행하라" + 스킬
   페이로드(기존 `skill_payload()` 재사용: SKILL.md + 파일 요약) + 실행 케이스
   (`metadata.execute_in_skill`)면 기존 샌드박스 실행 결과(`deterministic_with_skill_results`)
   포함. user: case input.
2. **without-arm**: 모델 1콜 — user: case input만 (스킬 컨텍스트 없음).
3. **grader**: 모델 1콜 — 양 arm 출력 + expected를 채점, per-case
   `{status, score, baseline_status, baseline_score, notes}` JSON 반환.
   기존 `normalize_case_results` 계약 유지 — **추정이 아니라 실제 산출물 채점**.

- arm별 wall-clock/토큰 실측 → benchmark에 `measured: true`, `token_delta`
  (with−without tokens), `duration_delta_ms` 추가. 기존 with/without pass rate·
  score 통계(`aggregate_benchmark`)는 그대로 실측 입력으로 계산.
- 취소: 기존 `EvalCancellationCheckpoint` — arm 단위 체크포인트 추가.
- 타임아웃: 케이스 단위 `skill_evaluation_case_timeout_seconds` 적용(arm 합산).
- `run_config.baseline_comparison`(기본 true) false면 without-arm/grader baseline 스킵
  (estimate `uses_baseline_comparison`과 연동).
- runner_version `llm-2`, grader_prompt_version `llm-grader-2`. 워커 기본 evaluator를
  교체. deterministic 러너는 테스트/폴백용으로 유지.
- 실행 실패(모델 예외)는 케이스 단위 failed 처리 — 런 전체 fail은 기존 계약 유지.

### 4.2 E2E 결정론 (scripted 모델)

`e2e_scripted_model.py`에 평가 시나리오 추가:
- grader 시스템 프롬프트(`GRADER_SYSTEM_PROMPT` 식별 마커) 감지 → 유효 grader JSON
  (with=pass, without=fail → 양수 delta 연출).
- arm 프롬프트 감지 → 짧은 결정론적 답변(토큰 usage_metadata 포함).

## 5. 비용 실회계

### 5.1 usage 캡처

- LangChain 응답 `usage_metadata`(input_tokens/output_tokens)를 arm/grader 콜마다
  수집 → 런 rollup. 단가는 `Model` 테이블(`cost_per_input_token/output`)을 runner
  model_name으로 lookup(`chat_service.py`의 단가 조회 패턴). 단가 없으면 cost NULL.
- 런 완료 시: `run.usage` 저장 + `skill_usage_events(source=evaluation_run)` 기록.
- 케이스 자동 생성(`skill_evaluation_case_generator_llm`) 콜은 v1 범위 밖(후속).

### 5.2 estimate 실계산

`estimate_run()`: 케이스 input/expected + 스킬 페이로드 크기 기반 토큰 휴리스틱
(chars/4, 상수 명시) × 콜 수(3 or 2) × Model 단가 → `estimated_cost_usd`.
단가 미보유 시 0 유지 + 응답 `pricing_available: false` 플래그(프론트 "단가 미설정" 표기).

### 5.3 채팅 실행 카운트

`skill_executor.py` execute_in_skill **성공 경로**에서
`skill_usage_events(source=chat_execution, execution_count=1)` 기록.
- **비파괴**: SpendHook 패턴 — try/except 전량 삼킴 + 자체 세션(요청 세션 오염 금지).
- draft 실행(스킬 row 없음)·eval 내부 실행(fabricated descriptor)은 skip —
  descriptor에 skill_id 있는 경우만.

## 6. API

| 메서드 | 경로 | 응답 |
|--------|------|------|
| GET | `/api/skills/{id}/usage?days=30` | totals(tokens_in/out, cost_usd, eval_run_count, execution_count) + 일별 시리즈 + source 분리 |
| GET | `/api/skills/{id}/evaluations/version-stats` | `[{skill_version, content_hash, run_count, latest_pass_rate, avg_pass_rate, latest_benchmark_delta, last_run_at}]` 시간순 |
| PUT/DELETE | `/api/skills/{id}/evaluations/{setId}/runs/{runId}/case-feedback` | body `{case_index, verdict, comment?}` — (run,user,case_index) upsert |
| GET/PUT/DELETE | `/api/skills/{id}/feedback` | 내 피드백 + `{up_count, down_count}` 집계 |

- 전부 기존 ownership 가드(enumeration-safe 404) + 뮤테이션은 `verify_csrf`.
- `SkillEvaluationRunResponse`에 `usage` 필드, run 상세에 케이스 피드백(내 것+집계) 동봉.
- 피드백/usage 페이로드에 시크릿·프롬프트 원문 저장 금지(§8 함정).

## 7. 프론트 (평가 탭 개편 + 버전 탭 배지)

| 요소 | 위치 | 구현 |
|------|------|------|
| A/B 비교 차트 | run detail | chart.js bar (with/without pass rate·mean score), `measured` 배지, 레거시 런(measured 부재) "추정" 라벨. 기존 표시 키(duration_delta_ms/token_delta/quality_delta) 실키 정합 |
| 실비용 | run detail + estimate 다이얼로그 | run.usage 토큰·비용·콜 수 / 실단가 예상 비용 + pricing_available |
| 버전별 통과율 추이 | 평가 탭 섹션 | chart.js line (version-stats) |
| usage 카드 | 평가 탭 | 30일 토큰/비용/실행 횟수, source 분리 |
| 케이스 피드백 | run detail 케이스 행 | agree/disagree 토글 + 코멘트 팝오버, 집계 카운트 |
| 스킬 피드백 | 평가 탭 상단 카드 | up/down + 코멘트, up/down 집계 |
| 리비전 통과율 배지 | 버전(히스토리) 탭 | version-stats의 content_hash 매칭 |

- 차트는 `components/usage/spend-{line,bar}-chart.tsx` 패턴 재사용(chart.js 4.5).
- 새 api/hooks/types — 쿼리 키 팩토리 준수, i18n ko/en 동시 추가.
- 디자인 가드(`pnpm lint:design-system`)·a11y 신규 위반 0.

## 8. 함정 (선행 세션 수확)

- push는 `SKILL_EVALUATION_ENABLED=true git push` (pre-push 훅).
- 실 LLM 투어가 throwaway DB text_primary를 영속 → scripted 검증 전 DB 재생성.
- 시드 스킬 패키지 바이트 content-hash — lint fix 금지.
- grader 프롬프트에 스킬 파일 내용이 실림 — usage 이벤트/피드백 페이로드에 프롬프트
  원문 저장 금지(토큰 수·비용 스칼라만).
- `test_worker_loop_consumes_enqueued_run` xdist 간헐 flake(단독 재실행 판별).
- 프론트: DataTable rowSelection 규칙, useAuiState reference-stable, 부분 스트리밍
  args 가드, E2E 버블 스코프 단언(page-wide getByText 금지).

## 9. E2E 증빙 (캡처 투어)

`captures/captures-skill-studio-phase3.spec.ts` (E2E_CAPTURE_TOUR=1):

1. 평가 탭 전경 — A/B 차트 + usage 카드 + 스킬 피드백 카드
2. run detail — 실측 benchmark(measured 배지) + 실비용
3. 버전별 통과율 추이 차트
4. 히스토리(버전) 탭 리비전 통과율 배지
5. 케이스 피드백 상호작용(disagree + 코멘트)
6. 스킬 피드백 상호작용(up + 집계 반영)
7. estimate 다이얼로그 실단가 예상 비용

## 10. 성공 기준 (검증 가능)

1. 평가 런 완료 시 `run.usage` 실측 기록 + `skill_usage_events` 이벤트 적재
   (단가 없는 모델은 cost NULL).
2. llm-2 런 benchmark가 `measured: true` + 실측 with/without pass rate·token_delta —
   grader 추정이 아닌 양 arm 실행 산출 채점.
3. 채팅 execute_in_skill 성공 시 해당 스킬 실행 카운트 증가(draft/eval 내부 제외).
4. 평가 탭에 A/B 차트·버전별 통과율 차트·usage 카드·스킬 피드백이 실데이터로 렌더,
   히스토리 탭 리비전에 통과율 배지.
5. 케이스/스킬 피드백 upsert 왕복 + 새로고침 유지.
6. backend pytest(SKILL_EVALUATION_ENABLED=true)+ruff, vitest/tsc/eslint/build/
   lint:i18n/design-system, mock+live E2E, 캡처 투어 7장 그린.
