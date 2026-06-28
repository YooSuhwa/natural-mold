# 채팅 Generative UI — 개발 기획 문서 (구현 준비 완료)

> 이 문서 하나만 보고 처음부터 끝까지 구현할 수 있도록 작성한다. 가정/근거/파일 위치/코드 스켈레톤/검증 절차를 모두 포함한다. 추측이 필요한 지점은 **검증 스파이크**로 먼저 확인한다.

- **상태**: 설계 확정, 미구현
- **대상 런타임**: v3 채팅(`langgraph_v3`, `use-moldy-langgraph-stream.ts` + `useExternalStoreRuntime`)
- **assistant-ui**: 0.14.18 (업그레이드 불필요 — 근거는 §2.3)
- **관련 선례 문서**: `docs/design-docs/chat-attachments-dev-plan.md` (커밋 분리·검증·throwaway E2E 스택 패턴을 그대로 따른다)

---

## 0. TL;DR

1. **목표**: AI 도구 결과를 "문자열/JSON pretty-print"가 아니라 **타입별 React 컴포넌트**(DataTable/Chart/StatsDisplay/Terminal …)로 렌더한다. 이는 assistant-ui가 말하는 **"LangGraph Generative UI"의 Moldy-네이티브 구현**이다 — 백엔드가 `{type, props}` 타입드 페이로드를 push하고, 프론트가 **allowlist 레지스트리 + Zod 검증**으로 컴포넌트를 고른다.
2. **왜 "네이티브 구현"인가**: Moldy는 assistant-ui의 LangGraph **Cloud** 런타임(`@assistant-ui/react-langgraph` / `useLangGraphRuntime`)을 쓰지 않고 **자체 SSE agent-protocol + `useExternalStoreRuntime`** 을 쓴다(§1). 따라서 LangGraph Cloud의 `push_ui_message`/`custom` 채널 경로를 그대로 못 쓰고, **동일 개념을 Moldy 프로토콜에 얹는다**. 단 렌더링은 assistant-ui 0.14.18에 이미 있는 **`makeAssistantDataUI` 데이터-파트 API**를 쓴다.
3. **전송 메커니즘은 FILE_EVENT(아티팩트) 선례를 그대로 복제한다** — 이미 검증된 "백엔드 typed 이벤트 → 프론트 custom 채널 소비 → store → 메시지에 부착 → 렌더" 파이프라인(§3).
4. **순서(사용자 요구)**:
   - **Phase 1**: Generative UI **인프라**만 구축(전송 계약 + 1개의 데모 타입) → **회귀 0** 을 실서버 + 화면별 캡쳐로 증명 → 그 후에야 컴포넌트 추가.
   - **Phase 2**: DataTable → Chart → StatsDisplay → Terminal 을 **하나씩** 추가, 각 단계마다 테스트 + 캡쳐.
5. **범위 밖**: payload-type 전면 전환(모든 도구 결과 계약화), assistant-ui toolkit 마이그레이션, 0.14.24 업그레이드. 이 문서는 **추가형(additive)** 이라 기존 tool-ui(`makeAssistantToolUI`) 렌더를 건드리지 않는다.

---

## 1. 배경: Moldy는 왜 자체 런타임인가 (확정 사실)

- 프론트 v3 런타임은 `frontend/src/lib/chat/langgraph-runtime/use-moldy-langgraph-stream.ts:2798` 의 **`useExternalStoreRuntime`** 로 만든다. `@assistant-ui/react-langgraph` 는 **의존성에 없다**(package.json에 `@assistant-ui/react`, `@assistant-ui/react-langchain` 만 존재). 코드의 `useLangGraphRuntime` 식별자는 **boolean 플래그**일 뿐 assistant-ui hook이 아니다.
- 백엔드는 `backend/app/routers/conversation_agent_protocol_*.py` (~25개)로 된 **자체 agent-protocol over SSE** 다 (commands/event_normalization/thread_stream/resume/redaction/state_snapshot …). LangGraph Cloud/Platform 배포가 아니라 **FastAPI + deepagents 라이브러리 사용**.
- **결론**: "정식 LangGraph 런타임으로 전환"은 제품 재설계급(멀티유저 인증·크리덴셜·MCP·스킬·마켓·SSE resume·브랜치·리댁션을 전부 재배선)이라 범위 밖. 대신 **Generative UI 개념만** Moldy 프로토콜에 구현한다. → 본 문서.

