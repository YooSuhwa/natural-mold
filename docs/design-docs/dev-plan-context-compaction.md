# 개발 기획서 — 컨텍스트 압축(Compaction): 게이지 정상화 + 자동압축 표현

> 이 문서 하나로 처음부터 끝까지 구현 가능하도록 **실제 소스 기준 파일:라인 + 변경 스니펫 + 검증**을 명시한다.
> 대상 버전: deepagents `0.6.9`, Moldy main(`d98ddd67` 이후).
> 범위 결정: **수동 compact는 이번 범위에서 제외(후속 옵션)**. 자동 압축만으로 기능적 안전망은 충분하므로, **Phase 0(컨텍스트 단일화) + Phase 1(자동압축 표현)** 까지를 권장 범위(Tier 2)로 한다.
> 선행: `docs/design-docs/spec-context-compaction.md`.

---

## 0. 한눈에 — 범위 & 작업량

| Tier | 범위 | 사용자 경험 | 예상 |
|------|------|------------|------|
| 1 (최소) | **Phase 0**만 | 게이지 작동 + 자동압축 전 모델 정상(조용히 압축) | ~2–3d |
| **2 (권장)** | **Phase 0 + Phase 1** | + "방금 자동 정리됨 · 원본 보기" 마커 | **~3–4.5d** |
| 후속(Optional) | 수동 compact 도구 + 버튼 | 능동 압축(클로드코드式) | +2–3d |

**핵심 한 줄:** 진짜 가치는 **Phase 0**(게이지 켜짐 + 커스텀 모델 자동압축 임계값 교정)다. 수동은 필수 아님 → 후속으로 분리.

**Day1 spike 1개(나머지는 사실 확인용):**
- **`model.profile` 가 쓰기 가능한가?** → 가능하면 Phase 0가 한 줄. 불가하면 명시 미들웨어로 폴백.

---

## 1. 배경 — "자동 압축이 안 되는 거 아냐?"의 정확한 답

deepagents `create_deep_agent`는 **자동으로 `SummarizationMiddleware`를 주입**한다(graph.py:779 메인, :626/:702 서브). 이 미들웨어는 `compute_summarization_defaults(model)`(summarization.py:223-260)로 **`model.profile["max_input_tokens"]`** 를 읽는다:

| 모델 종류 | `model.profile` | 자동 압축 |
|----------|-----------------|-----------|
| **정식 모델**(LangChain이 아는 Claude/GPT/Gemini) | 내장 O | ✅ **이미 정상** — `trigger=("fraction",0.85)` |
| **커스텀/`openai_compatible`/게이트웨이** | 없음 | ⚠️ **고정 `("tokens",170000)` fallback** → 실제 한도와 무관(작은 모델은 트리거 전 에러, 큰 모델은 너무 일찍) |

→ "자동이 안 된다"는 **커스텀/게이트웨이 모델에 한해 맞다**(임계값이 틀림). 정식 모델은 이미 동작. **Phase 0가 이 임계값을 우리 `context_window`로 교정**한다. 또한 **컨텍스트 게이지**(`context-window-gauge.tsx`)도 이 값이 있어야 켜진다(현재 전 모델 NULL → 비활성).

> ⚠️ **자동 미들웨어를 "제거"할 필요는 거의 없다.** 후속에서 수동 도구를 붙일 때도, `create_summarization_tool_middleware`는 **도구 레이어만** 추가하고 자동 미들웨어는 deepagents 기본이 그대로 담당한다(소스 docstring: *"Only the tool layer is registered ... `create_deep_agent` adds one by default, so dropping it into `middleware=[...]` gives you both layers; they share state via `_summarization_event`"*). 중복 안 생긴다. (제거는 §6의 폴백 경우에만.)

---

## 2. 현재 상태 (소스 확인)

### 이미 되어 있는 것 ✅
- ORM/스키마/CRUD: `Model.context_window`(`models/model.py:46`), 스키마(`schemas/model.py:26/48/69/92/107`), 생성 `routers/models.py:157`, 수정 `:205-215`, 직렬화 `services/model_service.py:100`.
- **`ModelBrief.context_window` 노출됨**(백엔드 `schemas/agent.py:151`, 프론트 `lib/types/index.ts:36`) — 게이지가 읽음.
- **모델 추가/수정 UI 입력 필드 구현됨**(`model-add-dialog.tsx:91,222-235`, `model-edit-dialog.tsx:54,260-272`).
- 자동 압축 자체는 동작(정식 모델 한정, §1).

### 비어 있는 것 ❌ (이번 작업)
- `default_models.py`: **context_window 0건** → 전 모델 NULL → 게이지 비활성 + 커스텀 모델 임계값 틀림.
- 우리 `context_window`가 **압축 엔진(model.profile)로 전달 안 됨**.
- **자동 압축 발생을 알리는 UI** 전무.

