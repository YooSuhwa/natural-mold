# SPEC — 컨텍스트 압축(Compaction) 단일화 + 수동 compact + UI 표현

상태: Draft (구현 전 합의용)
관련: ADR-001(Deep Agent Engine), ADR-012(HiTL Middleware), ADR-019(System LLM), 컨텍스트 게이지(commit 82f8ab32)
구현 문서: `dev-plan-context-compaction.md`

> **갱신 (범위·사실 정정)** — 구현 문서에서 다음을 확정/정정했다. 본 SPEC의 일부 서술보다 구현 문서가 우선한다.
> 1. **자동 압축은 정식 모델(LangChain이 아는 Claude/GPT/Gemini)에선 이미 정상 동작**한다(`model.profile` 보유). **커스텀/`openai_compatible`만 고정 170k fallback으로 임계값이 틀린다** → Phase 0가 교정.
> 2. **수동 compact는 이번 범위에서 제외(후속 옵션)**. 자동만으로 기능 충분. 권장 범위 = **Phase 0(context_window 단일화) + 자동압축 인라인 마커**(Tier 2).
> 3. **수동을 붙일 때도 자동 미들웨어 "제거"는 불필요**하다. `create_summarization_tool_middleware`는 도구 레이어만 추가하고 자동은 deepagents 기본이 유지된다(중복 없음, state 공유). 제거는 `model.profile`가 read-only라 임계값 교정을 명시 미들웨어로 해야 할 때의 폴백뿐.
> 4. context_window 주입은 **옵션 A(프로필 주입)가 기본**, 옵션 B(제외+교체)는 폴백.

## 1. 배경 / 문제

deepagents `0.6.9`는 `create_deep_agent` 기본 스택에 **`SummarizationMiddleware`를 자동 주입**한다(graph.py). 토큰 사용량이 **모델 컨텍스트의 85%**(`trigger=("fraction", 0.85)`)를 넘으면 오래된 메시지를 LLM 요약으로 대체하고 원본을 `/conversation_history/{thread_id}.md`로 오프로드한다. 우리는 이걸 끄지 않으므로 **자동 압축은 이미 활성**이다.

그러나 세 가지 공백이 있다:

1. **컨텍스트 크기 소스가 둘로 갈라져 있고, 우리 쪽은 비어 있다.**
   - 우리 DB `models.context_window`(컨텍스트 게이지가 읽는 값)는 **seed/UI 어디서도 채워지지 않는다** (`default_models.py`에 `context_window` 0건, 모델 생성/수정 UI에 필드 없음). → 실서비스 전 모델 NULL → **게이지가 전부 "한도 미설정"으로 비활성**.
   - deepagents 자동 압축은 **LangChain 모델 `.profile["max_input_tokens"]`** 를 쓴다(summarization.py L234). `model_factory`에서 우리가 주입하지 않으므로, 정식 모델(LangChain 프로필 보유)만 85%가 먹고 **`openai_compatible`/커스텀은 프로필 없음 → 보수적 고정 fallback**.
   - 결과: 게이지 %와 압축 임계값이 **서로 다른(또는 빈) 숫자**라 어긋난다.

2. **수동 compact가 꺼져 있다.** `compact_conversation` 도구(`SummarizationToolMiddleware`)는 opt-in인데 우리 스택에 없다. 사용자가 "압축해줘"라고 해도 도구가 없어 실행 불가.

3. **압축을 알리는 UI가 없다.** streaming/프론트에 압축 표시가 전무 → 자동 압축이 일어나도 사용자는 모른다.

### 공식 권장안(LangChain Deep Agents 문서, Context7 확인)
- 수동 압축: `create_summarization_tool_middleware(model, backend)` 추가 → `compact_conversation` 도구. **기본적으로 사용자 승인 필요(approval-by-default)**. 자동 압축과 같은 엔진/state 공유.
- 압축은 `messages` 스트림에 **툴 콜**로 드러난다 → 일반 도구처럼 렌더하는 것이 권장 표현.
- 임계값은 **model-aware**(컨텍스트 크기 필요) → 공백 #1을 풀어야 정확해진다.

