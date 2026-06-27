# 개발 기획서 — 자동압축 표시(마커) B-풀버전: "압축 중… → 요약 완료"

> 이 문서 **하나만 보고** 처음부터 끝까지 구현 가능하도록, 실제 소스 기준 **파일:라인 + 검증된 사실 + 변경 스니펫 + 테스트 + 리스크**를 명시한다.
> 대상 버전: deepagents `0.6.9`, langchain `1.4.x`, Moldy 브랜치 `feature/context-compaction`(Phase 0 커밋 `c1b03660` 이후).
> 선행: Phase 0(`models.context_window` 단일화 + 게이지 + 자동압축 임계값 교정)는 **이미 완료·검증·커밋됨**. 이 문서는 그 위에 얹는 **표시(마커)** 작업이다.
> 범위: **자동압축이 일어날 때 (a) 진행 중 "압축 중…" 일시 상태 + (b) 완료 후 "이전 대화를 요약했어요 · 원본 보기" 영구 마커** 를 v3 채팅(프로덕션)에 띄운다. 부수적으로 **요약 토큰 누수(answer 오염) 차단**(LangChain 공식 권장)을 함께 한다.

---

## 0. 한눈에 — 무엇을 / 어디에

| 단계 | 사용자 경험 | 핵심 변경 |
|------|------------|-----------|
| 압축 진행 중 | 응답 스피너 라벨이 **"압축 중…"** 으로 바뀜 (답변 생성과 구분) | 백엔드: 요약 토큰 감지 → `compaction(state=running)` 이벤트 emit + 요약 토큰 **suppress** / 프론트: transient activity |
| 압축 완료 | "압축 중…" 사라지고 답변이 흐르며, 그 턴에 작게 **"이전 대화를 요약했어요 · 원본 보기"** 한 줄이 영구 표시 | 백엔드: `_summarization_event` 감지 → `compaction(state=done, offload_path)` emit / 프론트: 메시지 메타 attach → 인라인 마커 렌더 |

**핵심 한 줄:** deepagents 0.6.9 자동압축은 요약을 `messages`에 남기지 않고 스트림 메타데이터(`lc_source=summarization`)와 state(`_summarization_event`)로만 드러낸다 → **백엔드 스트림 어댑터에서 감지/emit** 하고 프론트는 그 이벤트를 렌더한다. (LangChain 공식 deepagents *context-engineering* 문서가 `metadata.lc_source=="summarization"` 감지를 권장.)

---

## 1. 선행 사실 (모두 실측/소스 확인 완료)

### 1.1 deepagents 0.6.9 자동압축의 실제 동작
- `create_deep_agent`는 `create_summarization_middleware(model, backend)`를 기본 스택에 주입한다(`graph.py:779`, 서브에이전트 `:626/:702`). 트리거는 `compute_summarization_defaults(model)`이 `model.profile["max_input_tokens"]`로 계산 → **Phase 0가 채운 `context_window` 기준 `("fraction", 0.85)`**.
- **요약은 `messages` state에 들어가지 않는다.** `_DeepAgentsSummarizationMiddleware.wrap_model_call`(summarization.py:1003~)은 docstring 그대로 *"does NOT modify the LangGraph state. Instead, it tracks summarization events in middleware state(`_summarization_event`)"*. 요약은 **모델 요청에만 transient 적용**되고, persisted `messages`는 원본 유지.
- 따라서 "요약 메시지를 messages에서 찾는" 방식(구 `before_model` 가정)은 0.6.9에서 **동작하지 않는다.** 이게 Phase 1 재설계의 이유.

### 1.2 압축은 "답변 직전 단계"라 입력 불가 구간과 겹친다
순서(한 턴 안):
```
[사용자 전송] → 옛 대화 요약(LLM 1회, 압축 중) → 실제 답변 생성 → [완료]
```
이 턴이 도는 동안 컴포저는 이미 "응답 중(Stop)" 상태 → **압축 중 별도 입력 불가**(클로드코드와 동일). 부족한 건 "왜 기다리는지"를 알려주는 표시뿐.