---

## 3. Phase 0 — context_window 단일 소스화 (필수)

목표: `models.context_window`가 단일 진실원 → 게이지 + 자동압축 임계값이 같은 숫자.

### 0.1 seed 값 채우기 (트리비얼)
`backend/app/seed/default_models.py` — `DEFAULT_MODELS`의 4개 dict에 `"context_window": int` 추가.
```python
{
    "provider": "anthropic", "model_name": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6",
    "is_default": True,
    "cost_per_input_token": Decimal("0.000003"), "cost_per_output_token": Decimal("0.000015"),
    "context_window": 200000,   # ← 추가 (출처 주석). GPT-4o=128000, Gemini 등 공식 한도
},
```
- ⚠️ seed가 "없을 때만 insert"면 **기존 운영 DB 모델은 NULL 잔존** → 운영자 UI(이미 구현됨)로 보정하거나 1회성 backfill. (seed upsert 동작 확인.)

### 0.2 context_window → 압축 엔진 주입 (핵심, spike 후 택1)

**옵션 A — 모델 프로필 주입 (권장·idiomatic):**
deepagents가 보는 `model.profile["max_input_tokens"]`를 우리 값으로 세팅 → **기본 자동 미들웨어가 그대로 올바른 임계값** 사용. 미들웨어 스택 손 안 댐.
- 위치: `agent_runtime/model_factory.py:create_chat_model()`(L215-278) 반환 직전.
  ```python
  # context_window는 **extra로 전달받아
  if context_window:
      try:
          model.profile = {**(getattr(model, "profile", None) or {}), "max_input_tokens": int(context_window)}
      except Exception:
          pass  # read-only면 옵션 B
  ```
- **spike: `.profile`가 쓰기 가능한지** 확인(LangChain 버전에 따라 property일 수 있음).

**옵션 B — 명시 미들웨어 교체 (폴백, profile read-only일 때만):**
deepagents 자동 summarization을 제외하고 우리가 직접 추가.
- `runtime_component_builder._prepare_runtime_components()`(L494-666) 미들웨어 조립부(L570-575):
  ```python
  from deepagents.middleware.summarization import create_summarization_middleware
  if cw:
      middleware.append(create_summarization_middleware(model, components.backend))  # 우리가 profile 세팅했거나, 직접 trigger 구성
  ```
- `build_agent`(`runtime_component_builder.py:83`)의 `create_deep_agent` 호출에 **자동 제외** 전달(`excluded_middleware={"SummarizationMiddleware"}` — 정확한 인자 경로는 spike; `_excluded_middleware.py:90-165` `.name` 문자열 매칭).

> 권장: **A**. 프로필만 맞추면 deepagents 기본이 알아서 올바르게 동작 → 제외/교체 불필요. B는 A 불가 시에만.

### 0.3 AgentConfig 배선
- `agent_runtime/runtime_config.py` `AgentConfig`(L14-84)에 `context_window: int | None = None` 추가.
- cfg 채우는 대화 라우터/`chat_service`(→ `_prepare_agent`)에서 `Agent.model.context_window`를 세팅(Agent.model 관계 이미 로드됨).
- model_factory에 cw를 넘기는 경로 연결(`_model_constructor_params` 또는 호출부에서 `**extra`로).

### 0.4 (UI 이미 완료) — 확인만
모델 추가/수정 다이얼로그 context_window 입력 필드 존재(§2). 변경 불필요.

### Phase 0 검증
- 단위: cw→trigger 토큰(0.85), AgentConfig 전달, model_factory가 profile에 주입.
- 통합/E2E: 실모델로 **게이지 활성** + 긴 대화로 자동 압축이 `cw*0.85` 부근 트리거. **openai_compatible(프로필 없는 모델)도 우리 cw로 동작**.
- done-when: 게이지 활성 / 압축 임계값=우리 cw 기준 / 게이지%·임계값 일치.

---

## 4. Phase 1 — 자동압축 인라인 마커 (권장 범위의 표현 부분)

목표: 자동 압축이 일어나면 사용자가 인지(조용한 메시지 교체로 인한 혼란 방지).

### 1.1 감지 (프론트, 견고)
자동 압축은 **요약 `HumanMessage` 삽입**으로 드러난다. 식별키(소스 확정): `additional_kwargs["lc_source"] === "summarization"`(`_is_summary_message` summarization.py:501-516). content엔 오프로드 경로 `/conversation_history/{thread_id}.md` 포함(L533-564).
- 위치: `frontend/src/lib/chat/langgraph-runtime/langchain-message-conversion.ts`(`convertMoldyLangChainMessage`) — 변환 시 이 메시지에 `metadata.isCompactionSummary = true` + 오프로드 경로 추출 부여.
- ⚠️ 이 메시지는 role=user지만 **시스템 요약** → 일반 user 말풍선으로 그리면 안 됨. 변환 단계에서 가로채 마커로 분기.