---

## 2. 핵심 설계 결정

### 2.1 전송: custom SSE 이벤트 (FILE_EVENT 패턴 복제)

백엔드가 `moldy.ui_data` **custom SSE 이벤트**를 emit한다. 메시지 본문(LangChain content)을 건드리지 않는 **side-channel** 이라 LLM/deepagents 메시지 변환과 충돌하지 않는다. 이는 아티팩트(FILE_EVENT)가 이미 쓰는 검증된 경로다.

페이로드(예):
```json
{
  "schema_version": 1,
  "type": "data_table",
  "message_id": "019f0d8e-...",
  "run_id": "...",
  "tool_call_id": "call_abc",
  "props": { "columns": [...], "rows": [...] }
}
```

### 2.2 렌더: allowlist 레지스트리 + Zod 검증 + fail-safe

프론트는 `type → React 컴포넌트` **레지스트리(allowlist)** 로 컴포넌트를 고른다. **Zod로 props를 검증**하고, **모르는 type/검증 실패는 안전하게 fallback**(렌더 생략 또는 "미리보기 미지원" 칩). 이것이 사용자가 참조한 tool-ui.com의 "payload type → 컴포넌트, 일치하면 렌더 아니면 안전 실패" 모델과 동일하다.

### 2.3 assistant-ui 데이터-파트 API는 0.14.18에 이미 있다 (업그레이드 불필요)

`frontend/node_modules/@assistant-ui/react/dist/index.d.ts` 에 다음이 **이미 export** 됨:
`makeAssistantDataUI`, `useAssistantDataUI`, `useMessagePartData`, `DataMessagePart`, `DataMessagePartComponent`, `AssistantDataUI`, `DataRenderers`.

또한 v3 렌더 스위치 `frontend/src/components/chat/assistant-thread.tsx:299-328` 에 **`case 'data'` 분기가 이미 존재**하며 `leaf.dataRendererUI` 를 렌더한다. `AssistantThread` 는 `dataUI?: readonly AssistantDataUI[]` prop(파일 내 ~742, 1013-1015)을 받는다.

> **결정**: 렌더는 `makeAssistantDataUI` 레지스트리로 한다. 단 "custom 이벤트 → data **파트**" 브리지가 깔끔한지는 §4 스파이크로 먼저 검증하고, 안 깔끔하면 **인라인 카드 렌더**(아티팩트 `AssistantArtifactCards` 패턴, 동일 레지스트리 재사용)로 폴백한다. 두 경로 모두 동일한 레지스트리/Zod를 공유하므로 폴백 비용이 작다.

---

## 3. FILE_EVENT(아티팩트) 선례 — 그대로 복제할 파이프라인 (근거)

### 3.1 백엔드 emit (streaming.py)

- `emit(event, data)` 클로저: `backend/app/agent_runtime/streaming.py:309-341` — `seq` 증가, `format_sse`, broker/persist 전파.
- 이벤트 이름 상수: `backend/app/agent_runtime/event_names.py` (MESSAGE_START/CONTENT_DELTA/MESSAGE_END/TOOL_CALL_START/TOOL_CALL_RESULT/**FILE_EVENT**/`moldy.compaction`/MEMORY_*/STALE).
- 도구 결과 직후 아티팩트 emit: `streaming.py:520-547`
  ```python
  if msg.type == "tool":
      ...
      yield emit(event_names.TOOL_CALL_RESULT, result_payload)
      if artifact_recorder is not None:
          artifact_events = await artifact_recorder.collect_after_tool_result(...)
          for payload in artifact_events:
              yield emit(event_names.FILE_EVENT, payload)   # ← 선례 emit
  ```
- 도구 결과 → 부수효과 이벤트 패턴: `backend/app/agent_runtime/memory_event_projection.py::memory_event_from_tool_result` (도구 결과 문자열을 JSON 파싱해 이벤트로 투영).
- custom 이벤트 네이밍: `moldy.*` (예: `moldy.compaction`, `moldy.memory_proposed` — ag_ui_adapter.py 참조). 프로토콜 변환은 `method="custom"` + namespace로 **자동 통과**(`conversation_agent_protocol_runtime.py:118-149`, `protocol_events.py::_matches_channels`). **새 custom 이벤트는 별도 등록 없이 흐른다.**