### 1.3 v3(프로덕션) 스트림에서 압축 신호가 드러나는 형태 — **실측**
프로덕션 채팅은 `runtimeMode==='langgraph_v3'`(기본값, `frontend/src/lib/chat/runtime-mode.ts`)이고, 백엔드는 `astream_events(version="v3")`를 쓴다(`langgraph_streaming.py:_open_v3_stream` L72-83). 작은 `context_window`로 압축을 강제하고 v3 이벤트를 떠보면:

```
v3 methods: {'values': 2, 'messages': 10}
lc_source=summarization 운반: method=='messages' (payload, metadata) 튜플의 metadata.lc_source
_summarization_event 운반: method=='values' 의 data 안 {cutoff_index, summary_message, file_path}
```

즉 v3 이벤트에서:
- **요약 생성 토큰** = `method=="messages"` 이벤트의 `metadata.lc_source == "summarization"`.
- **압축 확정 신호** = `method=="values"` 이벤트의 `data["_summarization_event"]`(키: `cutoff_index`, `summary_message`, `file_path`).

타임라인:
```
messages(lc_source)×N   ← 압축 중 (요약 토큰)        → state=running
values(_summarization_event, cutoff_index>0)         → state=done (+offload_path)
messages(lc_source 없음)×M ← 실제 답변
```

> ⚠️ **현재 누수 위험:** `adapt_v3_protocol_event`(langgraph_protocol_adapter.py:37) → `_normalize_protocol_data`(:140) → `_message_payload_with_metadata`(:151-163)가 `metadata.lc_source`를 메시지 payload에 **그대로 합쳐 프론트로 보낸다.** 요약 토큰(`messages`)을 그대로 흘리면 assistant-ui LangGraph SDK가 **요약 텍스트를 답변/유령 메시지로 렌더**할 수 있다. LangChain 공식 문서도 이 토큰을 **필터링**한다 → 본 작업에서 suppress 한다.

### 1.4 오프로드 파일 경로
Moldy 백엔드는 `FilesystemBackend(root_dir=_DATA_DIR, virtual_mode=True)`(runtime_component_builder.py:603) — **CompositeBackend 아님** → `artifacts_root="/"` → 오프로드 경로는 결정적으로 **`/conversation_history/{thread_id}.md`**. 단, 가능하면 `_summarization_event["file_path"]`를 직접 쓰고, 없을 때만 이 규칙으로 derive 한다(미래 backend 교체 안전).

### 1.5 두 개의 스트리밍 경로 (production = v3)
| 경로 | 사용처 | 백엔드 | 프론트 변환 |
|------|--------|--------|-------------|
| **v3 (프로덕션)** | `langgraph_v3` 기본 | `langgraph_streaming.py`(`astream_events v3`) + `adapt_v3_protocol_event` | LangGraph 런타임(`use-moldy-langgraph-stream.ts`) |
| legacy | `NEXT_PUBLIC_CHAT_RUNTIME=legacy` | `streaming.py`(`stream_mode="messages"`) | `use-chat-runtime.ts` + `convert-message.ts` |

→ **v3가 필수, legacy는 선택(권장).** 둘 다 같은 신호(`lc_source` / `_summarization_event`)를 쓰므로 동일 로직을 두 경로에 둔다.

---

## 2. 설계

### 2.1 이벤트 계약 (백엔드 → 프론트)
프론트 `custom` 채널로 단일 side-channel 이벤트를 보낸다. method = `custom:moldy.compaction`(또는 `custom`+`name="moldy.compaction"`). data payload:

```jsonc
// 압축 시작
{ "state": "running" }
// 압축 완료
{ "state": "done", "offload_path": "/conversation_history/{thread_id}.md", "cutoff_index": 12 }
```

- **run당 최대 1쌍(running→done)** 으로 dedup. (한 턴에 압축은 보통 1회. 멀티스텝 런에서 2회 이상이면 각 쌍을 emit해도 되지만 v1은 **run당 1회**로 단순화 + `log` 남김.)
- 이벤트는 기존 emit 경로(`stored_custom_protocol_event`)로 나가 **persist + broker + replay** 가 공짜로 된다(아래 3.1).

