# 개발 기획서 — 컨텍스트 압축(Compaction) 단일화 + 수동 compact + UI 표현

> 이 문서 하나로 처음부터 끝까지 구현 가능하도록 **실제 소스 기준 파일:라인 + 변경 스니펫 + 검증**을 명시한다.
> 대상 버전: deepagents `0.6.9`, Moldy main(`d98ddd67` 이후).
> 선행 SPEC: `docs/design-docs/spec-context-compaction.md`.

---

## 0. 한눈에 — 작업량 산정

| Phase | 내용 | 핵심 변경 | 난이도 | 예상 |
|-------|------|----------|--------|------|
| **0** | context_window 단일 소스화 | seed 값 + 모델 profile 주입(또는 명시 미들웨어) + AgentConfig 배선 | 중 | **1.5–2.5d** |
| **1** | 수동 `compact_conversation` 도구 기본 켜기 | 미들웨어 1개 추가(0과 수렴) + approval/trigger 정책 | 하~중 | **1–1.5d** |
| **2** | UI: 도구 아이콘 + compact 버튼 + 자동압축 인라인 마커 | 아이콘 1줄 + 버튼/트리거 + 감지/마커 + SSE | 중~상 | **2.5–3.5d** |
| — | E2E·캡쳐·문서 | 통합 테스트 + before/after | — | **0.5–1d** |
| **합계** | | | | **약 5.5–8.5 person-day (1인)** |

**가장 큰 불확실성 3가지(먼저 검증):**
1. LangChain 모델 `.profile` 가 **쓰기 가능한지**(가능하면 Phase 0 대폭 단축).
2. `create_deep_agent`에 **auto-injected SummarizationMiddleware 제외**를 어떻게 넘기는지(정확한 인자).
3. 게이지 버튼이 압축을 **트리거하는 경로**(메시지 전송 vs 전용 엔드포인트).

→ 구현 1일차에 위 3개 spike부터 끝낼 것.

---

## 1. 현재 상태 (소스 확인 결과)

### 이미 되어 있는 것 ✅
- ORM/스키마: `Model.context_window`(`models/model.py:46`), 모든 Pydantic 스키마(`schemas/model.py:26/48/69/92/107`).
- 모델 CRUD: 생성 `routers/models.py:157`, 수정 `:205-215`, 직렬화 `services/model_service.py:100`.
- **`ModelBrief.context_window`** 노출됨(백엔드 `schemas/agent.py:151`, 프론트 `lib/types/index.ts:36`) — 컨텍스트 게이지가 이걸 읽음.
- **모델 추가/수정 UI 입력 필드** 완전 구현(`model-add-dialog.tsx:91,222-235`, `model-edit-dialog.tsx:54,260-272`).
- 컨텍스트 게이지(`context-window-gauge.tsx`), 툴 pill·아이콘(`tool-icons.ts`), HiTL 승인 카드(`approval-card.tsx`).
- deepagents 자동 압축은 이미 동작(graph.py가 `create_summarization_middleware(model, backend)` 자동 주입; main `graph.py:779`, subagent `:626/:702`).

### 비어 있는 것 ❌ (이번 작업 대상)
- `default_models.py`: **context_window 0건** → 전 모델 NULL → 게이지 비활성 + 압축 임계값이 우리 값과 무관.
- 우리 `context_window`가 **압축 미들웨어로 전달 안 됨**(deepagents는 LangChain `model.profile["max_input_tokens"]`만 봄).
- **수동 compact 도구** 미적용(opt-in 안 함).
- **압축 발생 UI** 전무(자동·수동 모두).

---

## 2. deepagents 0.6.9 API (소스 기준 사실)

