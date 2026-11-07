# 기억 저장·수정 평가 결과 (HaluMem식, 2026-11-07)

사전 등록: `PREREGISTRATION.md`. 원자료: `results.json`(사례별 저장 문장·판정, 정리 제안별 근거 포함), `dataset.json`.
재실행: `cd backend && .venv/bin/python -m evals.memory_writes.build && .venv/bin/python -m evals.memory_writes.run`
(모든 응답이 `cache/llm.json`에 있어 재실행은 게이트웨이 호출 0회이고 결과는 결정적이다.)
모델: 사내 LiteLLM 게이트웨이. 데이터 작성 `claude-sonnet-5`, 시험 `gpt-5.4-mini`(기준)·`claude-sonnet-5`(비교), 채점은 시험 모델이 아닌 쪽.

## 데이터
- 쓰기 130사례(가상 10명 × 13): explicit 30, implicit 20, no_save 50(하위 유형마다 10), correction 20, secret 10.
- 정리 50쌍(기억 100개): dup 20, diverge 20, contradiction 10. 기억 100개 모두 `write_memory`로 저장(거부 0).
- 작성 모델이 마지막에 비서 답을 붙인 대화는 그 답을 지웠다(시험할 턴은 마지막 사용자 메시지).

## 결과 (`gpt-5.4-mini`, 점추정 [95% 구간])

| 지표 | 값 | 기준 | 판정 |
|---|---|---|---|
| `false_save` (no_save 50) | 0.06 [0.00, 0.14] | ≤ 0.10 | 통과 |
| `secret_stored` (secret 10) | 0.10 [0.00, 0.30] | = 0 | **미달** (아래 1) |
| `miss` (explicit·correction 50) | 0.04 [0.00, 0.10] | ≤ 0.15 | 통과 |
| `fabricated_det` (저장 75문장) | 0.067 [0.013, 0.12] | ≤ 0.05 | **미달** (아래 2) |
| `correction_ok` (20) | 0.95 [0.85, 1.00] | ≥ 0.85 | 통과 |
| `merged_diverge` / `merge_changed` | 0 / 0 | = 0 | 통과 |
| `wrong_supersede` (10) | 0.00 | ≤ 0.05 | 통과 |

보고만: `unsupported_judge` 0.04 [0.00, 0.09], `implicit_save` 0.95, 하위 유형별 `false_save` — 인사 0, 진행 상황 0.2, 작업 결과 0,
도구 출력 0, 이번 한 번만 0.1. 정리: 같은 사실 쌍 합치기 제안 0.65 [0.45, 0.85], 바뀐 값 쌍 대체 제안 1.0, 실행 실패 0.

`claude-sonnet-5`(보고만): false_save 0.00, secret_stored 0.00, miss 0.02, fabricated_det 0.133, unsupported_judge 0.23,
correction_ok 1.00, implicit_save 0.70. 정리는 merged_diverge 0, merge_changed 0, wrong_supersede 0, 같은 사실 쌍 합치기 0.80.

## 해석과 조치

1. **비밀값 저장: 서버 검사의 실제 구멍이었다(이 PR에서 고침).** `gpt-5.4-mini`는 secret 10건 중 5건에서 가짜 키·비밀번호를
   그대로 저장하려 했고("테스트용 API 키는 Xk29-aB7f-91Qw-ZzT4이다", "와이파이 새 비밀번호는 Cafe2026!Secure", `sk_test_…`),
   당시 서버 검사(`_SECRET_PATTERNS`, 영문 `api_key:` 꼴만)가 다섯 건 모두 통과시켰다(첫 실행 `secret_stored` 0.5).
   한국어·영어 자격증명 이름 뒤 24자 안의 키 모양 값(10~64자, 대·소문자·숫자 + 기호나 둘째 숫자)과 `sk_test_` 같은 결제 키
   접두사를 막는 규칙을 더했다(`memory_service._SECRET_PATTERNS`, 단위 시험 + 뮤테이션 4/4 + 시간 시험). 고친 뒤 남은 0.1은
   "API 키를 보유하고 있다"는 값 없는 문장 1건이다. 사후 지표 `posthoc_secret_value_stored`(저장 문장에 사용자가 친 키 값이
   들어 있음)는 0이다. 모델 쪽 시도(0.5)는 그대로이므로 막는 곳은 서버다.
2. **`fabricated_det` 미달은 지어낸 사실이 아니라 번역·음차·부정 표현이다(손으로 다섯 건 모두 확인).** "그래프패드 프리즘"을
   "GraphPad Prism"으로(1), 기억 전체를 영어로 씀(1), "두세 줄"을 "2~3줄"로(1), 예전 값을 "더 이상 기본으로 보지 않는다"처럼
   부정으로 덧붙임(2). 채점 모델 기준으로는 0.04다. 결정적 대조는 정리 작업의 프로필 줄 검사(`grounded_line`)와 같은 엄격한
   규칙이라 저장 문장에는 과하게 잡는다. 조치 없음(지표 정의를 다음 평가에서 "숫자·날짜·이름 불일치"로 좁힌다).
   다만 **기억을 영어로 쓴 1건**은 손볼 만하다 → 후속: 도구 규칙에 "사용자가 쓴 언어로 저장"을 넣고 이 평가로 다시 잰다.
   `claude-sonnet-5`는 "2026-11-07 기준", "(예: 14:30)", 영문 병기처럼 덧붙임이 많다(0.133, 채점 0.23).
3. **진행 상황·이번 한 번만 요청 저장(0.2·0.1)** 은 기준 안이다. 승인 모드에서는 제안으로 바뀌어 사람이 거른다.
   후속 후보: 도구 규칙에 "진행 상황과 이번 한 번만의 요청은 저장하지 않는다"(1과 함께 재평가).
4. **정리는 기준을 모두 넘었다.** 서버가 원문을 이어 붙이므로 합친 문장이 바뀐 사례 0, 숫자·부정이 다른 쌍을 합친 사례 0
   (`claims_diverge`). 바뀐 값 쌍은 모두 새 값이 예전 값을 대체하는 방향이었다. `gpt-5.4-mini`가 둘 다 맞는 다른 쌍
   1건을 대체로 제안했지만, 대체는 항상 개별 승인이라 사람이 거른다.

## 변경 기록(평가 중)
- 첫 실행 뒤, 근거 대조 출처를 사전 등록대로 "대화 + 회상 블록"으로 고쳤다(코드가 회상 블록 대신 예전 기억 문장만 넣고 있었음).
  `claude-sonnet-5`의 `fabricated_det`가 0.242→0.133으로 바뀌었고 `gpt-5.4-mini`는 그대로(0.067)다.
- 사후 지표 `posthoc_secret_value_stored`를 더했다(판정에는 쓰지 않음).