### 2.2 백엔드 책임
1. **감지**: v3 루프에서 adapted event를 보고
   - `method=="messages"` & `data.metadata.lc_source=="summarization"` → 요약 토큰.
   - `method=="values"` & `data._summarization_event.cutoff_index>0` → 압축 확정.
2. **suppress**: 요약 토큰(`messages` w/ lc_source) 이벤트는 **프론트로 yield하지 않는다**(누수 차단).
3. **emit**: 첫 요약 토큰에서 `compaction(running)` 1회, `_summarization_event` 도착에서 `compaction(done, offload_path)` 1회.

### 2.3 프론트 책임 (v3 LangGraph 런타임)
1. **transient "압축 중…"**: `compaction(running)` → `done` 사이 동안 표시. 기존 activity 인프라(`RunActivity`)에 `kind:'compaction'` 추가 → 로딩 인디케이터가 렌더.
2. **영구 "이전 대화를 요약했어요 · 원본 보기"**: `compaction(done)` → 해당 assistant 턴 메시지 메타에 attach → `AssistantMessageParts` 근처에서 인라인 마커 렌더. (persist된 이벤트가 reload 시 replay → 재attach → 영구.)

---

## 3. 구현 — 백엔드

### 3.1 이벤트 헬퍼 (계약 + emit 수단 확인)
- `RAW_PROTOCOL_METHODS`(langgraph_protocol_adapter.py:18-)에 `"custom"` 이미 포함.
- side-channel emit 도구: `app/agent_runtime/protocol_events.py:stored_custom_protocol_event(name, data, ...)` (protocol_side_effects.py가 이미 사용). 이걸로 `name="moldy.compaction"` 이벤트를 만든다.
- **event_names.py**: 백엔드 상수는 필수는 아니나 가독성 위해 추가 권장:
  ```python
  # event_names.py
  COMPACTION: Final = "moldy.compaction"   # custom side-channel name
  ```

### 3.2 v3 경로 감지/suppress/emit — `langgraph_streaming.py`
대상: `stream_agent_response_langgraph`의 v3 루프(현재 L275-309 `else: async for raw_event in stream:`).

감지 헬퍼(같은 모듈 또는 `langgraph_protocol_adapter.py`에 추가):
```python
def _compaction_signal(event: StoredProtocolEvent) -> str | None:
    """adapted protocol event에서 압축 신호를 분류.
    returns: "summary_token" | "committed" | None
    """
    data = event.get("data")
    method = event.get("method")
    if method == "messages" and isinstance(data, Mapping):
        md = data.get("metadata")
        if isinstance(md, Mapping) and md.get("lc_source") == "summarization":
            return "summary_token"
    if method == "values" and isinstance(data, Mapping):
        ev = data.get("_summarization_event")
        if isinstance(ev, Mapping) and isinstance(ev.get("cutoff_index"), int) and ev["cutoff_index"] > 0:
            return "committed"
    return None

def _compaction_offload_path(event: StoredProtocolEvent, thread_id: str) -> str | None:
    data = event.get("data")
    if isinstance(data, Mapping):
        ev = data.get("_summarization_event")
        if isinstance(ev, Mapping) and isinstance(ev.get("file_path"), str):
            return ev["file_path"]
    return f"/conversation_history/{thread_id}.md" if thread_id else None
```