`backend/.venv/.../deepagents/middleware/summarization.py`:
- `create_summarization_middleware(model, backend, *, summary_prompt, trim_tokens_to_summarize, token_counter)` → `compute_summarization_defaults(model)` 호출(L223-260): `model.profile["max_input_tokens"]`(int) **있으면 `trigger=("fraction",0.85)`, keep=("fraction",0.10)`; 없으면 `trigger=("tokens",170000)`, keep=("messages",6)`**.
- `SummarizationMiddleware(model, backend, *, trigger, keep, ...)` — `trigger`는 `("tokens",N)|("messages",N)|("fraction",F)` 또는 dict. **public alias `.name == "SummarizationMiddleware"`**(L270-282).
- `create_summarization_tool_middleware(model, backend, ...)`(L1330-1418) — 자동 압축 + `compact_conversation` 도구를 **함께** 제공(확인 필요: trigger 옵션 인자 받는지).
- `SummarizationToolMiddleware(summarization=<mw>, *, system_prompt)`(L1421) — 기존 summarization 인스턴스 참조 + 도구 노출.
- 도구 `compact_conversation`: **인자 없음**(`CompactConversationSchema` 빈 스키마, L99-101). `_create_compact_tool()` L1499.
- **50% 게이트**: `_is_eligible_for_compaction()`(L1657), `_compact_threshold = int(value*0.5)`(L1625). 사용량이 auto-trigger의 50% 미만이면 도구 호출 거부.
- **자동압축 결과 메시지(프론트 감지 키)**: `HumanMessage` + `additional_kwargs["lc_source"] == "summarization"`(`_is_summary_message` L501-516). content는 오프로드 경로 `/conversation_history/{thread_id}.md` 포함(L533-564).

`backend/.venv/.../deepagents/graph.py` + `_excluded_middleware.py`:
- 자동 주입 제외: `excluded_middleware={"SummarizationMiddleware"}`(문자열 `.name` 매칭, `_apply_excluded_middleware` L90-165).
- ⚠️ **확인 필요**: `create_deep_agent(...)`에 이 제외 set을 넘기는 정확한 인자/경로(profile? 파라미터?). 1일차 spike.

---

## 3. Phase 0 — context_window 단일 소스화

목표: `models.context_window`를 단일 진실원으로, 게이지·자동압축·수동압축이 같은 숫자.

### 0.1 seed 값 채우기 (트리비얼)
`backend/app/seed/default_models.py` — `DEFAULT_MODELS`의 4개 dict에 `"context_window": int` 추가.
```python
{
    "provider": "anthropic",
    "model_name": "claude-sonnet-4-6",
    "display_name": "Claude Sonnet 4.6",
    "is_default": True,
    "cost_per_input_token": Decimal("0.000003"),
    "cost_per_output_token": Decimal("0.000015"),
    "context_window": 200000,   # ← 추가 (출처 주석)
},
```
- 나머지 모델도 공식 한도로(예: GPT-4o 128000, Gemini 2.x 등). **seed는 upsert인지 확인** — 기존 행을 덮어쓰는지/신규만 넣는지 보고, 기존 행도 갱신되게(필요시 마이그레이션 or 운영자 UI로 보정).
- ⚠️ seed가 "없을 때만 insert"라면 이미 생성된 운영 DB의 모델은 NULL로 남음 → **운영자 UI(이미 구현됨)로 채우거나** 1회성 backfill 스크립트.

### 0.2 context_window → 압축 미들웨어로 주입 (핵심)

두 경로 중 1일차 spike 결과로 택1:

**옵션 A — 모델 프로필 주입 (가능하면 최소 침습):**
`model.profile`에 `max_input_tokens`를 우리 값으로 세팅 → deepagents 자동 압축이 그대로 사용.
- 위치: `agent_runtime/model_factory.py:create_chat_model()`(L215-278) 반환 직전, 또는 `runtime_component_builder`에서 모델 생성 후.
- ```python
  if context_window:
      try:
          model.profile = {**(getattr(model, "profile", None) or {}), "max_input_tokens": int(context_window)}
      except Exception:
          pass  # profile read-only면 옵션 B로
  ```
- ⚠️ `.profile`가 read-only property면 불가 → 옵션 B.

