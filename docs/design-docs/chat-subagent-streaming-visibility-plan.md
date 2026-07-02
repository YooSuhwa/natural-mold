# G10 — 서브에이전트 스트리밍 가시성 (Subagent Streaming Visibility)

> 상태: **기획(Plan)** — 구현 전
> 작성: 2026-07-02
> 브랜치: `feature/chat-subagent-streaming-visibility` (worktree)
> 출처: `docs/design-docs/chat-feature-gap-analysis.md` G10 (Tier 2, 가치 중 / 노력 중)
> 관련: [[chat-generative-ui-dev-plan]] · `docs/superpowers/plans/2026-06-13-assistant-ui-langgraph-v3-streaming.md` · `adr-021-value-based-trace-redaction.md` · `adr-011-sse-stream-resume.md`

---

## 0. TL;DR

G10의 원래 정의는 "부모가 **최종 결과만** 보고 서브에이전트 내부 진행/사고는 안 보인다"였다. 하지만 실제 소스코드를 조사한 결과, **v3 채팅 경로에는 서브에이전트 스트리밍 가시성 인프라가 이미 상당 부분 구현·검증되어 있다.** 따라서 G10은 "신설"이 아니라 **"기존 인프라를 프로덕션(실 LLM) 경로에서 end-to-end로 완성하고, 식별·라벨·사고요약·정책의 남은 갭을 메우는 증분 작업"**이다.

**현재 이미 되는 것 (e2e로 검증됨):**
- v3 경로에서 `task` 툴콜 → 서브에이전트 pill(`SubagentCard`) 즉시 렌더
- 진행률 집계 pill(`SubagentProgress`: 완료/진행중/실패) + 활동 스트립(`RunActivityStrip`)
- 서브에이전트 내부 메시지/툴콜의 **scoped 실시간 렌더**(`useMessages`/`useToolCalls`, `tools:<call_id>` 중첩 namespace 기반)
- 우측 레일 상세(`subagent-panel-content.tsx`), 재로드 hydration 유지(cg47 수정)

**진짜 남은 갭 (= G10 작업):**
1. **표시명 미주입** — v3 경로가 `subagent_display_names`를 소비하지 않아 pill이 runtime name(`agent_xxxxxxxx`)/description을 보여줌 (레거시 경로만 사람이 읽는 이름으로 enrich).
2. **내부 사고(reasoning) 마스킹** — `redact_private_reasoning`가 서브에이전트 reasoning을 `summary`/`status`/`signature`만 남기고 마스킹 → "내부 사고" 관찰 불가.
3. **인라인 가시성 정책** — 동시 실행 서브에이전트 중 앞 2개만 인라인 상세(`DEFAULT_MAX_LIVE_INLINE_DETAILS = 2`), 완료 5개↑ 자동 접힘(`AUTO_COLLAPSE_COMPLETED_THRESHOLD = 5`). 관찰성 강화 시 재검토 대상.
4. **실 LLM 실측 미확인** — 위 가시성은 scripted 모델 e2e로 검증됐다. 실제 deepagents `task`(블로킹 `ainvoke`) + 실 LLM에서 내부 토큰이 `tools:<call_id>` namespace로 실제 방출되는지는 **Phase 0 스파이크로 실측** 필요.

---

## 1. 배경 — 두 개의 스트리밍 경로

프로덕션 v3 채팅과 레거시 채팅이 공존하며, 서브에이전트 노출 수준이 근본적으로 다르다.

### 1-1. 레거시 경로 (구조적으로 내부 스트리밍 불가)
- `backend/app/agent_runtime/streaming.py:395-399` — `agent.astream(..., stream_mode="messages")`, **`subgraphs=True` 없음**.
- deepagents `task` 툴은 서브에이전트를 **블로킹 `.ainvoke()`**로 실행하고(`deepagents/middleware/subagents.py:721`), 마지막 비어있지 않은 `AIMessage.text` 하나만 `ToolMessage`로 반환(`:600-638`).
- 결과: 부모 스트림에는 `task` 툴콜 시작(+ `enrich_subagent_tool_call_parameters`로 `agent_name`/`agent_runtime_name` 주입, `streaming.py:499-503`) + `task` 툴 결과(= 서브에이전트 최종 리포트)만 노출. **내부 토큰/툴콜 전무.**

