# 개발 기획: 범용 Tool-Call 그룹핑 (모든 도구를 그룹 박스로)

> 상태: **기획(미구현)** · 작성: 2026-06-25 · 대상 세션: 신규 구현 세션
> 결정: **Option 3 (커스텀 유지 + 일반화)**. assistant-ui 공식 `GroupedParts`/`ToolGroup` API는 **아직 npm 미출시**라(부록 A 검증) 지금은 채택 불가. 정식 배포 시 마이그레이션 재검토.

---

## 0. 한 줄 요약

지금 그룹핑(여러 박스 → 1개로 뭉치기)은 **검색/딥리서치(tavily) 전용**이고, **레거시 런타임에만** 걸려 있다. 이걸 **모든 도구가 동일하게 동작**하도록(연속 같은 도구 = 1 컨테이너 + 개수 라벨 + running 펼침/done 접힘) **메인 v3 채팅**에 **커스텀으로 일반화**한다.

---

## 1. 목표 (Done 기준)

1. **메인 v3 채팅**(`assistant-thread.tsx`)에서 한 assistant 메시지 안의 **연속된 같은 도구 호출 N개**가 **1개의 접을 수 있는 그룹 컨테이너**로 렌더된다.
2. 그룹 헤더에 **개수 라벨**("웹 검색 · 10회" 등 도구별 라벨 + count).
3. **running 중 자동 펼침 / 완료 시 접힘.**
4. 그룹 내부는 호출별 **한 줄 요약**(검색=쿼리, 파일=경로 등).
5. (선택) 검색류 도구는 **출처를 pill로 별도 집계**(Perplexity식).
6. 단일 호출(N≤1)은 **그룹핑하지 않고** 기존 개별 박스로 렌더.
7. 모든 도구에 일반 적용(tavily 특수처리 제거 또는 일반 그룹의 특수 케이스로 흡수).

---

## 2. 현재 상태 (이번 세션 검증 완료)

### 2.1 런타임 2개
- **v3 (메인 채팅)**: `frontend/src/lib/chat/langgraph-runtime/use-moldy-langgraph-stream.ts` + LangGraph SDK(`useStream`). 렌더 진입점은 `frontend/src/components/chat/assistant-thread.tsx`.
- **레거시**: `frontend/src/lib/chat/use-chat-runtime.ts`. 아직 살아있는 표면 = 대화형 빌더(`app/agents/new/conversational`), Assistant 패널(`components/agent/assistant-panel.tsx`), 설정 테스트챗(`app/agents/[agentId]/settings/_components/right-panel/test-chat-panel.tsx`).

### 2.2 도구 박스 = 자체 구현
- `frontend/src/components/chat/tool-ui/`의 ~28개 커스텀 컴포넌트가 assistant-ui **`makeAssistantToolUI` 프리미티브**(도구명 → 렌더 함수 매핑)로 등록. 비주얼은 100% 자체 구현. **assistant-ui tool-group 미사용.**

### 2.3 현재 그룹핑 = tavily 전용 + 레거시 전용
- 로직: `frontend/src/lib/chat/deep-research-summary.ts` (253줄)
  - `compactDeepResearchMessages(messages)` → turn 단위로 `tavily_search` 호출을 모아, **2개 이상**(`tavilyCalls.length <= 1`이면 패스)일 때 개별 호출 N개 + 결과 메시지를 제거하고 **합성 `deep_research_summary` tool_call 1개**로 치환.
  - 상수: `TAVILY_SEARCH_TOOL_NAME = 'tavily_search'`, `DEEP_RESEARCH_SUMMARY_TOOL_NAME = 'deep_research_summary'`.
- UI: `frontend/src/components/chat/tool-ui/deep-research-summary-ui.tsx` (172줄) — `makeAssistantToolUI({ toolName: 'deep_research_summary' })`. 출처 dedup·도메인 랭킹·완료 N/M·소요시간 등 **풍부한 요약** 렌더.
- 와이어링: **`frontend/src/lib/chat/use-chat-runtime.ts:467`** `const merged = compactDeepResearchMessages(...)` — **레거시 런타임에서만 호출**.
- 테스트: `frontend/src/lib/chat/deep-research-summary.test.ts`.