**옵션 B — 명시 미들웨어 교체 (견고, 권장 기본):**
deepagents 자동 summarization을 제외하고, 우리가 토큰 트리거를 직접 계산해 추가. **Phase 1과 수렴**(아래 한 번에).
- `runtime_component_builder._prepare_runtime_components()`(L494-666), middleware 조립부(L570-575)에서:
  ```python
  from deepagents.middleware.summarization import create_summarization_tool_middleware
  cw = cfg.context_window  # 0.3에서 AgentConfig에 추가
  if cw:
      # 자동압축(우리 트리거) + compact_conversation 도구를 한 번에
      middleware.append(create_summarization_tool_middleware(
          model, components.backend,
          # trigger/keep 옵션 시그니처는 spike로 확정 (없으면 SummarizationMiddleware 직접 구성)
      ))
  ```
- 동시에 `create_deep_agent` 호출에 **자동 summarization 제외** 전달(`build_agent`, `runtime_component_builder.py:83`). 제외 인자 형식은 spike 확정.
- cw 없으면 제외/추가 안 함 → deepagents 기본(profile/fallback) 유지.

> 권장: **옵션 B**(ADR-012 "auto-injected 회피 + 명시 인스턴스" 패턴과 일관, profile 의존 제거). 단 A가 가능하면 A가 훨씬 싸다 → spike로 판단.

### 0.3 AgentConfig에 context_window 배선
deepagents/middleware가 cw를 알아야 하므로 런타임까지 흘린다.
- `agent_runtime/runtime_config.py` `AgentConfig`(L14-84)에 `context_window: int | None = None` 추가.
- cfg를 채우는 곳(대화 라우터/`chat_service` → `_prepare_agent`)에서 `Agent.model.context_window`를 cfg에 세팅. (Agent.model 관계는 이미 로드됨.)
- `langgraph_agent_stream_runner.py`(L124-137)가 cost_per_*를 넘기듯 cw도 필요한 경로로 전달(미들웨어 빌드는 cfg를 쓰므로 위 0.2에서 `cfg.context_window` 참조).

### 0.4 (UI는 이미 완료) — 확인만
모델 추가/수정 다이얼로그의 context_window 입력 필드 존재(§1). 변경 불필요.

### Phase 0 검증
- 단위: cw→trigger 토큰 계산(0.85·0.10), AgentConfig 전달.
- 통합/E2E: 실모델로 게이지 활성 표시 + 자동 압축이 `cw*0.85` 토큰 부근에서 트리거(긴 대화 강제). openai_compatible(프로필 없는 모델)도 우리 cw로 동작.
- done-when: 게이지가 실모델에서 활성 / 압축 트리거 = 우리 cw 기준 / 게이지%·압축 임계값 일치.

---

## 4. Phase 1 — 수동 compact 도구 기본 켜기

**옵션 B로 가면 0.2에서 `create_summarization_tool_middleware` 추가만으로 compact 도구가 켜진다**(자동+수동 한 번에). 옵션 A로 갔다면 별도로 `SummarizationToolMiddleware`를 추가해야 하는데 auto 인스턴스 핸들이 필요 → 사실상 옵션 B가 Phase 1까지 깔끔.

### 1.1 미들웨어 추가
- §3 옵션 B 코드가 곧 Phase 1. 결과: 에이전트에 `compact_conversation` 도구 노출.
- 도구는 **사용량 50% 미만이면 자동 거부**(deepagents 게이트). UI도 이에 맞춰 버튼 비활성(§5).

### 1.2 승인(approval) 정책 — 결정 필요
- Context7 문서는 "approval-by-default"라 하지만, **소스상 도구 미들웨어 자체가 interrupt를 거는지 미확인**(spike). Moldy는 HiTL을 `interrupt_on`/`_default_interrupt_on_from_tools`(CLAUDE.md)로 제어.
- 선택:
  - **(권장) 승인 없이 즉시 실행** — 압축은 가역적(원본 오프로드)이고, 버튼 클릭/대화 요청이 곧 동의. 단순.
  - 또는 `compact_conversation`을 `interrupt_on`에 추가 → 기존 승인 카드 표시(§5.1로 자연 표현). 안전하지만 한 번 더 클릭.
- 결정에 따라 `runtime_component_builder`의 interrupt 정책 한 줄.

### 1.3 트리거(스케줄) 모드 정책
- `is_trigger_mode`에서 HiTL 비활성(CLAUDE.md). compact 도구는 트리거 모드에서 **노출하되 자동 실행** 또는 **제외** 중 택1(자동압축은 어차피 동작하므로 수동 도구는 트리거 모드에서 빼도 무방).