### 1-2. v3 프로토콜 경로 (프로덕션 기본, 내부 이벤트 전파됨)
- 엔드포인트: `POST /api/conversations/{id}/langgraph/threads/{tid}/commands`(`run.start`) → `execute_agent_stream_langgraph` (`conversation_agent_protocol_commands.py:328-329`).
- 주 스트림: `agent.astream_events(actual_input, config=config, version="v3")` (`langgraph_streaming.py:75-86`).
  - `astream_events(version="v3")`는 LangGraph Pregel 네이티브 기능으로 **`subgraphs`를 강제 True**로 둔다 (`langgraph/pregel/main.py:378-393`: "subgraphs is forced True so nested namespaces flow through scoped muxes").
- 폴백 스트림: `agent.astream(..., stream_mode=["messages","updates","values","custom"], subgraphs=True)` (`langgraph_streaming.py:89-101`).
- 이벤트 어댑터가 namespace/checkpoint_ns를 wire까지 보존: `langgraph_protocol_adapter.py:37-72`, `protocol_events.py:174-193`.
- 메인 루프는 namespace 필터 없이 모든 이벤트를 emit (`langgraph_streaming.py:394-483`).
- **테스트 근거**: `backend/tests/agent_runtime/test_langgraph_streaming.py:111` — `"namespace": ["tools:call-1"]` 중첩 namespace 이벤트가 emit/persist/replay 됨. `tools:<tool_call_id>`는 정확히 `task` 툴 내부 서브에이전트 실행에서 나오는 namespace 패턴.

**결론:** v3 경로는 서브에이전트 내부 이벤트를 `tools:<task_call_id>` 중첩 namespace로 이미 흘려보낸다. 프론트 SDK가 이를 소비한다.

---

## 2. 프론트엔드 — 3계층 실시간 가시성 (이미 구현됨)

`@langchain/react` v1.0.22의 `useStream`이 discovery 맵(`stream.subagents`)을 관리하고, 프로젝트 컴포넌트가 이를 렌더한다.

### 2-1. 데이터 소스
- `frontend/src/lib/chat/langgraph-runtime/use-moldy-langgraph-stream.ts:2100-2103` — `useStream({ transport, threadId })`.
- 구독 채널: `:218-227` — `messages, tools, values, updates, lifecycle, tasks, checkpoints, custom`.
- SDK discovery(`@langchain/langgraph-sdk/.../discovery/subagents.js`): `tool-started`+name=`task` → subagent 등록(running), `tool-finished`/`tool-error` → complete/error. **`task` 툴콜 자체가 subagent 식별자(trigger_call_id)** 로 쓰인다. 재로드 시 checkpoint의 `getState().values.messages`로 seed(`subagents.js:34-45`).
- scoped 상세: `useMessages(stream, subagent)`/`useToolCalls(stream, subagent)` — subagent namespace(`tools:<call_id>`)로 **ref-counted 구독을 마운트 시 연다**(`@langchain/react/dist/selectors.d.ts:27-48`). **토큰 단위 스트리밍은 백엔드가 그 namespace로 `messages` 채널 델타를 방출해야 성립**(`:36-45`).