루프 변경(스니펫, 기존 `yield await emit(event)` 직전/직후):
```python
# 루프 진입 전 상태:
_compaction_running_emitted = False
_compaction_done_emitted = False

# ... async for raw_event in stream:
event = adapt_v3_protocol_event(raw_event, run_id=msg_id, thread_id=thread_id)
if _is_empty_input_requested_event(event):
    deferred_empty_input_requested = event
    continue

signal = _compaction_signal(event)
if signal == "summary_token":
    # 1) 요약 토큰은 답변 오염 방지 위해 프론트로 보내지 않는다(누수 차단).
    # 2) 첫 토큰에서 "압축 중" 1회 emit.
    if not _compaction_running_emitted:
        _compaction_running_emitted = True
        side_effect_seq += 1
        yield await emit(stored_custom_protocol_event(
            name=event_names.COMPACTION, run_id=msg_id, thread_id=thread_id,
            seq=side_effect_seq, data={"state": "running"},
        ))
    continue  # ← suppress: 요약 토큰 자체는 yield 안 함
if signal == "committed" and not _compaction_done_emitted:
    _compaction_done_emitted = True
    side_effect_seq += 1
    yield await emit(stored_custom_protocol_event(
        name=event_names.COMPACTION, run_id=msg_id, thread_id=thread_id,
        seq=side_effect_seq, data={
            "state": "done",
            "offload_path": _compaction_offload_path(event, thread_id),
            "cutoff_index": event["data"]["_summarization_event"]["cutoff_index"],
        },
    ))
    # values 이벤트 자체는 기존대로 계속 흘려보낸다(상태 동기화 유지) → 아래 yield 유지

yield await emit(event)
# (이후 usage/side-effect 수집 로직은 그대로)
```
> ⚠️ `stored_custom_protocol_event`의 정확한 시그니처(seq/event_id/namespace 인자)는 `protocol_events.py:75-148`에서 확인 후 맞춘다. side-effect 이벤트들이 `side_effect_seq`를 증가시키며 쓰는 패턴(`collect_protocol_side_effect_events`)을 그대로 따른다.

> ⚠️ **fallback 경로(L241-274, `_open_stream_mode_fallback`, 테스트 fake용)** 도 같은 처리를 추가해야 일관. 거기선 `adapt_stream_mode_chunk`가 (mode,data) 튜플을 adapt하므로 동일 `_compaction_signal` 재사용 가능.

### 3.3 legacy 경로 (선택, 권장) — `streaming.py`
대상: `stream_agent_response`의 `async for chunk in agent.astream(stream_mode="messages")`(L394-). `msg, metadata = chunk` 직후, `builder:internal` skip(L402) 다음에:
```python
if (metadata or {}).get("lc_source") == "summarization":
    if not _compaction_emitted:
        _compaction_emitted = True
        yield emit(event_names.COMPACTION, {"state": "running"})
    continue  # suppress 요약 토큰
```
legacy는 `_summarization_event`(updates/values)를 안 받으므로 `done`은 **첫 비-요약 토큰 전환** 또는 스트림 종료 시 1회 emit(`{"state":"done","offload_path": f"/conversation_history/{thread_id}.md"}`). thread_id는 `config["configurable"]["thread_id"]`.
> legacy는 프로덕션 아님 → v1에서 **running만**(누수 차단 + "압축 중") 해도 허용. done 마커는 v3 우선.

### 3.4 redaction / persist 확인
- `redact_private_reasoning`(adapter)와 `protocol_redaction.py`가 새 custom 이벤트 data(`offload_path` 등)를 깨지 않는지 확인. `offload_path`는 민감정보 아님(경로). 시크릿 마스킹 대상 아님.
- custom 이벤트는 기존 emit 경로로 persist(`message_events`)되어 **reload replay** 됨 → 프론트 영구 마커의 근거.

---

## 4. 구현 — 프론트 (v3 LangGraph 런타임)

경로 전제: `chat-runtime-section.tsx`가 `runtimeMode==='langgraph_v3'`일 때 `useMoldyLangGraphStream`(`use-moldy-langgraph-stream.ts`) 사용. custom 이벤트는 `['custom']` 채널로 들어옴(`activity-protocol.ts`의 `ActivityProtocolMethod`에 `custom:${string}` 이미 존재).

### 4.1 compaction 이벤트 파싱 훅 — 신규 `langgraph-runtime/compaction-events.ts`
패턴 출처: `memory-events.ts`(useLangGraphMemoryEffects, custom 이벤트 파싱/dedup) + `usage-events.ts`(message attach). 신규 훅:
- `['custom']` 채널 구독, `customName(event)==='moldy.compaction'` 필터.
- `state==='running'|'done'` 파싱(Zod 또는 타입가드; `isCompactionPayload`).
- 반환:
  - `compactionStatus: 'idle' | 'running'`(transient, running 수신~done 수신까지)
  - `compactionByRunId: Map<runId, {offloadPath?}>`(done 시 기록) → 메시지 attach용.
- dedup: `event_id`(`memory-events.ts:84-89` 패턴).

