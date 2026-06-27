# 자동압축 마커 "B-풀버전" 구현 (dev-plan-context-compaction-marker.md)

대상: deepagents 0.6.9 / 브랜치 `feature/context-compaction` (Phase 0 = c1b03660 이후)
범위: (a) 압축 중 "압축 중…" 일시 표시 + (b) 완료 후 "이전 대화를 요약했어요 · 원본 보기" 영구 마커.
프로덕션 v3 런타임(langgraph_v3) 기준. Phase 0 커밋과 분리된 독립 커밋.

## 실측 확정 (probe 완료)
- 요약 토큰: `method=="messages"` & `event["data"]["metadata"]["lc_source"]=="summarization"` (adapter 통과 후)
- 압축 확정: `method=="values"` & `event["data"]["_summarization_event"]={cutoff_index, summary_message, file_path}`
- offload: `file_path = /conversation_history/{thread_id}.md`
- `stored_custom_protocol_event(name=, payload=, event_id=, id=)` — memory/artifact 패턴과 동일 dedup

## 백엔드 — 전부 완료 ✅ (ruff + agent_runtime 253 + streaming/config 219 그린)
- [x] B1 `config.py` — `compaction_marker_enabled: bool = True` feature flag
- [x] B2 `event_names.py` — `COMPACTION: Final = "moldy.compaction"`
- [x] B3 `langgraph_streaming.py` — 감지 헬퍼 `_compaction_signal` / `_compaction_offload_path` / `_compaction_event`
- [x] B4 `langgraph_streaming.py` v3 루프 — 요약토큰 suppress + running 1회/done 1회 emit (dedup 플래그)
      · done payload `{state, offload_path, cutoff_index}` (run_id 매핑 불가 실측 → payload에서 제거)
      · flag off면 기존 동작 그대로
- [x] B5 `langgraph_streaming.py` fallback 루프 — 동일 처리
- [x] B6 `streaming.py` (legacy) — 요약 토큰 suppress만 (누수 0; 마커는 v3 전용)
- [x] B7 Level 2 테스트 `test_langgraph_streaming_compaction.py` (2 passed)
      · ★ 답변 무손실 회귀 — over-suppress 시 답변 '' 됨을 probe로 실증, 정확 일치는 보존
      · compaction(running)×1 + compaction(done,offload_path)×1 + 요약 토큰 suppress + flag off

## 프론트 (v3 LangGraph 런타임) — 전부 완료 ✅
- [x] F1 `activity-types.ts` — RunActivityKind에 `'compaction'`
- [x] F2 `activity-model.ts` reduceCustom — compaction 분기 (running→running, done→complete)
- [x] F3 `run-activity-strip.tsx` — KIND_ICON.compaction(Minimize2Icon) + activityLabel `t('compaction')`
- [x] F4 `compaction-events.ts`(신규) — custom+messages 채널 훅: done.seq 직전 마지막 message-start에
      매핑(실측 순서 running→answer→done) + lastAssistant fallback
- [x] F5 `compactionFromMessage()` — compaction-events.ts에 위치 (additional_kwargs.metadata.compaction)
- [x] F6 `langchain-message-conversion.ts` — compaction을 metadata.custom.compaction에 attach (usage 패턴)
- [x] F7 `use-moldy-langgraph-stream.ts` — compaction-events 배선 + attach (messagesWithUsage 직후)
- [x] F8 `compaction-summary.tsx`(신규) — 영구 마커 ("요약했어요" + "원본 보기" 클립보드 복사)
- [x] F9 `assistant-thread.tsx` — AssistantCompactionMarker (useAuiState, reference-stable `?? null`)
- [x] F10 i18n ko/en — `chat.compaction.*` + `chat.activity.compaction`

## 테스트 (§7 — 85% 안 채우고 window 작게)
- [x] L1 vitest — compaction-events 9개 (파싱/attach/fallback + reduceCustom) + compaction-summary 렌더
- [x] L2 백엔드 — B7 (누수 0 + emit 1쌍 + 답변 무손실 + flag off) 2 passed
- [ ] L3 (선택) scripted E2E_COMPACTION 캡쳐 — 사용자 확인 후 결정

## 완료 기준
- [x] tsc 0 / vitest 1049 / 백엔드 ruff+pytest / lint(i18n·design-system) / build 그린
      (vitest 1 unhandled error = tool-icons.ts pre-existing, 내 변경 무관)
- [ ] 실서버(또는 scripted) 캡쳐: "압축 중…" + 영구 마커 — L3과 함께 결정
- [x] feature flag off 가능
- [ ] 마커 전용 독립 커밋 → push → PR