> ⚠️ 핵심: **메인 v3 채팅(`use-moldy-langgraph-stream.ts`)은 `compactDeepResearchMessages`를 호출하지 않는다.** 즉 메인 채팅엔 그룹핑이 **아예 없어서** "N박스" 문제가 실재한다. 일반화의 1차 타깃이 바로 여기다.

### 2.4 재사용 가능한 자산
- `frontend/src/components/chat/tool-ui/collapsible-pill.tsx` — 접기/펼치기 UX (그룹 컨테이너에 재사용).
- `frontend/src/lib/chat/search-results.ts` (90줄) — `parseSearchResults`, `sourceSummariesFromResults`(출처 dedup/도메인 집계). 출처 pill에 재사용.
- 도구는 **registry 도구**: 예) `tavily_search` = `backend/app/tools/definitions/tavily_search.py` (key `tavily`, `TAVILY_API_KEY` 사용). MCP 아님.

---

## 3. 경험적 근거 (실측 데이터)

### 3.1 도구 호출은 intra-message (한 메시지에 N개)
- 출처: dev DB(`natural-mold-postgres-1`, localhost:5432) `message_events` 테이블의 실제 deep-research 대화 2건.
  - 대화 `cba4e3c9…` → assistant 메시지 1개(`c40c9bcd…`)에 **`tavily_search` ×10 + `read_file` ×1**.
  - 대화 `992272b8…` → assistant 메시지 1개(`bf62d812…`)에 **`tavily_search` ×4 + `naver_search_news` ×1**.
  - tool_call id가 모두 `{assistant_msg_id}-{n}` 형태 → **한 메시지 소속 확정**.
- **결론: 검색 N개는 별개 메시지가 아니라 한 메시지의 N개 part다 → intra-message 그룹핑으로 충분.** (cross-message 패스 불필요. 기존 커스텀이 cross-message도 처리하는 건 방어적.)

### 3.2 한 메시지에 도구가 섞임
- tavily 10 + read_file 1 / tavily 4 + naver 1 처럼 **다른 도구가 섞임.**
- → "모든 도구를 한 그룹"이 아니라 **연속된 같은 도구 이름** 기준으로 그룹(웹 검색은 웹 검색끼리, read_file은 별도). groupingFunction은 **tool name 기준**.

### 3.3 저장 포맷 (참고)
- `message_events.events`는 Moldy 자체 SSE 프로토콜(`message_start`/`tool_call_start`/`tool_call_result`/`content_delta`/`message_end`, `backend/app/agent_runtime/event_names.py` + `legacy_event_projection.py`). **AG-UI 아님**(AG-UI는 외부 Agent API 전용 projection). resume/replay/share/trace용으로 영속화(ADR-011). 그룹핑 작업과는 무관(프론트 `Message[]` 레벨에서 처리).

---

## 4. 설계 — 3-rule 패턴

1. **연속 같은 도구 = 1 컨테이너 + 개수 라벨**: `{도구 라벨} · {N}회` (예: "웹 검색 · 10회", "파일 읽기 · 3회").
2. **running 펼침 / done 접힘**: 그룹 내 tool-call part의 status가 하나라도 running이면 펼침, 전부 완료면 접힘.
3. **호출별 한 줄 요약** + (검색류) **출처 pill 별도 집계**.
- **임계값**: N ≥ 2일 때만 그룹. N=1은 기존 개별 박스.

> 이 패턴은 assistant-ui 공식 `ToolGroup`(미출시)과 동일한 모양이다. 커스텀 레이어를 **공식 `groupBy` + group-render 형태와 닮게** 설계하면, 나중에 공식 API 출시 시 교체가 기계적이 된다(부록 A 참고).

---

## 5. 구현 계획

### 5.1 진입점
- `frontend/src/components/chat/assistant-thread.tsx`
  - 현재 `AssistantMessageParts()` (≈236–239행)에서 `<MessagePrimitive.Content components={ASSISTANT_PART_COMPONENTS} />`로 parts를 렌더.
  - 또 다른 `<MessagePrimitive.Content />` 사용처(≈757행) + `builder-overrides.tsx`(레거시/빌더용)도 점검.