### 2-2. 렌더 계층
1. **거친 진행률(항상 실시간)**: `subagent-progress.tsx:14-44`("완료 N/M · 진행 K · 실패 J") + `RunActivityStrip`(`activity-model.ts:25-60`이 namespace 있는 messages 이벤트마다 subagent 활동 upsert). `assistant-message-loading.tsx:105-116`에서 running일 때만 표시.
2. **서브에이전트 pill(항상 실시간)**: `sub-agent-ui.tsx:92-97`(`makeAssistantToolUI({toolName:'task'})`) → `subagent-card.tsx:160-206`(`SubagentCard` = `CollapsiblePill`). discovery 스냅샷 기반 상태 전이.
3. **인라인 scoped 상세(제한적 실시간)**: `subagent-card.tsx:97-158`(`SubagentDetails`). 게이트 `canRenderScopedDetails = subagent !== null && stream !== null && inlinePolicy.canRenderInlineDetails` (`:170-171`).

### 2-3. 인라인 정책 (핵심 제약)
- `subagent-runtime.tsx:8-9` — `AUTO_COLLAPSE_COMPLETED_THRESHOLD = 5`, `DEFAULT_MAX_LIVE_INLINE_DETAILS = 2`.
- `getSubagentInlinePolicy` (`:129-163`): running 중 인덱스 `< 2`만 자동 펼침+인라인 렌더, 나머지는 `overflowedLiveDetails`(접힘, 상세 미렌더 → 우측 레일에서 온디맨드). complete는 `defaultExpanded: scoped.length < 5`. error는 항상 펼침.
- **lazy resolve**: `CollapsiblePill`은 펼쳐야만 `renderBody` 호출(`collapsible-pill.tsx:233-235`) → 접힌 카드는 scoped 구독조차 안 열림. cg47 수정(`collapsible-pill.tsx:125-147`)으로 재로드 시 `defaultExpanded` false→true rising-edge에서 재확장(사용자가 접은 카드는 존중).

---

## 3. 백엔드 — 남은 갭의 정확한 위치

### 3-1. 표시명(display name) 비대칭
- `runtime_config.py:58-59` — `AgentConfig.subagents_config` + `AgentConfig.subagent_display_names`.
- 채팅 cfg 빌드: `conversation_stream_service.py:163-168` — `cfg.subagents_config, cfg.subagent_display_names = await build_subagents_config(...)`. **두 값 모두 채워진다.**
- **소비 비대칭**:
  - `subagents_config` → 양쪽 경로 모두 `create_deep_agent(subagents=...)` 전달 (`runtime_component_builder.py:767`).
  - `subagent_display_names` → **레거시 경로만** 소비 (`agent_stream_runner.py:218` → `streaming.py:239,499-503`). v3 러너(`langgraph_agent_stream_runner` / `stream_agent_response_langgraph`)는 **인자로 받지도 않는다**(`langgraph_streaming.py:214-228` 시그니처에 없음).
- 결과: v3 pill은 `task` args의 `subagent_type`(runtime name = `agent_xxxxxxxx`)이나 `description`을 보여줌. 사람이 읽는 에이전트 이름 아님.

### 3-2. reasoning 마스킹
- `langgraph_protocol_adapter.py:103` — 모든 v3 이벤트 data가 `redact_private_reasoning(normalized)`을 거침.
- `langgraph_reasoning_redaction.py:6-30` — `reasoning`/`thinking`/`chain_of_thought` 등은 `[redacted]`; reasoning **블록**은 `DISPLAYABLE_REASONING_KEYS = {type,id,index,summary,message,status,signature}`만 노출.
- 결과: 서브에이전트의 내부 사고는 provider가 `summary`를 채운 경우에만 부분 노출. G10 "내부 사고 가시성"의 정책 결정 지점 (ADR-021 값 기반 redaction과 정합 유지 필요).

### 3-3. 데드코드 `extract_subagent_discovery`
- `langgraph_protocol_adapter.py:107-137` — 완성돼 있으나 `app/` 내 호출 0건. 프론트 SDK가 `task` 툴콜로 자체 discovery하므로 **기능적으로 불필요**. G10에서 표시명을 emit 계층에 주입하는 방식을 택하면 이 함수는 계속 데드코드로 남거나 정리 대상.

---