### 1.2 렌더 (신규 컴포넌트)
- `frontend/src/components/chat/compaction-summary.tsx`(신규): 인라인 마커
  - "🗜️ 이전 대화를 요약해 컨텍스트를 정리했어요 · 원본 보기"
  - "원본 보기" = content에서 뽑은 `/conversation_history/...` 경로를 `read_file`/아티팩트로 열기(기존 파일 도구 재사용).
- 메시지 렌더 분기 지점에서 `metadata.isCompactionSummary`면 이 컴포넌트로.
- i18n: `messages/ko.json`+`en.json`에 `chat.compaction.summary`, `chat.compaction.viewOriginal`.

### 1.3 (보강, Optional v2) 전용 SSE 이벤트
- 더 견고하게 하려면 백엔드에서 압축을 전용 이벤트로 emit: `agent_runtime/event_names.py`에 `COMPACTION` 추가 + `streaming.py`/`langgraph_streaming.py`에서 메시지 수 감소 감지. **v1은 메시지 마커(1.1)만으로 충분** → 이건 후속.

### Phase 1 검증
- 긴 대화로 자동 압축 트리거 → 인라인 마커 표시 + 원본 열람. 게이지가 압축 후 하락.
- 캡쳐: 압축 전/후(마커 + 게이지 하락).

---

## 5. 변경 파일 요약 (권장 범위 Tier 2)

**백엔드**
- `seed/default_models.py` — context_window seed (+ 필요시 backfill).
- `agent_runtime/runtime_config.py` — `AgentConfig.context_window`.
- `agent_runtime/model_factory.py` — (옵션 A) profile 주입 + cw 인자 수용.
- cfg 채우는 대화 라우터/`chat_service` — cw 세팅.
- (옵션 B 시에만) `runtime_component_builder.py` — 자동 제외 + 명시 미들웨어.

**프론트**
- `lib/chat/langgraph-runtime/langchain-message-conversion.ts` — 요약 메시지 감지/분기.
- `components/chat/compaction-summary.tsx`(신규) — 인라인 마커.
- 메시지 렌더 분기 지점(assistant-thread 메시지 컴포넌트) — 마커 렌더.
- `messages/ko.json`+`en.json` — i18n.

---

## 6. 후속(Optional) — 수동 compact 도구 + 버튼

> 자동만으로 기능 충분하므로 분리. 사용자가 "태스크 전에 미리 압축하고 싶다"는 요구가 나오면 추가. **추가만 하면 되고 제거 작업 없음.**

- **백엔드**: `runtime_component_builder` 미들웨어 리스트에 `create_summarization_tool_middleware(model, components.backend)` 추가 → `compact_conversation` 도구 노출. **자동 미들웨어 제거 불필요**(도구 레이어만 추가, 자동은 deepagents 기본 유지, state 공유). 50% 미만 사용량에선 도구 자동 거부(`_compact_threshold=value*0.5`).
- **승인 정책 결정**: 압축은 가역적(원본 오프로드)이라 **무승인 즉시 실행** 권장. 승인을 원하면 `interrupt_on`에 `compact_conversation` 추가 → 기존 HiTL 승인 카드(`approval-card.tsx`) 자동 재사용.
- **프론트**: `tool-icons.ts` `EXACT_TOOL_ICONS`에 `compact_conversation: <Icon>` 1줄(툴 pill 자동 렌더). 게이지 옆 compact 버튼(`assistant-thread.tsx` ThreadComposer L1122-1130) — `pct = (latestTurnUsage.prompt_tokens / contextWindow)*100`로 **≥50%일 때만 활성**. 트리거 v1 = `sendMessage("대화를 압축해줘")`(콜백 prop을 page→section→thread→composer로 전달).
- 예상 +2–3d.

---

## 7. 작업 순서 & 리스크

```
Day1   Spike: model.profile 쓰기 가능? → Phase 0 옵션 A/B 확정
Day1-2 Phase 0: seed + (A profile 주입 or B 교체) + AgentConfig 배선 + 검증
Day3   Phase 1: 요약 메시지 감지 + 마커 컴포넌트 + i18n
Day3-4 E2E + 캡쳐(게이지 전후, 마커) + /code-review
```