### 5.2 접근 방식 — **render-time 그룹핑(권장)**
- **Option B (render-time, 권장)**: parts를 렌더 단계에서 순회하며 **연속 같은 tool-call part**를 `<ToolGroup>`으로 묶는 커스텀 컴포넌트. 메시지 재작성 없음, 일반적, 공식 API와 형태 유사 → 미래 교체 용이.
- Option A (message-transform): `compactDeepResearchMessages`처럼 `Message[]`를 사전 변환해 그룹 마커 삽입. 일반화 가능하지만 메시지 재작성이라 공식 마이그레이션과 덜 맞음. **비권장.**
- ⚠️ 주의: 0.14.18에 **deprecated `Unstable_PartsGrouped`**(groupingFunction + `Group` 컴포넌트 config)가 **존재**한다. 디딤돌로 쓸 수 있지만 deprecated(곧 제거)라 **권장 안 함** — 차라리 순수 커스텀 iterator로 part를 그룹해 공식 출시 시 갈아끼우는 편이 안전. (판단은 구현 세션에서.)

### 5.3 만들 컴포넌트
- `ToolGroup` 컨테이너 (신규, `components/chat/tool-ui/tool-group.tsx`):
  - 헤더: 도구 아이콘 + `{label} · {count}회` + chevron. **`collapsible-pill.tsx` 재사용.**
  - body: 호출별 한 줄 요약(아래 5.4).
  - 상태: 그룹 내 part status 집계로 running/done → 펼침/접힘.
- 도구 메타 맵 (신규, `lib/chat/tool-group-meta.ts`):
  - `toolName → { label, icon, summaryLine(args) }`. 예: `tavily_search → {label:'웹 검색', summaryLine: a => a.query}`, `read_file → {label:'파일 읽기', summaryLine: a => a.file_path}`. **제네릭 fallback**(라벨=toolName, summaryLine=주요 arg 1개).

### 5.4 호출별 한 줄 요약
- 도구별 핵심 arg를 한 줄로(검색=`query`, read_file=`file_path` 등). 맵에 없으면 fallback.

### 5.5 출처 pill (선택, 검색류)
- `lib/chat/search-results.ts`의 `parseSearchResults` + `sourceSummariesFromResults`로 그룹 내 모든 검색 결과의 **고유 출처/도메인 집계** → 그룹 footer 또는 메시지 끝에 pill row.

---

## 6. 기존 deep-research 그룹핑 정리

- `deep-research-summary.ts` + `deep-research-summary-ui.tsx`의 운명 결정:
  - **(a) 일반 ToolGroup으로 흡수 + 풍부한 요약 보존(권장 if 가치 있음)**: tavily 그룹의 body/footer에 기존 집계(출처 dedup/도메인/완료 N·M/소요시간)를 특수 렌더로 유지.
  - (b) 풍부한 요약 폐기, 심플 일반 그룹으로 통일(코드↓).
- 레거시 `use-chat-runtime.ts:467` 호출은: 일반 그룹핑이 레거시 표면까지 커버할 때까지 유지하거나, 두 런타임 모두 마이그레이션.

---

## 7. 테스트

- **vitest**:
  - `ToolGroup` 컴포넌트 + groupingFunction 단위 테스트: intra-message, **tool name 기준**, 도구 혼합(tavily+read_file), 임계값(N≥2), running/done 상태.
  - `deep-research-summary.test.ts` 마이그레이션/업데이트.
- **chat E2E**(기존 `frontend/e2e/chat-stream-integrity.spec.ts` 확장 또는 신규):
  - scripted 모델에 **한 메시지 M개 동일 tool_call** 방출 마커 추가(`backend/app/agent_runtime/e2e_scripted_model.py` — 기존 `E2E_HITL_MULTI` 패턴 참고).
  - 단언: N개 호출이 **1개 그룹 + count**로, running→펼침/done→접힘, 박스 중복 없음.
  - 기존 베이스라인/known-flake 인지: cg47(`chat-langgraph-v3.spec.ts:47`, subagent 완료 stall, 별도), visual-matrix:146(부하 flake).
- **가드**: Phase A/B에서 만든 render-integrity 스펙들이 이미 있음 → 확장.

---

## 8. 리스크 / 미해결 질문

1. **렌더 단계 part 접근**: `MessagePrimitive.Content`가 parts를 어떻게 노출하는지 확인 후, deprecated `Unstable_PartsGrouped` 디딤돌 vs 순수 커스텀 iterator 선택. (per-tool `makeAssistantToolUI` 렌더러를 깨지 않아야 함.)
2. **cross-message**: 실측은 intra-message 우세. 순차 단일호출 메시지를 내는 에이전트가 있으면 cross-message 그룹도 필요 — **일단 보류**(현 커스텀은 방어적으로 처리 중).
3. **레거시 런타임**: 일반 그룹핑을 레거시에도 적용할지, 기존 deep-research 그룹핑을 둘지.
4. **공식 마이그레이션 대비**: 커스텀 레이어를 공식 `groupBy(part)→["group-tool"]` + group-render 형태와 닮게 만들어 교체를 기계적으로.