## 4. 목표 / 비목표 / 성공 기준

### 4-1. 목표
- **G10-A (표시명)**: v3 경로에서 서브에이전트 pill/진행률/우측레일이 **사람이 읽는 에이전트 이름**을 표시한다.
- **G10-B (실측 & 신뢰성)**: 실 LLM 프로덕션 경로에서 서브에이전트 내부 진행(토큰/툴콜)이 인라인/우측레일에 실시간 표시됨을 **실측으로 확증**하고, 안 되면 원인(namespace 방출 누락 등)을 수정한다.
- **G10-C (사고 요약 노출, 선택)**: 서브에이전트의 reasoning **요약**을 관찰 가능하게 노출(보안 정책 유지). 결정 포인트.
- **G10-D (인라인 정책, 선택)**: 동시 2개/완료 5개 제한을 관찰성 관점에서 재조정 또는 "모두 펼치기" 토글 제공. 결정 포인트.

### 4-2. 비목표
- 레거시 경로에 내부 스트리밍 추가 (구조적으로 불가 + v3가 프로덕션 기본이므로 무가치).
- deepagents `task` 툴의 블로킹 실행 구조 변경 (벤더 코드).
- `AsyncSubAgentMiddleware`(background subagent) 신규 UI (별도 트랙, `2026-06-13...streaming.md:82,98` 참조).
- 서브에이전트 raw private reasoning(전문) 노출 — 보안상 명시적 제외.

### 4-3. 성공 기준 (done-when)
- v3 실 LLM 채팅에서 부모가 서브에이전트를 위임하면, 위임 즉시 **에이전트 이름이 붙은 pill**이 뜨고, 진행 중 내부 도구호출/부분 출력이 인라인(또는 우측레일)에 실시간 표시된다.
- backend `uv run pytest` green (신규 표시명/emit 테스트 포함).
- frontend `pnpm vitest run` 전체 green + `pnpm build`/`pnpm lint` green.
- v3 서브에이전트 e2e(신규/보강 스펙) green + 회귀 스펙(`chat-langgraph-v3-regressions.spec.ts` 서브에이전트 케이스) 무회귀.
- 캡처 PNG(가시성 상태) 1~2장.

---

## 5. 결정 포인트 (사용자 확인 필요)

| # | 결정 | 옵션 | 기본 권장 |
|---|------|------|-----------|
| D1 | **범위** | (a) A+B만(표시명+실측) / (b) A+B+C(사고요약) / (c) A+B+C+D(정책까지) | **(a) 최소 → 실측 후 확장** |
| D2 | **표시명 주입 방식** | (a) v3 러너에 `subagent_display_names` 전달 후 `task` 툴콜 args enrich (레거시와 동일 패턴) / (b) 프론트가 config 매핑을 별도 fetch | **(a) 레거시 패턴 재사용** |
| D3 | **사고 요약 노출(C 채택 시)** | (a) reasoning `summary`만(현행 유지, redaction 무변경) / (b) 서브에이전트 컨텍스트 한정으로 노출 키 확장 | **(a) 현행 유지 — 보안 우선** |
| D4 | **인라인 정책(D 채택 시)** | (a) 무변경 / (b) 제한 상향(2→3~4) / (c) "모두 펼치기" 사용자 토글 | **(c) 토글(정책 불변+사용자 제어)** |

---

## 6. Phase 0 — 실측 스파이크 (구현 前 필수)

**목표: "실 LLM v3 경로에서 서브에이전트 내부 이벤트가 실제로 `tools:<call_id>` namespace로 방출되는가"를 로그로 확증.**