### 3.2 프론트 소비 (artifact-events.ts)

- custom 채널 구독: `frontend/src/lib/chat/langgraph-runtime/artifact-events.ts:199-203`
  ```ts
  useChannelEffect(stream, ARTIFACT_CHANNELS /* ['custom'] */, { replay: true, bufferSize: 300, onEvent: handleEvent })
  ```
- 페이로드 추출 + 타입가드: 같은 파일 `protocolArtifactPayload()` (custom name `moldy.` prefix 제거 후 매칭, shape 검증).
- Jotai store: `frontend/src/lib/stores/chat-artifacts.ts:48-65` `upsertChatArtifactAtom`.
- 메시지에 부착: `attachArtifactsToMessages` (assistant_msg_id 정확 매칭 + 마지막 assistant 메시지 fallback).
- 인라인 렌더: `frontend/src/components/chat/assistant-thread.tsx:341-413` `AssistantArtifactCards`.

전체 흐름:
```
SSE custom:moldy.file_event
  → protocolArtifactPayload() (검증)
  → upsertChatArtifactAtom (Jotai)
  → attachArtifactsToMessages (메시지에 매칭)
  → AssistantArtifactCards (인라인 렌더)
```
**우리는 이 흐름을 `ui_data` 용으로 1:1 복제한다.**

---

## 4. Phase 0 — 검증 스파이크 (구현 전 0.5~1일, 추측 제거)

> 목적: "custom 이벤트를 assistant-ui **data 파트**로 만들어 `makeAssistantDataUI` 로 렌더"가 깔끔한지 확정. 안 되면 인라인 카드 폴백 확정.

- [ ] **S1**: `@assistant-ui/react-langchain` 의 `convertLangChainBaseMessage` 가 어떤 content 블록 shape를 `{type:'data', data}` 파트로 변환하는지 `node_modules` 소스에서 정확히 확인. (data 파트 생성 블록 shape 확보)
- [ ] **S2**: 최소 PoC — 하드코딩한 data 파트 1개를 메시지에 주입 → `dataUI={[demoDataUI]}` 로 `makeAssistantDataUI` 렌더가 화면에 뜨는지 확인. (스크립트 모델 불필요, 정적 메시지로)
- [ ] **S3**: 결정 기록 — **경로 A(data 파트 + makeAssistantDataUI)** vs **경로 B(인라인 카드 + 레지스트리)**. 둘 중 하나를 §5 본 구현의 렌더 경로로 확정하고 이 문서 §2.2에 반영.
- **done-when**: 렌더 경로가 코드로 1개 확정됨(데모 타입이 화면에 뜸). 회귀 없음(기존 채팅 정상).

> 이후 §5/§6은 **확정된 렌더 경로**를 전제로 기술한다. 아래 스켈레톤은 경로 A(권장)를 기준으로 하되, 경로 B 폴백 지점을 명시한다.

---

## 5. Phase 1 — Generative UI 인프라 (전송 계약 + 데모 타입)

> 이 단계의 산출물은 "DataTable/Chart 같은 실제 컴포넌트"가 아니라 **end-to-end 배관 1줄과 데모 타입**이다. 데모 타입(`demo_note`: 텍스트 props를 박스로 렌더)으로 파이프라인만 증명한다. 실제 컴포넌트는 Phase 2.

### 5.1 백엔드

**커밋 B1 — `feat(chat): ui_data event contract (schema + emit scaffold)`**

- `backend/app/schemas/ui_data.py` (신규)
  ```python
  from __future__ import annotations
  from typing import Any, Literal
  from pydantic import BaseModel

  UIDataType = Literal["demo_note"]  # Phase 2에서 확장: "data_table"|"chart"|"stats"|"terminal"

  class UIDataEvent(BaseModel):
      schema_version: Literal[1] = 1
      type: UIDataType
      message_id: str | None = None     # 부착 대상(아티팩트와 동일 규칙: 없으면 마지막 assistant)
      run_id: str | None = None
      tool_call_id: str | None = None
      props: dict[str, Any]             # 타입별 검증은 프론트 Zod + (선택) 서버측 per-type 모델
  ```