### Phase 1 검증
- "대화를 압축해줘" → 에이전트가 `compact_conversation` 호출 → (승인 정책대로) 실행 → `/conversation_history/{thread_id}.md` 생성 + 다음 턴 메시지 수 감소 + 게이지 하락.
- 50% 미만에서 도구 거부 확인.

---

## 5. Phase 2 — UI 표현

### 5.1 수동 compact 표현 (대부분 무료)
- **툴 아이콘**: `frontend/src/lib/chat/tool-icons.ts` `EXACT_TOOL_ICONS`에 1줄:
  ```ts
  compact_conversation: ArchiveIcon,  // (lucide import 추가; 또는 Minimize2Icon/FoldVerticalIcon)
  ```
  → `compact_conversation` 툴 콜이 자동으로 🗜️ 아이콘 pill로 렌더(내가 만든 resolver 경로).
- **승인 카드**(승인 정책 채택 시): interrupt → `standardInterruptToToolCalls()`(`lib/chat/standard-interrupt.ts:126-153`)가 `request_approval` 툴 콜 생성 → `approval-card.tsx`가 렌더. `tool_args.tool_name == "compact_conversation"`. 추가 작업 거의 없음(라벨 i18n 정도).

### 5.2 게이지 옆 compact 버튼
- **위치**: `assistant-thread.tsx` `ThreadComposer` 하단 툴바(L1122-1130) — 게이지와 send 사이.
- **50% 게이트 계산**(컴포저에서 직접):
  ```ts
  const hasLimit = typeof contextWindow === 'number' && contextWindow > 0
  const pct = hasLimit ? ((latestTurnUsage?.prompt_tokens ?? 0) / contextWindow) * 100 : 0
  const canCompact = pct >= 50
  ```
  (`latestTurnUsage`는 L1063-64에서 이미 `useAtomValue`로 읽음.)
- **버튼**: `canCompact`일 때만 활성, 아니면 disabled + 툴팁("아직 압축할 만큼 차지 않았어요"). 🗜️ 아이콘.
- **트리거 경로**(spike로 택1):
  - **(A) 메시지 전송**: `use-moldy-langgraph-stream`의 `sendMessage(content)`(검색 결과상 존재; `stream.submit({messages:[HumanMessage]})`)로 "대화를 압축해줘" 전송 → 에이전트가 `compact_conversation` 호출. 가장 간단, LLM 턴 1회 소모. **버튼이 그 함수를 받으려면** `chat-runtime-section → assistant-thread → ThreadComposer`로 콜백 prop 추가(showContextGauge/contextWindow와 동일 prop 체인, page.tsx L430-440 → chat-runtime-section L93-114 threadProps → assistant-thread L1005 → ThreadComposer).
  - **(B) 전용 엔드포인트**(후속): 백엔드가 thread 체크포인트에 압축 직접 실행(턴 없이). 깔끔하나 작업 큼.
  - → **v1 = (A)**.
- **i18n**: `messages/ko.json`+`en.json`에 `chat.contextWindow.compactButton`, `compactDisabledHint`.

### 5.3 자동 압축(85%) 인라인 마커
- **감지(권장, 견고)**: 메시지 변환 지점에서 요약 메시지 식별 → `additional_kwargs.lc_source === "summarization"`.
  - 위치: `frontend/src/lib/chat/langgraph-runtime/langchain-message-conversion.ts`(`convertMoldyLangChainMessage`) — 그 메시지를 일반 user 말풍선이 아니라 **압축 마커**로 분기(metadata 플래그 부여) 후, 렌더 분기.
  - 렌더: 신규 컴포넌트 `compaction-summary.tsx` — "🗜️ 이전 대화를 요약해 컨텍스트를 정리했어요 · 원본 보기"(원본 = content의 `/conversation_history/...` 경로를 `read_file`/아티팩트로 열기).