---

## 9. 견적

- 일반 그룹핑 + `ToolGroup` 컴포넌트 + 메타 맵 + 테스트: **~3–4 dev-days.**
- 풍부한 deep-research 요약 보존(6-a): **+1d.**
- 출처 pill(5.5): **+0.5d.**

---

## 부록 A — 왜 지금 공식 assistant-ui tool-group을 못 쓰나 (이번 세션 검증)

- 설치 `@assistant-ui/react` **0.14.18**, npm 최신 **0.14.24**. 동반: `react-langchain` 0.0.13(peer 없음), `react-streamdown` 0.3.3(peer `^0.14.18`).
- 공식 docs(`https://www.assistant-ui.com/docs/ui/tool-group`) 패턴:
  ```tsx
  <MessagePrimitive.GroupedParts groupBy={(part) => part.type === "tool-call" ? ["group-tool"] : null}>
    {({ part, children }) => part.type === "group-tool"
      ? <ToolGroupRoot><ToolGroupTrigger count={part.indices.length} active={part.status.type==="running"} /><ToolGroupContent>{children}</ToolGroupContent></ToolGroupRoot>
      : part.toolUI ?? <ToolFallback {...part} /> }
  </MessagePrimitive.GroupedParts>
  ```
  - `ToolGroupRoot/Trigger/Content`는 **copy-paste shadcn 컴포넌트**(`@/components/assistant-ui/tool-group`, CLI 스캐폴드, count·active 내장).
- **검증 결과(결정적)**:
  - `groupBy`/`toolUI` 심볼 → **0.14.18, 0.14.24 둘 다 dist에 0건.** `.d.ts`엔 `@deprecated Prefer <MessagePrimitive.GroupedParts>` 주석 + 구식 `Unstable_PartsGrouped`만 존재.
  - npm dist-tags = `{ latest: 0.14.24 }` — **next/canary 없음.**
  - → **공식 `GroupedParts` API는 문서화됐지만 npm 미출시**(마이그레이션 중).
  - **0.14.24 업그레이드 실시 → vitest 5개 파일 로드 실패**(v3 chat runtime: `message-list`/`langchain-message-conversion`/`interrupt-requires-action`/`server-message-fallback`/`chat.test`, import breaking) + peer 불일치(`@assistant-ui/tap` `^0.7.0` vs `0.9.3`). **0.14.18로 revert**(vitest 201파일/997테스트 green 복구).
- **결론**: 정식 배포되면 그때 업그레이드 + `GroupedParts`로 마이그레이션이 적기. 그 전까지 본 문서대로 **커스텀 일반화**로 진행하되, 공식과 닮은 형태로 만들어 교체를 쉽게 한다.

---

## 부록 B — 핵심 파일 인덱스

| 목적 | 경로 |
|------|------|
| v3 thread 렌더 진입점 | `frontend/src/components/chat/assistant-thread.tsx` (≈239, `MessagePrimitive.Content`) |
| 현재 그룹핑 로직(tavily 전용) | `frontend/src/lib/chat/deep-research-summary.ts` |
| 현재 그룹 박스 UI | `frontend/src/components/chat/tool-ui/deep-research-summary-ui.tsx` |
| 그룹핑 와이어링(레거시) | `frontend/src/lib/chat/use-chat-runtime.ts:467` |
| 접기 UX 재사용 | `frontend/src/components/chat/tool-ui/collapsible-pill.tsx` |
| 출처 집계 재사용 | `frontend/src/lib/chat/search-results.ts` |
| 도구별 박스 등록 | `frontend/src/components/chat/tool-ui/*` (`makeAssistantToolUI`) |
| scripted E2E 모델(테스트 마커) | `backend/app/agent_runtime/e2e_scripted_model.py` |
| tavily 도구 정의(registry) | `backend/app/tools/definitions/tavily_search.py` |
| 기존 그룹핑 테스트 | `frontend/src/lib/chat/deep-research-summary.test.ts` |