- `backend/app/agent_runtime/event_names.py`
  ```python
  UI_DATA_EVENT: Final = "moldy.ui_data"
  ```
- `backend/app/agent_runtime/ui_data_projection.py` (신규, `memory_event_projection.py` 패턴)
  ```python
  def ui_data_from_tool_result(tool_name: str, result: str, *, tool_call_id: str | None) -> list[dict]:
      """도구 결과(JSON 문자열)에서 ui_data 페이로드를 투영. 미해당이면 []."""
      if tool_name not in UI_DATA_TOOL_NAMES:   # 데모 단계엔 빈 set → 항상 [] (배관만 검증)
          return []
      try:
          parsed = json.loads(result)
      except (json.JSONDecodeError, TypeError):
          return []
      if not isinstance(parsed, dict) or "ui_type" not in parsed:
          return []
      return [UIDataEvent(
          type=parsed["ui_type"], tool_call_id=tool_call_id,
          props={k: v for k, v in parsed.items() if k != "ui_type"},
      ).model_dump(mode="json")]
  ```
- `backend/app/agent_runtime/streaming.py:~548` (FILE_EVENT 블록 직후, memory_event 블록과 나란히)
  ```python
  for payload in ui_data_from_tool_result(tool_name, result, tool_call_id=normalized_tool_call_id):
      yield emit(event_names.UI_DATA_EVENT, payload)
  ```
- **데모 주입 경로(스파이크/E2E용)**: 스크립트 모델 또는 테스트 헬퍼에서 `demo_note` 1건을 emit하도록 작은 훅. (운영 코드 경로는 `UI_DATA_TOOL_NAMES` 가 비어 있어 무동작 = 회귀 0)
- 테스트: `backend/tests/test_ui_data_projection.py` (투영 단위), `test_streaming.py` 에 ui_data emit 케이스(도구 결과 → UI_DATA_EVENT) 추가. `test_conversation_agent_protocol_*` 에서 custom 채널 통과 확인.

### 5.2 프론트

**커밋 F1 — `feat(chat): ui_data event ingestion + registry scaffold`**

- 계약 타입: `frontend/src/lib/types/ui-data.ts` (신규) — 백엔드 `UIDataEvent` 와 1:1.
- Zod 스키마 + 레지스트리: `frontend/src/lib/chat/data-ui-registry.ts` (신규)
  ```ts
  import { z } from 'zod'
  // 타입별 props Zod (데모)
  const demoNoteProps = z.object({ text: z.string() })
  export const DATA_UI_REGISTRY = {
    demo_note: { props: demoNoteProps, Component: DemoNoteCard },
    // Phase 2: data_table, chart, stats, terminal
  } as const
  export function resolveDataUI(type: string, rawProps: unknown) {
    const entry = (DATA_UI_REGISTRY as Record<string, { props: z.ZodTypeAny; Component: React.FC<any> }>)[type]
    if (!entry) return null                         // 미지원 type → fail-safe
    const parsed = entry.props.safeParse(rawProps)
    if (!parsed.success) return null                // 검증 실패 → fail-safe
    return { Component: entry.Component, props: parsed.data }
  }
  ```
- 이벤트 소비: `frontend/src/lib/chat/langgraph-runtime/data-ui-events.ts` (신규, `artifact-events.ts` 복제)
  - `useChannelEffect(stream, ['custom'], { onEvent })` → custom name `moldy.ui_data` 매칭 → 페이로드 추출.
  - Jotai store `frontend/src/lib/stores/chat-data-ui.ts` (신규, `chat-artifacts.ts` 복제) — message_id 키로 upsert.
- 렌더 (경로 A — data 파트 + makeAssistantDataUI):
  - `frontend/src/lib/chat/data-ui.tsx` — `makeAssistantDataUI({ render: ({ data }) => <DataUIDispatcher data={data} /> })`. `DataUIDispatcher` 가 `resolveDataUI` 로 컴포넌트 선택 + fail-safe.
  - `AssistantThread` 의 `dataUI` prop으로 주입 (page → chat-runtime-section → AssistantThread 경로 배선).
  - store→메시지 부착은 `attachArtifactsToMessages` 와 동일하게 `attachDataUIToMessages` 로 구현(스파이크에서 확정한 data 파트 주입 방식).