| # | 리스크 | 대응 |
|---|--------|------|
| R1 | `model.profile` read-only | 옵션 B(제외+명시) 폴백 |
| R2 | seed가 기존 행 갱신 안 함 | 운영자 UI(구현됨) or backfill |
| R3 | 요약 메시지를 user 말풍선으로 오렌더 | conversion 단계 분기(§4.1) |
| R4 | 정식 모델은 자동 이미 동작 | Phase 0는 그들에겐 "게이지용"; 커스텀 모델에 핵심 |

**구현 전 확정:** 옵션 A/B(R1 spike 결과) 하나면 끝. 수동(§6)은 별도 의사결정.

---

## 8. 테스트 전략 — "85%까지 채우지 않고" 검증

핵심 원리: **자동 압축 임계값은 `context_window`에 비례**(`0.85 × max_input_tokens`)한다. 따라서 **테스트에서 `context_window`를 작게(예 1500~2000) 잡으면 메시지 2~3개 만에 임계값을 넘겨** 압축을 재현한다. 200k를 무한히 채울 필요 없음.

```
테스트 모델 context_window = 1500
→ 트리거 ≈ 0.85 × 1500 = 1275 토큰
→ 시스템 프롬프트 + 답변 2~3턴이면 도달 → 자동 압축 발생
```
> 토큰 카운트는 `count_tokens_approximately`가 **메시지 content에서 직접** 센다(usage_metadata 불필요). 그래서 scripted 모델 메시지도 길이만 충분하면 카운트됨. 가장 확실한 건 `context_window`를 작게 두는 것.

### Level 1 — 프론트 단위 (즉시, LLM 불필요) [필수]
압축 "표현"만 검증. 트리거 자체가 불필요.
- `langchain-message-conversion.ts`: **mock 요약 메시지**(`HumanMessage` + `additional_kwargs.lc_source="summarization"`, content에 `/conversation_history/...` 경로)를 변환 → `metadata.isCompactionSummary === true` + 오프로드 경로 추출 단언.
- `compaction-summary.tsx`: 마커가 "요약됨 + 원본 보기"를 렌더하고, 일반 user 말풍선으로 안 그려지는지 단언.
- vitest, 밀리초. **85% 무관.**

### Level 2 — 백엔드 통합 (수 초, 브라우저 X) [권장]
압축이 실제로 **트리거**되는지 + 오프로드 검증.
- 테스트에서 `context_window=1500`(작게)인 모델로 에이전트 빌드 → 메시지 2~3개 invoke → state messages가 요약 메시지로 교체됐는지(`lc_source=="summarization"`) + 오프로드 파일 생성됐는지 assert.
- aiosqlite/in-memory로 가능하면 빠르고 결정론적. (LLM 호출이 필요하면 E2E scripted 모델 또는 cheap 게이트웨이 모델로 최소 턴.)
- Phase 0 자체 단위: `cw → trigger 토큰(0.85)` 계산, `model.profile` 주입 후 `compute_summarization_defaults`가 fraction 경로 타는지.

### Level 3 — E2E (브라우저, 몇 턴) [권장]
UI까지. **85%가 아니라 tiny-context로 몇 턴.**
- 격리 스택(5433/8101/3100). 테스트 DB 모델 `context_window`를 작게 UPDATE(게이지 캡쳐 때 쓴 패턴) → 2~3턴 전송 → **인라인 마커 표시 + 게이지 하락** 단언/캡쳐.
- 캡쳐: 압축 전/후(마커 + 게이지 90%→낮은 값).

### 옵션 — 결정론적 scripted 압축 마커 (가장 견고한 프론트 E2E)
프론트 마커를 LLM·토큰카운팅 없이 100% 안정적으로 E2E하려면, 기존 `E2E_TOOL_GROUP`/`E2E_SEARCH_GROUP`처럼 **`E2E_COMPACTION` scripted 마커**를 `backend/app/agent_runtime/e2e_scripted_model.py`에 추가:
- 마커 입력 시 **요약 `HumanMessage`(lc_source="summarization") + 오프로드 경로**를 결정론적으로 방출.
- 프론트는 이 메시지를 받아 마커 렌더 → 단언. (트리거 로직과 분리되어 flaky 없음.)
- 분담: **트리거 동작 = Level 2(tiny-context)**, **마커 렌더 = scripted 마커 E2E**.

### 검증 매트릭스
| 검증 대상 | 방법 | 85% 채움? |
|-----------|------|-----------|
| 게이지 활성/% 정확 | E2E tiny-context (기존 패턴) | ❌ |
| 자동압축 트리거 + 오프로드 | Level 2 통합(cw=1500) | ❌ (몇 턴) |
| 마커 변환/렌더 | Level 1 단위(mock) | ❌ |
| 마커 UI 전체 | scripted `E2E_COMPACTION` 또는 Level 3 | ❌ |
| 커스텀 모델 임계값 교정 | Level 2: profile 주입 후 fraction 경로 | ❌ |