## 2. 목표 / 성공 기준

- [ ] `models.context_window`가 단일 source of truth가 되고, **게이지·자동압축·수동압축이 모두 같은 숫자**를 쓴다.
- [ ] 사용자가 (a) 대화로 "압축해줘", (b) 컴포저 게이지 옆 **compact 버튼** 두 경로로 수동 압축을 실행할 수 있다.
- [ ] 압축이 일어나면 UI로 인지 가능하다: 수동=툴 pill+승인 카드, 자동=인라인 "요약됨" 마커 + 원본 보기.
- [ ] `openai_compatible`/커스텀 모델에서도 압축 임계값이 의도대로 동작(검증 포함).

### 비목표 (Out of scope)
- **clear(대화 초기화)**: 기존 **"새 대화"(new conversation)**가 그 의도를 이미 충족 → 별도 in-thread clear는 만들지 않는다. (원하면 게이지 옆에 "새 대화" 단축만 재노출 — 선택)
- deepagents 버전 업그레이드.

## 3. 설계 — Phase 0/1/2

### Phase 0 — context_window 단일 소스화 (토대, 먼저)

가장 중요. 이걸 안 하면 게이지 비활성 + 임계값 들쭉날쭉이라 1·2의 효과가 반감.

1. **채우기**
   - `backend/app/seed/default_models.py`: 정식 모델별 `context_window` seed (예: Claude 200000, GPT-4o 128000, Gemini 1.5/2.x 등). 출처 명시 주석.
   - 모델 생성/수정 UI(`frontend/.../settings/models`, 백엔드 `schemas/model.py`는 이미 `context_window` 필드 보유)에 **입력 필드 추가** — 운영자가 커스텀/게이트웨이 모델에 직접 지정.
   - (선택) LangChain 프로필에서 **자동 derive**: 모델 생성 시 `init_chat_model(...).profile["max_input_tokens"]`가 있으면 기본값으로 채우기.

2. **주입(핵심) — 압축이 우리 숫자를 쓰게 한다.** 다음 중 택1 (구현 시 검증 필요):
   - **(A) 모델 프로필에 주입**: `model_factory`에서 생성한 LangChain 모델의 `.profile["max_input_tokens"]`를 우리 `context_window`로 설정 → deepagents 자동 압축이 그대로 우리 숫자 사용. *최소 침습이나 `.profile` 설정 가능 여부 확인 필요.*
   - **(B) 명시 미들웨어로 교체**: 자동 주입된 summarization을 deepagents alias로 제외하고, `create_summarization_middleware(model, backend, SummarizationMiddlewareOptions(trigger=("tokens", int(context_window*0.85)), ...))`를 우리가 직접 추가. *명시적·제어 쉬움. ADR-012의 "auto-injected 회피 + 명시 인스턴스" 패턴과 일관.*
   - → **권장: (B)** (제어/일관성). `context_window` 없으면 deepagents 기본(프로필/고정 fallback) 유지.

3. **검증**: 주력 모델(Anthropic/OpenAI/openai_compatible)에서 자동 압축이 의도한 토큰에서 트리거되는지 + 게이지 %와 일치하는지 E2E/통합 테스트.

**done-when**: 게이지가 실모델에서 활성 표시 / 압축 트리거 토큰 = `context_window*0.85` / openai_compatible도 정상.

### Phase 1 — 수동 compact 도구 기본 켜기 (작은 백엔드)

- `runtime_component_builder`에 `create_summarization_tool_middleware`(또는 `SummarizationToolMiddleware`) 추가 → `compact_conversation` 도구 노출.
- approval-by-default라 호출 시 **기존 HiTL 승인 카드가 자동으로 뜬다** → 별도 UI 없이 "압축할까요?" 표현 확보.
- 게이트: deepagents가 사용량 **~50% 미만이면 도구 사용 차단**(너무 일찍 압축 방지) — 동작 확인.
- 트리거 모드(스케줄)에선 HiTL 비활성이므로 compact 도구도 자동 승인/생략 정책 결정 필요.