- 렌더 (경로 B — 폴백, 인라인 카드): `AssistantDataUICards`(= `AssistantArtifactCards` 복제)가 store에서 현재 메시지 페이로드를 읽어 `resolveDataUI` 로 렌더.
- 데모 컴포넌트: `frontend/src/components/chat/data-ui/demo-note-card.tsx` (텍스트 박스).
- 테스트(vitest): `data-ui-registry`(미지원 type/검증 실패 → null), `data-ui-events`(이벤트→store), 데모 렌더(파이프라인). **공유 mock transport 규칙 준수**(CLAUDE.md): 새 메서드 추가 시 `createMockTransport()` 갱신.

### 5.3 Phase 1 완료 기준 (DoD)
- [ ] `tsc 0 / lint 0 / vitest green / 백엔드 pytest green / ruff clean`
- [ ] 데모 타입(`demo_note`)이 v3 채팅 버블에 박스로 렌더됨(라이브 + reload).
- [ ] **운영 경로 무동작 증명**: `UI_DATA_TOOL_NAMES` 비어 있어 실제 대화에 ui_data 0건 → 기존 채팅과 100% 동일.
- [ ] §7 회귀 캡쳐 게이트 통과.

---

## 6. Phase 2 — 컴포넌트 추가 (하나씩, 각 단계 캡쳐)

각 컴포넌트 = **(a) 백엔드 per-type 페이로드 스키마 + (b) 프론트 Zod + 컴포넌트 래퍼 + 레지스트리 등록 + (c) 테스트 + (d) 캡쳐**. 순서대로, 각 PR/커밋 분리.

### 6.1 DataTable (가장 재사용 쉬움 — 먼저)
- 재사용: `frontend/src/components/ui/data-table.tsx` (tanstack, `DataTableProps<T>`: `columns: ColumnDef<T>[]`, `data: T[]`, `searchable`, `filters`, `pageSize`).
- props 계약: `{ columns: {key, header}[], rows: Record<string,unknown>[], title?, searchable? }`. 래퍼가 `{key,header}` → `ColumnDef` 변환.
- 컴포넌트: `frontend/src/components/chat/data-ui/data-table-card.tsx` (UsageChartFrame 류 카드 셸 + DataTable).
- 작업량: 컴포넌트 자체는 거의 "붙이기", **어댑터(행/열 → ColumnDef)** 가 핵심.

### 6.2 Chart
- 재사용: `frontend/src/components/usage/usage-chart-frame.tsx`(셸) + `spend-line-chart.tsx`/`spend-bar-chart.tsx`(인라인 SVG 라인/바 참조). 더 리치하면 `chart.js`(이미 의존성) 래핑.
- props 계약: `{ chartType: "line"|"bar", series: {label:string, value:number}[], title, xLabel?, yLabel? }`.
- 컴포넌트: `data-ui/chart-card.tsx` — 범용 series → 차트. (spend 차트는 spend 데이터 전용이라 **범용 래퍼 신규**.)

### 6.3 StatsDisplay
- 재사용 컴포넌트 없음 → shadcn `Card` 프리미티브로 신규(가벼움). usage 페이지 요약 숫자 패턴 참조.
- props 계약: `{ items: {label:string, value:string|number, delta?:number, unit?:string}[] }`.
- 컴포넌트: `data-ui/stats-card.tsx` — KPI 그리드.

### 6.4 Terminal
- 재사용 근접: `frontend/src/components/chat/tool-ui/code-tool-ui.tsx` 의 `CodeBlock`(mono `<pre>`). 적응/신규(가벼움).
- props 계약: `{ lines: string[] | string, exitCode?: number, command?: string }`.
- 컴포넌트: `data-ui/terminal-card.tsx` — mono 출력 + (선택) 명령/exit code 헤더.

### 6.5 각 컴포넌트 공통 작업
- 백엔드: `UIDataType` Literal 확장 + (선택) per-type Pydantic 모델로 서버측 검증 + `UI_DATA_TOOL_NAMES` 에 해당 도구 등록(또는 데모 emit).
- 프론트: `DATA_UI_REGISTRY` 에 `{props: zod, Component}` 1줄 추가.
- 테스트 + §7 캡쳐.