1. 서브에이전트를 가진 에이전트를 하나 구성(부모 + 자식 1개, 실 LLM 키).
2. `langgraph_streaming.py` emit 루프에 임시 디버그 로깅 추가(namespace + method만; **커밋 안 함**) 또는 브라우저 devtools에서 SSE 이벤트 스트림 관찰.
3. 위임 실행 후 확인:
   - [ ] `task` 툴콜이 `tools:<call_id>` namespace로 나오는가
   - [ ] 서브에이전트 내부 LLM 토큰이 **동일 or 중첩 namespace**로 델타 방출되는가 (`messages` 채널)
   - [ ] 서브에이전트 내부 툴콜이 방출되는가
   - [ ] 프론트 `stream.subagents`에 등록되고 scoped `useMessages`가 채워지는가 (React devtools/화면)
4. 산출: `tasks/g10-spike-findings.md`에 실측 결과 기록 → Phase 1 범위 확정.

> 이미 scripted e2e(`e2e_langgraph_v3_script.py` slow subagent parts)가 내부 스트리밍을 검증하므로 **경로 자체는 작동할 가능성 높음**. Phase 0은 "실 LLM에서도 동일한가 + 표시명 외 추가 갭이 있는가"를 좁히는 게 목적.

---

## 7. 구현 계획 (Phase별)

### Phase 1 — 표시명 주입 (G10-A) [핵심, 노력 소~중]

> **구현 노트 (실제 채택, 커밋 f15f7367)**: 아래 계획은 D2=(a) "레거시처럼 `task` 툴콜 args를 enrich"를 가정했으나, 실제로는 **사이드채널 `moldy.subagent_names` custom 이벤트 + 프론트 표시계층 치환**을 채택했다. 이유: checkpoint-backed `subagent_type`를 재작성하면 실행/namespace 바인딩/reload seeding이 깨지고, args enrich는 stream-mode fallback(args를 안 건드림)에서 동작하지 않는다. 사이드채널은 두 문제를 모두 회피한다. 3번(args enrich) 대신 backend가 매핑을 방출하고, 4번은 `SubagentCard`+우측 레일이 conversation-scoped atom으로 `runtime_name→display_name`을 치환한다.

백엔드(계획 당시 D2=a 가정 — 실제는 위 노트 참조):
1. `langgraph_streaming.py`의 `stream_agent_response_langgraph` 시그니처에 `subagent_display_names` 추가 (레거시 `streaming.py:239` 대응).
2. v3 러너(`langgraph_agent_stream_runner`)에서 `cfg.subagent_display_names` 전달 (레거시 `agent_stream_runner.py:218` 대응).
3. `task` 툴콜 이벤트 방출 시 `subagent_type`(runtime name)을 표시명으로 enrich. 레거시 `enrich_subagent_tool_call_parameters`(`streaming.py:139-156,499-503`) 로직을 v3 방출 경로에 공유/재사용.
   - 주의: v3는 `astream_events`가 원본 툴콜을 방출 → enrich 지점을 emit 루프 or 어댑터(`adapt_*`)로 정하고, redaction/persist 순서와 충돌 없는지 확인.
프론트:
4. `SubagentCard`가 표시명을 우선 사용하도록 확인(현재 discovery `name` fallback → task args). 필요 시 표시명 필드 매핑 추가.
테스트: 백엔드 v3 방출 단위 테스트(표시명 enrich), 프론트 `subagent-card.test.tsx` 표시명 케이스.

### Phase 2 — 실측 기반 신뢰성 보강 (G10-B) [Phase 0 결과에 종속]
- Phase 0에서 내부 토큰 미방출로 판명되면: namespace 방출/구독 경로 수정(예: emit 루프의 namespace 필터, `synthesize_tool_events_from_values`의 namespace 승계 `langgraph_tool_event_synthesis.py:119` 검토).
- 정상 방출 확인되면: e2e를 실 LLM 근사(slow subagent) 케이스로 보강만.

### Phase 3 — 사고 요약 노출 (G10-C) [D1=b/c 채택 시]
- D3 결정에 따라 `langgraph_reasoning_redaction.py` 노출 정책 조정 or 유지. 조정 시 **서브에이전트 컨텍스트 한정** + ADR-021 정합 + redaction 테스트(`backend/tests/.../reasoning_redaction` 계열) 갱신.