**done-when**: 대화로 "압축해줘" → 에이전트가 `compact_conversation` 호출 → 승인 카드 → 승인 시 요약+오프로드 동작.

### Phase 2 — UI: compact 버튼 + 자동압축 마커

1. **수동 compact UI (대부분 기존 인프라 재사용)**
   - `compact_conversation` 툴 콜 → **기존 툴 pill**로 렌더. `tool-icons.ts`에 `compact_conversation → 🗜️`(예: `ArchiveIcon`/`Minimize2Icon`) 추가.
   - 게이지 옆 **compact 버튼**(`context-window-gauge.tsx` 인근): 클릭 → compact 트리거.
     - 트리거 방식 v1: **방식 A** — 숨은 압축 요청을 에이전트에 전송 → 도구 호출(LLM 턴 1회). v2: 전용 엔드포인트로 턴 없이 직접 실행(후속).
     - **≥50% 게이트 미만이면 버튼 비활성** + 툴팁("아직 압축할 만큼 차지 않았어요").
   - 승인 카드 = "압축 진행" 표현. 별도 스피너 불필요(게이지 하락으로 결과 가시화).

2. **자동 압축(85%) 마커 (신규 감지 필요)**
   - 자동 압축은 툴 콜이 아니라 **요약 HumanMessage 삽입**으로 드러남(`_is_summary_message`: 내용이 `Here is a summary of the conversation to date` / `<summary>` + `/conversation_history/...` 경로).
   - 프론트에서 그 메시지를 감지 → **인라인 마커** 렌더: "🗜️ 이전 대화를 요약해 컨텍스트를 정리했어요 · 원본 보기" (원본 = 오프로드 파일을 `read_file`/아티팩트로 열기).
   - 또는 백엔드 streaming에서 압축 step을 전용 SSE 이벤트로 emit(더 견고하지만 작업 큼) — v2.

**done-when**: 수동 compact가 툴 pill+승인으로 보이고, 자동 압축 후 인라인 마커가 뜨며 원본 열람 가능.

## 4. 영향 파일 (예상)
- 백엔드: `seed/default_models.py`, `schemas/model.py`(필드 이미 있음), `routers/models` (생성/수정), `agent_runtime/model_factory.py` 또는 `runtime_component_builder.py`(주입/미들웨어), `agent_runtime/middleware_registry.py`(summarization 제외/명시), streaming(자동압축 이벤트, v2).
- 프론트: `settings/models` 폼(필드), `lib/chat/tool-icons.ts`(compact 아이콘), `context-window-gauge.tsx`/`assistant-thread.tsx`(버튼), 요약 메시지 감지 + 인라인 마커 컴포넌트, i18n.

## 5. 열린 결정 (구현 전 확인)
- Phase 0 주입: (A) 프로필 주입 vs (B) 명시 미들웨어 — 권장 (B), 단 `.profile` 가능 여부로 (A) 재검토.
- compact 버튼 트리거: v1 방식 A(턴 소모) 수용 가능한가, 바로 전용 엔드포인트(B)로 갈까.
- 트리거(스케줄) 모드에서 compact 도구/자동압축 정책.
- 자동압축 마커: 메시지 감지(가벼움) vs 전용 SSE 이벤트(견고) — v1은 감지.

## 6. 검증 계획
- 단위: context_window 주입/임계값 계산, 요약 메시지 감지 로직, tool-icon 매핑.
- 통합/E2E: 실모델에서 자동 압축 트리거 토큰 = 기대값, 게이지 일치; "압축해줘" → 승인 카드 → 오프로드 파일 생성; compact 버튼 ≥50% 게이트; 자동압축 후 인라인 마커 + 원본 열람.
- 캡쳐: 수동 compact(툴 pill+승인), 자동압축 마커, 게이지 하락 before/after.