### 4.2 transient "압축 중…" — activity 또는 status flag
두 방식 중 택1(권장 A):
- **A. activity 주입(권장)**: `activity-model.ts`의 `RunActivityKind`에 `'compaction'` 추가. compaction-events 훅이 `running` 동안 `{kind:'compaction', status:'running', label}` activity를 `activities`에 합류 → `StreamingMessageLoadingIndicator`(assistant-message-loading.tsx:76-114)가 `RunActivityStrip`으로 렌더(기존 인프라 그대로). `done`이면 제거(또는 complete 후 사라짐).
- **B. status flag**: 훅이 `compactionStatus`를 노출 → 로딩 인디케이터가 `WittyLoadingMessage` 대신 `t('chat.compaction.running')`("압축 중…")을 표시.
- A가 기존 activity 렌더/정렬 인프라를 재사용해 더 견고. `run-activity-strip.tsx`/`activity-model.ts`에 kind 라벨/아이콘만 추가.

### 4.3 영구 마커 "이전 대화를 요약했어요 · 원본 보기"
- compaction-events 훅의 `compactionByRunId` → 해당 assistant 메시지 메타에 attach: `usage-events.ts`의 `withUsage`/`attachUsageToMessages`(메시지 배열에 metadata.custom 합치기) 패턴을 그대로 본떠 `attachCompactionToMessages(messages, compactionByRunId)` 작성 → `metadata.custom.compaction = {offloadPath}`.
  - run→message 매핑은 usage-events가 이미 쓰는 `runMessageIds`(usage-events.ts:393-400) 매핑을 참고/재사용.
- 신규 컴포넌트 `components/chat/compaction-summary.tsx`(Phase 1 시도분 재작성):
  ```tsx
  export function CompactionSummary({ offloadPath }: { offloadPath?: string }) {
    const t = useTranslations('chat.compaction')
    // 아이콘(lucide Minimize2Icon) + t('summary') + (offloadPath ? 클립보드 복사 "원본 보기" 버튼)
    // design-system: text-xs text-muted-foreground / text-primary-strong, rounded-md 이내, 시맨틱 색만
  }
  ```
- 렌더 위치: `assistant-thread.tsx`의 `AssistantMessageParts`(L236 부근) 직후 또는 `AssistantArtifactCards`(L248-303) 인접. `useAuiState((s)=> (s.message?.metadata as {custom?:{compaction?:{offloadPath?:string}}})?.custom?.compaction)`로 읽어 있으면 `<CompactionSummary/>` 렌더.
  - ⚠️ **selector reference-stable**: 빈 기본값은 모듈 상수(`const EMPTY = {}`)로 — 매 렌더 새 객체 반환 시 무한 리렌더(과거 Phase 2a에서 겪은 `useAuiState` 버그).

### 4.4 i18n
`frontend/messages/ko.json` + `en.json` `chat` 네임스페이스에:
```jsonc
"chat": {
  "compaction": {
    "running": "이전 대화를 압축하는 중…",   // en: "Compacting earlier messages…"
    "summary": "이전 대화를 요약해 컨텍스트를 정리했어요",  // en: "Older messages were summarized to free up context"
    "viewOriginal": "원본 보기",  // en: "View original"
    "copied": "경로 복사됨"        // en: "Path copied"
  }
}
```
activity 라벨을 쓰면 `chat.activity.compaction`도 함께. 작성 후 `pnpm lint:i18n`.

---

## 5. 변경 파일 요약

**백엔드**
- `app/agent_runtime/event_names.py` — `COMPACTION` 상수.
- `app/agent_runtime/langgraph_streaming.py` — v3 루프 + fallback 루프에 감지/suppress/emit.
- `app/agent_runtime/langgraph_protocol_adapter.py` — `_compaction_signal`/`_compaction_offload_path` 헬퍼(또는 streaming 모듈에).
- (선택) `app/agent_runtime/streaming.py` — legacy 경로 동일 처리.
- `app/agent_runtime/protocol_events.py` — `stored_custom_protocol_event` 시그니처 확인(변경 불필요 예상).