---

## 7. 회귀 검증 + 캡쳐 게이트 (사용자 요구의 핵심)

> **Phase 1 인프라가 회귀 0임을 실서버 + 화면별 캡쳐로 증명한 뒤에야 Phase 2로 넘어간다.** Phase 2의 각 컴포넌트도 동일하게 캡쳐한다.

### 7.1 실서버 기동 (throwaway 스택; CLAUDE.md "E2E 포트/DB 격리")
```bash
# throwaway Postgres
docker run -d --name moldy-genui-pg -p 5433:5432 \
  -e POSTGRES_DB=moldy -e POSTGRES_USER=moldy -e POSTGRES_PASSWORD=moldy postgres:16-alpine
cd backend && DATABASE_URL='postgresql+asyncpg://moldy:moldy@localhost:5433/moldy' \
  uv run alembic upgrade head
# 백엔드/프론트는 playwright webServer가 자체 기동(포트 3100/8101)
```

### 7.2 회귀 체크리스트 (화면별로 캡쳐 — 각 항목 1장 이상)
모든 기존 surface가 ui_data 도입 전후로 동일해야 한다.

- [ ] **C1 일반 대화**: 텍스트 스트리밍, 마크다운(코드/테이블), 정상 종료.
- [ ] **C2 도구 호출 그룹**: 연속 동일 도구 그룹핑(GroupedParts) + CollapsiblePill 정상.
- [ ] **C3 검색 도구**: search-tool-ui 출처 집계 정상.
- [ ] **C4 HITL**: 승인 카드(ApprovalCard)/ask_user(OptionList) 정상.
- [ ] **C5 아티팩트**: 생성 파일 인라인 카드 + 우측 레일 + 프리뷰(FILE_EVENT 경로가 ui_data와 같은 custom 채널 → 충돌 없음 확인).
- [ ] **C6 첨부**: user 버블 인라인 + /files 리스트(직전 머지 기능).
- [ ] **C7 reasoning / phase-timeline / sub-agent / memory** 도구 UI 정상.
- [ ] **C8 컨텍스트 게이지 / 토큰 팝오버 / 자동 compaction 마커** 정상.
- [ ] **C9 reload 후 전체 히스토리 재구성** 정상(데이터-파트 포함).
- [ ] **C10 Generative UI 데모(`demo_note`)** 가 라이브 + reload에서 박스로 표시.

### 7.3 자동화(E2E) — `frontend/e2e/chat-generative-ui.spec.ts` (신규)
- 스크립트 모델/테스트 헬퍼로 `demo_note` ui_data 1건 emit → 버블에 렌더 단언 + reload 유지 단언.
- 미지원 type / 검증 실패 → **렌더 생략(에러 없음)** 단언(fail-safe).
- 기존 `chat-attachments-display.spec.ts`·도구 그룹핑 E2E **회귀 없음** 재실행.

### 7.4 캡쳐 산출물
- 스크래치패드에 `captures/` 로 화면별 PNG 저장 후 사용자에게 전달(SendUserFile). headless에서 안 잡히는 콘텐츠(iframe-PDF 등)는 `--headed` 사용(chat-attachments 작업 교훈).
- **No-regression 기준**: C1~C9 캡쳐가 도입 전과 시각적으로 동일, C10 데모 정상, E2E green.

---

## 8. 검증 커맨드 (각 단계)

```bash
# 백엔드
cd backend && uv run pytest && uv run ruff check .
# 프론트
cd frontend && pnpm vitest run && pnpm exec tsc --noEmit && pnpm lint
# E2E (throwaway 스택; §7.1)
cd frontend && E2E_FRONTEND_PORT=3100 E2E_BACKEND_PORT=8101 \
  DATABASE_URL='postgresql+asyncpg://moldy:moldy@localhost:5433/moldy' \
  DATABASE_URL_SYNC='postgresql://moldy:moldy@localhost:5433/moldy' \
  RATE_LIMIT_ENABLED=false E2E_TEST_HELPERS_ENABLED=true \
  pnpm exec playwright test e2e/chat-generative-ui.spec.ts
```
- pre-push 막히면 `SKILL_EVALUATION_ENABLED=true` 로 push(공유 .env false 회피).