- **대안(보강, 큼)**: 백엔드 SSE 전용 이벤트 `event_names.COMPACTION` 추가(`streaming.py`/`langgraph_streaming.py`에서 메시지 수 감소 감지) — v2. v1은 메시지 마커만으로 충분.
- ⚠️ 주의: 요약 메시지는 `HumanMessage`(role=user)지만 실제론 시스템 요약 → user 말풍선으로 그리면 안 됨. 위 분기로 가로채기.

### Phase 2 검증
- 수동: compact 툴 pill(🗜️) + (정책 시) 승인 카드. 버튼 ≥50% 활성/미만 비활성.
- 자동: 긴 대화로 85% 돌파 → 인라인 마커 + 원본 열람.
- 캡쳐: 버튼/툴 pill/마커, 게이지 압축 전후 하락.

---

## 6. 변경 파일 요약

**백엔드**
- `seed/default_models.py` — context_window seed (+ 필요시 backfill).
- `agent_runtime/runtime_config.py` — `AgentConfig.context_window`.
- `agent_runtime/runtime_component_builder.py` — 미들웨어 조립(L570-575)에 summarization tool 추가 + 자동 제외; `build_agent`(L83) 제외 인자.
- (옵션 A 시) `agent_runtime/model_factory.py` — profile 주입.
- cfg 채우는 대화 라우터/`chat_service` — cw 세팅.
- (선택 v2) `agent_runtime/event_names.py` + `streaming.py`/`langgraph_streaming.py` — COMPACTION 이벤트.

**프론트**
- `lib/chat/tool-icons.ts` — compact_conversation 아이콘.
- `components/chat/assistant-thread.tsx`(ThreadComposer) — compact 버튼 + 50% 게이트 + 콜백 prop.
- `components/chat/chat-runtime-section.tsx`, `app/.../conversations/[conversationId]/page.tsx` — compact 콜백 prop 체인.
- `lib/chat/langgraph-runtime/langchain-message-conversion.ts` — 요약 메시지 감지/분기.
- `components/chat/compaction-summary.tsx`(신규) — 인라인 마커.
- `messages/ko.json`+`en.json` — i18n.

---

## 7. 작업 순서 & 의존성

```
Day1  Spike (3 unknowns): profile 쓰기? / create_deep_agent 제외 인자 / 버튼 트리거 경로
      → Phase 0 옵션 A vs B 확정
Day1-2 Phase 0: seed + (A or B) 주입 + AgentConfig 배선 + 검증
Day3  Phase 1: compact 도구(0.2와 수렴) + 승인/트리거 정책 + 검증
Day4-5 Phase 2: 아이콘 + 버튼/트리거(prop 체인) + 자동압축 감지/마커 + i18n
Day5-6 E2E + 캡쳐 + 문서 + /code-review
```
의존성: **Phase 0 → 1 → 2** 순(0이 토대). 0의 옵션 B면 1이 거의 함께 끝남.

---

## 8. 리스크 / 열린 결정

| # | 항목 | 리스크 | 대응 |
|---|------|--------|------|
| R1 | `model.profile` 쓰기 가능 여부 | 옵션 A 불가능 시 B로 | Day1 spike |
| R2 | `create_deep_agent` 자동 제외 인자 | 미확인 → B 막힘 | Day1 spike (graph.py 시그니처) |
| R3 | `create_summarization_tool_middleware` trigger 옵션 인자 | 없으면 `SummarizationMiddleware`+`SummarizationToolMiddleware` 수동 조합 | spike |
| R4 | seed가 기존 행 갱신 안 함 | 운영 DB 모델 NULL 잔존 | 운영자 UI(구현됨) or backfill |
| R5 | 버튼 트리거가 LLM 턴 소모(A) | UX 사소한 비용 | v1 수용, v2 전용 엔드포인트 |
| R6 | 승인 정책 | 매번 승인=귀찮음 / 무승인=실수 압축 | 권장: 무승인(가역적) |
| R7 | 요약 메시지를 user 말풍선으로 오렌더 | 깨진 UI | conversion 단계 분기(§5.3) |

**구현 착수 전 확정할 결정:** 옵션 A/B(R1·R2 spike 결과), 승인 정책(R6), 버튼 트리거 A/B(R5).