**프론트**
- `src/lib/chat/langgraph-runtime/compaction-events.ts`(신규) — custom 이벤트 파싱/dedup/attach.
- `src/lib/chat/langgraph-runtime/activity-model.ts` — `RunActivityKind`에 `'compaction'`(방식 A).
- `src/lib/chat/langgraph-runtime/use-moldy-langgraph-stream.ts` — 훅 배선(반환에 compaction 합류, 메시지 attach).
- `src/components/chat/assistant-message-loading.tsx` — transient "압축 중" 렌더(activity 또는 flag).
- `src/components/chat/compaction-summary.tsx`(신규) — 영구 마커.
- `src/components/chat/assistant-thread.tsx` — `AssistantMessageParts` 인접에 마커 렌더 분기.
- `frontend/messages/ko.json` + `en.json` — i18n.

---

## 6. 리스크 & 조심할 것 (★ hot path)

| # | 리스크 | 대응 |
|---|--------|------|
| R1 ★ | **요약 토큰 suppress가 과해 일반 답변 토큰까지 누락** | suppress 조건을 `lc_source=="summarization"` **정확 일치**로만. 회귀 테스트: 압축 발생 런에서 답변 content 무손실 단언(Level 2). |
| R2 ★ | suppress 누락 → 요약 텍스트가 답변/유령 메시지로 **누수** | v3 `messages`+lc_source, legacy metadata.lc_source 둘 다 차단. 누수 단언 테스트. |
| R3 | compaction 이벤트 **중복/순서** (running 여러 번, done 먼저) | run당 `_running/_done` 플래그 1회. 멀티스텝 시 run당 1쌍으로 제한 + `log`. |
| R4 | reload 시 마커 **사라짐/중복** | 이벤트가 `message_events`로 persist→replay됨을 확인. 프론트 dedup(event_id). |
| R5 | `useAuiState` selector 무한 리렌더 | 기본값 모듈 상수, reference-stable. |
| R6 | usage 누락(요약 토큰 skip으로 요약 LLM 비용 미집계) | v1 허용(내부 오버헤드). 필요시 별도 usage 채널로 분리 집계(후속). `log`로 가시화. |
| R7 | trigger(스케줄) 모드 | trigger executor도 같은 스트림 경로면 자동 적용. HiTL 무관. 캡쳐로 확인. |
| R8 | feature flag | `MOLDY_COMPACTION_MARKER_ENABLED`(env, 기본 on) 또는 settings로 감싸 문제 시 즉시 off 가능(권장). |

**되돌리기**: DB 마이그레이션 없음 → 문제 시 PR revert로 완전 복구. Phase 0와 **PR 분리**(Phase 0는 이미 커밋됨).

---

## 7. 테스트 전략 (§Phase 0와 동일 원리 — 85% 안 채우고 `context_window`를 작게)

### Level 1 — 프론트 단위 (vitest)
- `compaction-events.ts`: mock custom 이벤트(`{method:'custom:moldy.compaction', params:{data:{state:'running'}}}` / `done`) → status 전이 + `compactionByRunId` attach 단언. (`memory-tool-ui.test.ts` 스타일)
- `attachCompactionToMessages`: 메시지 배열에 `metadata.custom.compaction` 합쳐지는지.
- `compaction-summary.tsx`: "요약했어요"+"원본 보기"(offloadPath 유무) 렌더, user 말풍선 아님.

### Level 2 — 백엔드 통합 (pytest, 브라우저 X) ★가장 중요
`context_window=1500`(또는 50) 모델로 deep agent 빌드 → `astream_events(version="v3")`를 직접 돌려:
- 압축 발생 시 **`compaction(running)` 1회 + `compaction(done, offload_path)` 1회** 가 yield되는지.
- **요약 토큰(messages+lc_source)이 yield되지 않는지(suppress)** + 최종 답변 content 무손실.
- `stream_agent_response_langgraph`를 직접 호출하는 통합 테스트가 이상적(기존 `tests/agent_runtime/test_langgraph_*` 참고). fake 모델은 `GenericFakeChatModel` + `bind_tools`→self, `profile={"max_input_tokens":50}`.

> 재현 핵심: fake 모델 멀티턴으로 토큰 누적 > `0.85×window` → 압축 발생. (검증 스니펫 §9)