---

## 9. 커밋/PR 계획 (각 커밋 그 자체로 그린)

- Phase 0: `chore(chat): generative-ui spike — confirm data-part render path` (스파이크 결과 문서/PoC, 필요 시)
- Phase 1: `feat(chat): ui_data event contract (backend emit + schema)` / `feat(chat): ui_data ingestion + registry + demo render (frontend)` / `test(e2e): generative-ui demo render + no-regression`
- Phase 2(컴포넌트별): `feat(chat): DataTable generative-ui card` → `... Chart ...` → `... StatsDisplay ...` → `... Terminal ...`
- 각 Phase 종료 시 §7 캡쳐 게이트 통과 후 다음 단계.

---

## 10. 리스크 & 미결

- **R1 (스파이크 의존)**: custom 이벤트 → assistant-ui **data 파트** 브리지가 깔끔하지 않으면 인라인 카드(경로 B)로 폴백. §4에서 먼저 확정 → 본 구현 리스크 제거.
- **R2 (보안)**: props는 백엔드/도구가 만든 데이터다. **프론트 Zod로 shape 검증** + **컴포넌트는 신뢰 props만 사용**(임의 HTML/스크립트 렌더 금지; Terminal/CodeBlock은 텍스트로만). allowlist는 type만 통제하므로 **per-type props 검증 필수**.
- **R3 (custom 채널 공존)**: ui_data와 FILE_EVENT가 같은 `custom` 채널을 공유. 소비 측에서 custom **name**(`moldy.ui_data` vs `moldy.file_event`)으로 정확히 분기(아티팩트 `protocolArtifactPayload` 와 동일 규칙). C5에서 회귀 확인.
- **R4 (message_id 매칭)**: 데이터-파트의 부착 대상 message_id는 아티팩트와 동일 규칙(정확 매칭 + 마지막 assistant fallback). 라이브 전송 직후 미매칭 시 reload로 복구(아티팩트와 동일 특성).
- **R5 (범위 절제)**: 기존 tool-ui(`makeAssistantToolUI`) 렌더를 건드리지 않는다(추가형). payload-type 전면 전환/toolkit 마이그레이션/0.14.24 업그레이드는 별도 트랙.

---

## 11. 참조 파일 인덱스 (구현 시 열어볼 곳)

| 목적 | 파일 |
|------|------|
| SSE emit/event 이름 | `backend/app/agent_runtime/streaming.py:309-341,520-548`, `event_names.py` |
| 도구결과→이벤트 투영 패턴 | `backend/app/agent_runtime/memory_event_projection.py` |
| 아티팩트 페이로드/스키마 | `backend/app/services/artifact_service.py:122-149,1028`, `backend/app/schemas/artifact.py:26-61` |
| 프로토콜 custom 통과 | `backend/app/routers/conversation_agent_protocol_runtime.py:118-149`, `agent_runtime/protocol_events.py` |
| 프론트 custom 소비 선례 | `frontend/src/lib/chat/langgraph-runtime/artifact-events.ts:78-203` |
| 프론트 store 선례 | `frontend/src/lib/stores/chat-artifacts.ts:48-65` |
| 파트 렌더 스위치('data') | `frontend/src/components/chat/assistant-thread.tsx:299-328,341-413` |
| 파트 그룹핑 | `frontend/src/lib/chat/group-assistant-parts.ts` |
| data-part API(0.14.18) | `frontend/node_modules/@assistant-ui/react/dist/index.d.ts` (`makeAssistantDataUI` 등) |
| 재사용 DataTable | `frontend/src/components/ui/data-table.tsx` |
| 재사용 Chart 셸/차트 | `frontend/src/components/usage/{usage-chart-frame,spend-line-chart,spend-bar-chart}.tsx` |
| 재사용 Terminal 근접 | `frontend/src/components/chat/tool-ui/code-tool-ui.tsx` (CodeBlock) |
| 런타임 구성 | `frontend/src/lib/chat/langgraph-runtime/use-moldy-langgraph-stream.ts:2798` (`useExternalStoreRuntime`) |