### Phase 4 — 인라인 정책 (G10-D) [D1=c 채택 시]
- D4 결정에 따라 `subagent-runtime.tsx:8-9` 상수 조정 or "모두 펼치기" 토글 UI. `subagent-runtime.test.tsx`(2개 제한/5개 접힘 계약) 갱신.

### Phase 5 — 검증 & 캡처
- backend pytest / frontend vitest·build·lint / v3 서브에이전트 e2e / 회귀 스펙 / 캡처 PNG.
- `/code-review` 세션 diff.

---

## 8. 테스트 & 회귀 게이트

**재사용/보강할 기존 테스트:**
- 프론트 유닛: `subagent-runtime.test.tsx`(인라인 정책), `subagent-progress.test.tsx`, `subagent-card.test.tsx`, `collapsible-pill.test.tsx`(defaultExpanded 재동기화), `subagent-panel-content.test.tsx`, `activity-model.test.ts`.
- 백엔드: `test_langgraph_protocol_adapter_subgraphs.py`(namespace 보존/discovery 정규화), `test_langgraph_streaming.py:111`(중첩 namespace emit/replay), `test_subagents_runtime.py:52`(`build_subagents_config`).
- e2e: `chat-langgraph-v3-regressions.spec.ts:323-384`(subagent 결과 인라인 유지: 라이브→reload→HITL resume→2차 reload). 헬퍼 `langgraph-v3-helpers.ts:97-109`, 스크립트 `e2e_langgraph_v3_script.py`(slow subagent parts).

**핵심 회귀 가드(절대 깨지면 안 됨):**
- 서브에이전트 카드 인라인 결과가 재로드/HITL resume 전 구간 유지.
- 사용자가 접은 카드는 자동 재확장 안 됨(cg47 계약).
- 시크릿 args redaction 유지(`chat-langgraph-v3-regressions.spec.ts:259`).

**E2E 실행:** throwaway 스택(별도 PG 포트) + `E2E_SCRIPTED_MODEL_ENABLED=true` + `E2E_SEED_USER_ENABLED=true`. (CLAUDE.md E2E 격리 섹션 참조.)

---

## 9. 리스크

| 리스크 | 완화 |
|--------|------|
| 실 LLM에서 내부 토큰이 namespace 없이 flat하게 섞임 | Phase 0 스파이크로 선(先)확증. 데이터 없이 구현 착수 금지 |
| 표시명 enrich 지점이 redaction/persist/replay 순서와 충돌 | 레거시 enrich 패턴을 그대로 이식 + replay 테스트로 가드 |
| 사고 노출로 시크릿/PII 누출 | D3 기본 = 현행 redaction 유지. 확장 시 서브에이전트 한정 + 값기반 마스킹 병행(ADR-021) |
| 인라인 제한 완화로 스트리밍 성능/스크롤 저하 | D4 기본 = 토글(기본값 불변). `chat-scroll-follow-streaming-ux-plan.md:490` 레이아웃 고려 |
| 공유 checkpointer 풀 부하로 e2e 타임아웃 | origin/main 대조 실행으로 분리 판단(CLAUDE.md) |

---

## 10. 참고 자료 (정독 순서)
1. `docs/superpowers/plans/2026-06-13-assistant-ui-langgraph-v3-streaming.md` — v3 스트리밍/subagent discovery 아키텍처 원본.
2. `frontend/src/lib/chat/langgraph-runtime/subagent-runtime.tsx` — 인라인 정책 구현.
3. `backend/app/agent_runtime/langgraph_streaming.py` + `langgraph_protocol_adapter.py` — v3 emit/어댑터.
4. `backend/app/agent_runtime/langgraph_reasoning_redaction.py` — 사고 노출 제약.
5. `backend/app/agent_runtime/streaming.py:139-156,499-503` — 레거시 표시명 enrich(이식 대상).