### Level 3 — E2E (선택, 결정론적)
- **scripted `E2E_COMPACTION` 마커**(`e2e_scripted_model.py`): 입력에 마커가 있으면 결정론적으로 `compaction` 이벤트 시퀀스를 방출 → 프론트 마커 렌더 단언/캡쳐. 트리거 로직과 분리해 flaky 제거. (기존 `E2E_TOOL_GROUP` 패턴)
- 또는 tiny-context 실모델로 2~3턴 → "압축 중…" + 영구 마커 캡쳐.

### 검증 매트릭스
| 대상 | 방법 | 85%? |
|------|------|------|
| 이벤트 emit(running/done) + suppress | Level 2(window=50, astream_events v3) | ❌ |
| 답변 무손실(누수 X) | Level 2 단언 | ❌ |
| 파싱/attach/렌더 | Level 1 단위 | ❌ |
| 마커 UI 전체 | scripted E2E or tiny-context | ❌ |

---

## 8. 완료 기준 (done-when)
- [ ] 압축 발생 런에서 백엔드가 `compaction(running)`→`compaction(done,offload_path)`를 **각 1회** emit하고, 요약 토큰은 프론트로 가지 않는다(누수 0).
- [ ] 일반(압축 없는) 턴에는 compaction 이벤트가 전혀 없고 답변 동작 무회귀.
- [ ] v3 프로덕션 채팅에서 압축 시 **"압축 중…"** 표시 → 사라지고 **"이전 대화를 요약했어요 · 원본 보기"** 영구 표시, reload 후에도 유지.
- [ ] tsc 0 / vitest / 백엔드 ruff+pytest / lint(i18n·design-system) 그린.
- [ ] 실서버(또는 scripted) 캡쳐: "압축 중…" + 영구 마커 + (게이지 압축 후 하락).
- [ ] feature flag로 off 가능. Phase 0와 별도 PR.

---

## 9. 부록 — 압축 재현/검증 스니펫 (구현 중 그대로 사용)

작은 window로 자동압축을 강제하고 v3 이벤트를 확인:
```python
import itertools, asyncio, inspect, warnings
warnings.filterwarnings("ignore")
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from deepagents import create_deep_agent

class FakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs): return self

fake = FakeModel(messages=itertools.cycle([AIMessage(content="요약/응답")]))
fake.profile = {"max_input_tokens": 50}   # 작게 → 즉시 압축
agent = create_deep_agent(model=fake, tools=[], system_prompt="sys", checkpointer=InMemorySaver())
cfg = {"configurable": {"thread_id": "t"}}
agent.invoke({"messages": [HumanMessage(content="첫 질문 " * 30)]}, cfg)  # 히스토리 누적

async def go():
    s = agent.astream_events({"messages": [HumanMessage(content="둘째 " * 30)]}, cfg, version="v3")
    if inspect.iscoroutine(s): s = await s
    async for ev in s:
        params = (ev or {}).get("params") or {}
        data = params.get("data")
        if ev.get("method") == "messages" and isinstance(data, (list, tuple)) and len(data) == 2:
            md = data[1] if isinstance(data[1], dict) else {}
            if md.get("lc_source") == "summarization":
                print("요약 토큰(suppress 대상)")
        if ev.get("method") == "values" and isinstance(data, dict) and "_summarization_event" in data:
            print("압축 확정:", data["_summarization_event"]["file_path"])
asyncio.run(go())
```
기대 출력: "요약 토큰…" 여러 번 → "압축 확정: /conversation_history/t.md".

---

## 10. 작업 순서
```
1) 백엔드 v3 감지/suppress/emit (langgraph_streaming + adapter 헬퍼) + Level 2 통합 테스트(누수 0 + emit 1쌍)
2) 백엔드 legacy 동일 처리(선택) + event_names 상수
3) 프론트 compaction-events 훅 + Level 1 단위
4) transient "압축 중"(activity kind) + 영구 마커(compaction-summary + assistant-thread 분기) + i18n
5) (선택) scripted E2E_COMPACTION + 캡쳐
6) feature flag, /code-review, PR(Phase 0와 분리)
```
