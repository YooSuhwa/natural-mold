# 개발 기획: 범용 Tool-Call 그룹핑 (모든 도구를 그룹 박스로)

> 상태: **Phase-1 구현 완료 (2026-06-25)** · 브랜치 `worktree-feature+generic-tool-call-grouping` · 최초 작성/개정 2026-06-25
> 결정: **공식 `MessagePrimitive.GroupedParts` 채택 + 그룹 컨테이너 비주얼은 기존 `CollapsiblePill` 재사용** (§5.3의 공식 `tool-group.tsx` vendoring 대신 — 디자인 토큰 일관성 + 검증된 접기 UX + keyframe/가드 작업 회피). tool name 세분화를 위해 `groupPartByType`(type 기준) 대신 inline `groupBy` 사용.
>
> **실제 구현/검증 (Phase-1, 메인 v3 한정):**
> - `frontend/src/components/chat/assistant-thread.tsx` — `MessagePrimitive.GroupedParts` + 모듈레벨 `groupAssistantParts`(`group-tool:${toolName}`, `isGroupableTool` 제외) + `renderGroupedAssistantPart`(group N≥2 컨테이너 / N=1 패스스루 / text·tool-call·data·indicator·default leaf 보존, default=null은 `defaultComponents`와 일치 확인).
> - `frontend/src/components/chat/tool-ui/tool-group-container.tsx` — `CollapsiblePill` 래퍼(라벨·count·running/done key remount).
> - `frontend/src/lib/chat/tool-group-meta.ts` — `toolGroupLabelKey`(검색/파일류 i18n) + `isGroupableTool`(HiTL/승인 제외).
> - i18n `chat.toolGroup.{count,labels.*}` (ko/en), vitest 2개, E2E `E2E_TOOL_GROUP` 마커(`current_datetime`×3+`resolve_relative_date`×1, no-network/no-HITL builtin).
> - 검증: tsc 0 · vitest 1009 그린 · lint(design-system/i18n) 통과 · E2E cold `--retries=0` 통과(N박스→1그룹+count, done 접힘, 펼침 복원).
> - **Phase-2 남음**: deep-research 특수처리 흡수(§6), 레거시/빌더 표면(test-chat-panel·conversational·assistant-panel) 적용.
>
> ⚠️ 아래 §4~9는 **구현 전 기획**이며, §5.3의 vendoring 안은 위 결정대로 `CollapsiblePill` 재사용으로 대체되었다.
> ⚠️ 개정 사유: 초판은 "공식 `GroupedParts`/`ToolGroup` API가 npm 미출시라 커스텀 일반화(Option 3)로 간다"고 결정했으나, **이 전제가 틀렸다**. 공식 grouping 런타임 API는 **우리가 이미 쓰는 `@assistant-ui/react` 0.14.18에 STABLE로 존재**한다(검증 §10·부록 A). 따라서 커스텀 일반화를 버리고 **공식 API로 직접 구현**한다. registry 컴포넌트(`tool-group.tsx`/`tool-fallback.tsx`)는 shadcn copy-paste로 vendoring 후 우리 디자인에 맞춘다.

---

## 0. 한 줄 요약

지금 그룹핑(여러 박스 → 1개로 뭉치기)은 **검색/딥리서치(tavily) 전용**이고 **레거시 런타임에만** 걸려 있다. 이걸 **모든 도구가 동일하게 동작**(연속 같은 도구 = 1 컨테이너 + 개수 라벨 + running 펼침/done 접힘)하도록 **메인 v3 채팅**에 적용한다. 구현은 **자체 일반화가 아니라 assistant-ui 공식 `MessagePrimitive.GroupedParts` + `groupPartByType`**(이미 0.14.18에 존재)을 쓰고, 그룹 컨테이너 비주얼만 vendored `tool-group.tsx`를 우리 토큰에 맞게 다듬는다.

---

## 1. 목표 (Done 기준)

1. **메인 v3 채팅**(`assistant-thread.tsx`)에서 한 assistant 메시지 안의 **연속된 같은 도구 호출 N개**가 **1개의 접을 수 있는 그룹 컨테이너**로 렌더된다.
2. 그룹 헤더에 **개수 라벨**("웹 검색 · 10회" 등 도구별 라벨 + count).
3. **running 중 자동 펼침 / 완료 시 접힘.**
4. 그룹 내부는 호출별 **한 줄 요약**(검색=쿼리, 파일=경로 등) — 기존 `makeAssistantToolUI` per-tool UI를 그대로 렌더.
5. (선택) 검색류 도구는 **출처를 pill로 별도 집계**(Perplexity식).
6. 단일 호출(N≤1)은 **그룹핑하지 않고** 기존 개별 박스로 렌더.
7. 모든 도구에 일반 적용(tavily 특수처리 제거 또는 일반 그룹의 특수 케이스로 흡수).
8. **(개정 추가)** 그룹핑은 공식 `GroupedParts` API로 구현하여, 향후 assistant-ui 업그레이드 시 커스텀 유지보수 부담이 없도록 한다.

---

## 2. 현재 상태 (실측 검증 완료)

### 2.1 런타임 2개
- **v3 (메인 채팅)**: `frontend/src/lib/chat/langgraph-runtime/use-moldy-langgraph-stream.ts` + LangGraph SDK(`useStream`). 렌더 진입점은 `frontend/src/components/chat/assistant-thread.tsx`.
- **레거시**: `frontend/src/lib/chat/use-chat-runtime.ts`. 아직 살아있는 표면 = 대화형 빌더(`app/agents/new/conversational`), Assistant 패널(`components/agent/assistant-panel.tsx`), 설정 테스트챗(`app/agents/[agentId]/settings/_components/right-panel/test-chat-panel.tsx`).

### 2.2 도구 박스 = 자체 구현
- `frontend/src/components/chat/tool-ui/`의 ~27개 커스텀 컴포넌트가 assistant-ui **`makeAssistantToolUI` 프리미티브**(도구명 → 렌더 함수 매핑)로 등록(`frontend/src/lib/chat/tool-ui-registry.ts`). 비주얼은 100% 자체 구현. **assistant-ui tool-group 미사용.**
- catch-all: `frontend/src/components/chat/tool-ui/generic-tool-ui.tsx`의 `GenericToolFallback`(`makeAssistantToolUI({ toolName: '*' })`)이 미등록 도구를 처리.
- reasoning: `frontend/src/components/chat/tool-ui/reasoning-ui.tsx`의 `ReasoningDataUI`(`makeAssistantDataUI`)로 등록(`data-ui-registry.ts`).

### 2.3 현재 그룹핑 = tavily 전용 + 레거시 전용
- 로직: `frontend/src/lib/chat/deep-research-summary.ts` (253줄)
  - `compactDeepResearchMessages(messages)` → turn 단위로 `tavily_search` 호출을 모아, **2개 이상**(`tavilyCalls.length <= 1`이면 패스)일 때 개별 호출 N개 + 결과 메시지를 제거하고 **합성 `deep_research_summary` tool_call 1개**로 치환.
  - 상수: `TAVILY_SEARCH_TOOL_NAME = 'tavily_search'`, `DEEP_RESEARCH_SUMMARY_TOOL_NAME = 'deep_research_summary'`.
- UI: `frontend/src/components/chat/tool-ui/deep-research-summary-ui.tsx` (172줄) — `makeAssistantToolUI({ toolName: 'deep_research_summary' })`. 출처 dedup·도메인 랭킹·완료 N/M·소요시간 등 **풍부한 요약** 렌더.
- 와이어링: **`frontend/src/lib/chat/use-chat-runtime.ts:467`** `const merged = compactDeepResearchMessages(...)` — **레거시 런타임에서만 호출**.
- 테스트: `frontend/src/lib/chat/deep-research-summary.test.ts`.

> ⚠️ 핵심: **메인 v3 채팅(`use-moldy-langgraph-stream.ts`)은 `compactDeepResearchMessages`를 호출하지 않는다.** 즉 메인 채팅엔 그룹핑이 **아예 없어서** "N박스" 문제가 실재한다. 일반화의 1차 타깃이 바로 여기다.

### 2.4 재사용 가능한 자산
- `frontend/src/components/chat/tool-ui/collapsible-pill.tsx` — 접기/펼치기 UX.
- `frontend/src/lib/chat/search-results.ts` (90줄) — `parseSearchResults`, `sourceSummariesFromResults`(출처 dedup/도메인 집계). 출처 pill에 재사용.
- 도구는 **registry 도구**: 예) `tavily_search` = `backend/app/tools/definitions/tavily_search.py` (key `tavily`, `TAVILY_API_KEY` 사용). MCP 아님.

---

## 3. 경험적 근거 (실측 데이터)

### 3.1 도구 호출은 intra-message (한 메시지에 N개)
- 출처: dev DB(`natural-mold-postgres-1`, localhost:5432) `message_events` 테이블의 실제 deep-research 대화 2건.
  - 대화 `cba4e3c9…` → assistant 메시지 1개(`c40c9bcd…`)에 **`tavily_search` ×10 + `read_file` ×1**.
  - 대화 `992272b8…` → assistant 메시지 1개(`bf62d812…`)에 **`tavily_search` ×4 + `naver_search_news` ×1**.
  - tool_call id가 모두 `{assistant_msg_id}-{n}` 형태 → **한 메시지 소속 확정**.
- **결론: 검색 N개는 별개 메시지가 아니라 한 메시지의 N개 part다 → intra-message 그룹핑으로 충분.** 공식 `GroupedParts`는 기본이 **adjacent(인접) 그룹핑**이라 정확히 이 케이스에 맞는다. (cross-message는 §8 참고, 보류.)

### 3.2 한 메시지에 도구가 섞임
- tavily 10 + read_file 1 / tavily 4 + naver 1 처럼 **다른 도구가 섞임.**
- → "모든 도구를 한 그룹"이 아니라 **연속된 같은 도구 이름** 기준으로 그룹. 공식 `groupPartByType`는 part type 기준 그룹이므로, **tool name 기준 세분화**는 `groupBy` 콜백에서 group key에 도구 이름을 포함시켜 처리(§5.2).

### 3.3 저장 포맷 (참고)
- `message_events.events`는 Moldy 자체 SSE 프로토콜(`message_start`/`tool_call_start`/`tool_call_result`/`content_delta`/`message_end`). **AG-UI 아님.** resume/replay/share/trace용으로 영속화(ADR-011). 그룹핑 작업과는 무관(프론트 `Message[]`/part 레벨에서 처리).

---

## 4. 설계 — 3-rule 패턴

1. **연속 같은 도구 = 1 컨테이너 + 개수 라벨**: `{도구 라벨} · {N}회` (예: "웹 검색 · 10회", "파일 읽기 · 3회").
2. **running 펼침 / done 접힘**: 그룹 내 tool-call part의 status가 하나라도 running이면 펼침, 전부 완료면 접힘. (공식 `ToolGroupTrigger`의 `active` prop이 이 신호를 받음.)
3. **호출별 한 줄 요약** + (검색류) **출처 pill 별도 집계**. 그룹 내부의 각 호출은 **기존 `makeAssistantToolUI` per-tool UI를 그대로** 렌더.
- **임계값**: N ≥ 2일 때만 그룹. N=1은 기존 개별 박스.

> 이 3-rule은 공식 `tool-group.tsx`(ToolGroupRoot/Trigger/Content)의 동작과 동일하다. 즉 **별도 컨테이너를 새로 만들 필요 없이** 공식 컴포넌트를 vendoring해서 라벨/토큰만 바꾸면 된다.

---

## 5. 구현 계획 — 공식 GroupedParts 채택

### 5.0 핵심 API (0.14.18에 존재, §10 검증)
- `MessagePrimitive.GroupedParts` (STABLE) — `@assistant-ui/react`의 `MessagePrimitive` 네임스페이스. `groupBy: (part, context) => TKey[] | null` prop을 받아 인접 part를 합성 `group-*` 노드로 묶는다. render fn은 `{ part, children }`를 받고, **group 케이스만 `children`을 렌더**.
- `groupPartByType(map)` (STABLE) — root에서 import. `groupPartByType({ "tool-call": ["group-tool"] })` 처럼 part type → group key 매핑을 만들어 `groupBy`에 넘기는 헬퍼.
- 합성 key `"standalone-tool-call"`(human tools·MCP apps 자동 standalone, 그룹에서 제외) — 우리는 당장 불필요하나 인지. (`"mcp-app"`은 deprecated, v0.15 제거.)

### 5.1 진입점
- `frontend/src/components/chat/assistant-thread.tsx`
  - 현재 `AssistantMessageParts()` (≈236–239행)에서 `<MessagePrimitive.Content components={ASSISTANT_PART_COMPONENTS} />`로 parts를 렌더. (`Content`는 `Parts`의 alias.)
  - → 이 자리를 `<MessagePrimitive.GroupedParts groupBy={...}>{renderGroupOrPart}</MessagePrimitive.GroupedParts>`로 교체.
  - 또 다른 `<MessagePrimitive.Content />` 사용처(≈757행) + `builder-overrides.tsx`(레거시/빌더용)도 점검 — 빌더 표면은 우선 기존 유지 가능.

### 5.2 그룹핑 로직 (tool name 세분화)
- `groupPartByType`는 type 기준이라 tavily·read_file이 한 그룹으로 섞일 수 있다. 우리는 **tool name 기준**이 필요하므로 `groupBy` 콜백을 직접 작성:
  ```ts
  const groupBy = (part) =>
    part.type === "tool-call" ? [`group-tool:${part.toolName}`] : null;
  ```
  - group key에 `toolName`을 포함 → **연속 같은 도구만** 한 그룹(인접 다른 도구는 자동 분리). N=1이면 컨테이너 없이 개별 박스로 렌더(render fn에서 `part.indices.length < 2` 분기).
- render fn:
  ```tsx
  ({ part, children }) =>
    part.type.startsWith("group-tool:")
      ? <ToolGroupRoot defaultOpen={part.status?.type === "running"}>
          <ToolGroupTrigger count={part.indices.length} active={part.status?.type === "running"} label={metaFor(part).label} />
          <ToolGroupContent>{children}</ToolGroupContent>
        </ToolGroupRoot>
      : children // 비-그룹 part는 기존 렌더 경로 그대로
  ```

### 5.3 vendoring할 컴포넌트 (shadcn copy-paste, 우리 토큰에 맞춤)
- `npx shadcn@latest add https://r.assistant-ui.com/tool-group.json https://r.assistant-ui.com/tool-fallback.json` → `frontend/src/components/assistant-ui/{tool-group,tool-fallback}.tsx` 생성. (tool-group이 tool-fallback에 의존하여 함께 설치됨.)
  - 원본 소스 참고 위치(로컬): `/Users/chester/dev/ref/assistant-ui/packages/ui/src/components/assistant-ui/`.
  - `tool-group.tsx`는 런타임 의존이 `useScrollLock`뿐 → **0.14.18에서 그대로 동작.**
  - `tool-fallback.tsx`의 **approval(HiTL) 서브컴포넌트는 0.14.19+ API(`respondToApproval`)** 라 0.14.18에선 일부 미동작 가능 → 우리는 자체 `ApprovalCard`가 있으므로 **approval 파트는 제거/우리 것으로 대체**.
- vendored 후: 헤더를 `{도구 라벨} · {N}회`로 (label prop 추가), 디자인 토큰을 ADR-010에 맞춤, `collapsible-pill.tsx`와 톤 통일.
- 도구 메타 맵 (신규, `frontend/src/lib/chat/tool-group-meta.ts`):
  - `toolName → { label, summaryLine(args) }`. 예: `tavily_search → {label:'웹 검색', summaryLine: a => a.query}`, `read_file → {label:'파일 읽기', summaryLine: a => a.file_path}`. **제네릭 fallback**(label=toolName, summaryLine=주요 arg 1개).

### 5.4 호출별 한 줄 요약 / 기존 per-tool UI 유지
- 그룹 내부의 각 `tool-call` part는 **기존 `makeAssistantToolUI` 등록 UI로 렌더**한다(검색=쿼리 한 줄, 파일=경로 등은 이미 per-tool UI가 함). 그룹은 컨테이너 역할만.
- ⚠️ 검증 필요(§8-1): `GroupedParts`의 `children`이 그룹 내부 part를 렌더할 때 **기존 등록된 per-tool UI 라우팅이 유지**되는지. 안 되면 group render fn 내부에서 `MessagePrimitive.PartByIndex`/`components.tools` 경로로 명시 위임.

### 5.5 출처 pill (선택, 검색류)
- `lib/chat/search-results.ts`의 `parseSearchResults` + `sourceSummariesFromResults`로 그룹 내 모든 검색 결과의 **고유 출처/도메인 집계** → 그룹 footer에 pill row. (공식 `sources.tsx`는 `source` **part type** 기반이라 우리 아키텍처(결과가 tool result 안)와 안 맞음 → 우리 파서 유지.)

---

## 6. 기존 deep-research 그룹핑 정리

- `deep-research-summary.ts` + `deep-research-summary-ui.tsx`의 운명 결정:
  - **(a) 일반 ToolGroup으로 흡수 + 풍부한 요약 보존(권장 if 가치 있음)**: tavily 그룹의 footer에 기존 집계(출처 dedup/도메인/완료 N·M/소요시간)를 특수 렌더로 유지(§5.5 출처 pill로 흡수).
  - (b) 풍부한 요약 폐기, 심플 일반 그룹으로 통일(코드↓).
- 레거시 `use-chat-runtime.ts:467`의 `compactDeepResearchMessages` 호출은: 일반 그룹핑이 v3에 안착하면 **메시지 사전변환 방식 자체를 제거**하고 레거시 표면도 `GroupedParts`로 통일하거나, 단계적으로 둘 다 마이그레이션.

---

## 7. 테스트

- **vitest**:
  - `groupBy` 콜백 + group render 단위 테스트: intra-message, **tool name 기준**, 도구 혼합(tavily+read_file), 임계값(N≥2), running/done 상태 → 펼침/접힘.
  - vendored `tool-group.tsx` 렌더 테스트(label·count·active).
  - `deep-research-summary.test.ts` 마이그레이션/업데이트.
- **chat E2E**(기존 `frontend/e2e/chat-stream-integrity.spec.ts` 확장 또는 신규):
  - scripted 모델에 **한 메시지 M개 동일 tool_call** 방출 마커 추가(`backend/app/agent_runtime/e2e_scripted_model.py` — 기존 `E2E_HITL_MULTI` 패턴 참고).
  - 단언: N개 호출이 **1개 그룹 + count**로, running→펼침/done→접힘, 박스 중복 없음.
  - 기존 베이스라인/known-flake 인지: cg47(`chat-langgraph-v3.spec.ts:47`, subagent 완료 stall, 별도), visual-matrix:146(부하 flake).
- **가드**: Phase A/B render-integrity 스펙들 확장.

---

## 8. 리스크 / 미해결 질문

1. **★per-tool UI 라우팅 유지**: `GroupedParts`의 group `children`이 그룹 내부 `tool-call` part를 렌더할 때 기존 `makeAssistantToolUI` 등록 UI가 그대로 적용되는지 **구현 첫 단계에서 검증**(§5.4). 안 되면 명시 위임 경로 필요. — 최대 불확실성.
2. **vendored tool-fallback approval**: 0.14.18엔 approval API(`respondToApproval`, 0.14.19+) 미존재 → tool-fallback.tsx의 Approval 서브컴포넌트는 제거하고 자체 `ApprovalCard` 사용(§5.3).
3. **cross-message**: 실측은 intra-message 우세. `GroupedParts`는 인접 그룹이라 충분. 순차 단일호출 메시지를 내는 에이전트가 생기면 `Unstable_PartsGrouped`(non-adjacent, unstable) 검토 — **일단 보류**.
4. **레거시 런타임**: 일반 그룹핑을 레거시/빌더 표면에도 적용할지(§6). 우선 v3만, 이후 통일.
5. **`makeAssistantToolUI` deprecated (0.14.24)**: 본 작업은 0.14.18 기준이라 무관하나, **장기적으로 27개 등록을 toolkit `render` API로 이전**해야 함(별도 트랙, §9 참고). 이 작업을 GroupedParts와 엮지 말 것.

---

## 9. 견적

- 공식 `GroupedParts`+`groupPartByType` 와이어링 + vendored `tool-group.tsx` 정리 + tool-meta 맵 + 테스트: **~2 dev-days** (커스텀 컨테이너를 새로 안 만들어 초판 대비 단축).
- 풍부한 deep-research 요약 보존/흡수(6-a) + 출처 pill(5.5): **+0.5~1d.**
- (별도 트랙, 본 작업과 분리) 0.14.24 업그레이드 타당성 + `makeAssistantToolUI`→toolkit `render` 마이그레이션 영향 분석: 별도 산정.

---

## 10. 공식 API 가용성 검증 (세션 2, 결정적)

- 설치 `@assistant-ui/react` **0.14.18**. npm 최신 = **0.14.24**(monorepo 소스 HEAD와 버전 일치, 소스가 앞서지 않음). 9개 docs 기능 전부 0.14.24에 출시 완료.
- **0.14.18에 이미 존재(직접 grep 확인)** — `dist/index.d.ts`+`index.js`가 다음을 export:
  - `groupPartByType`, `GroupByContext` (런타임+타입, root import 가능)
  - `MessagePrimitive.GroupedParts` (STABLE), `Unstable_PartsGrouped`, `Unstable_PartsGroupedByParentId`(deprecated)
  - `ReasoningMessagePartComponent`/`SourceMessagePartComponent`/`FileMessagePartComponent` + `useMessagePart{Reasoning,Source,File,Image}` 훅 + 각 part type 슬롯
  - `AttachmentPrimitive`, `CompositeAttachmentAdapter`, `SimpleImageAttachmentAdapter` 등 (우리는 이미 attachment 프리미티브 사용 중)
  - `makeAssistantToolUI`/`makeAssistantDataUI` (0.14.18에서 not deprecated)
- **없음**: `context-display`(런타임 export 없음, registry 컴포넌트만 + 서버 usage forwarding 필요), `directive-text`(메시지 part 아님 — composer용 `unstable_` directive만), `ToolGroupRoot/Trigger/Content`(런타임 프리미티브 아님 — registry tsx의 로컬 컴포넌트). `toolUI`/`ToolFallback` 리터럴은 **export 아님**(JSDoc example/scaffold용).
- **초판 부록 A의 오류 원인**: `frontend/node_modules/.pnpm`의 **stale `core@0.1.13`**을 grep해서 0건이 나옴. 실제 `react@0.14.18`이 resolve하는 건 **repo-root `.pnpm`의 `core@0.2.14`**. 재검증 시 root 스토어를 grep할 것.

---

## 부록 A — (정정) 공식 grouping 가용성

> **초판 부록 A는 "공식 `GroupedParts`/`ToolGroup`이 npm 미출시(dist 0건)"라고 결론냈으나 이는 오류였다.** §10 참조.

- 정정된 사실: 공식 `MessagePrimitive.GroupedParts` + `groupBy` prop + `groupPartByType` 헬퍼는 **현재 0.14.18에서 STABLE로 사용 가능**하다. 따라서 본 문서는 **공식 API 직접 채택**으로 진행한다.
- 여전히 유효한 주의: **0.14.18 → 0.14.24 업그레이드는 과거 실측에서 vitest 5파일 로드 실패**(import breaking + `@assistant-ui/tap` 0.7→0.9 충돌)를 유발해 revert된 전력이 있다. 본 그룹핑 작업은 **업그레이드가 필요 없으므로**(0.14.18로 충분) 이 리스크와 무관하다. 업그레이드가 필요해지면 **별도 격리 작업**(clean reinstall + 전체 vitest 그린)으로 다룬다.
- 공식 docs 패턴(참고):
  ```tsx
  <MessagePrimitive.GroupedParts groupBy={groupPartByType({ "tool-call": ["group-tool"] })}>
    {({ part, children }) =>
      part.type === "group-tool"
        ? <ToolGroupRoot><ToolGroupTrigger count={part.indices.length} active={part.status.type === "running"} /><ToolGroupContent>{children}</ToolGroupContent></ToolGroupRoot>
        : part.type === "tool-call" ? <ToolFallback {...part} /> : children}
  </MessagePrimitive.GroupedParts>
  ```
  - `ToolGroupRoot/Trigger/Content`·`ToolFallback`는 **registry copy-paste 컴포넌트**(우리가 vendoring·편집). 런타임 락인은 `GroupedParts`/`groupPartByType`뿐.

---

## 부록 B — 핵심 파일 인덱스

| 목적 | 경로 |
|------|------|
| v3 thread 렌더 진입점 | `frontend/src/components/chat/assistant-thread.tsx` (≈239, `MessagePrimitive.Content`→`GroupedParts`로 교체) |
| per-tool UI 등록 레지스트리 | `frontend/src/lib/chat/tool-ui-registry.ts` (27개 `makeAssistantToolUI`) |
| catch-all 도구 박스 | `frontend/src/components/chat/tool-ui/generic-tool-ui.tsx` (`ToolFallbackPanel`/`GenericToolFallback`) |
| 현재 그룹핑 로직(tavily 전용) | `frontend/src/lib/chat/deep-research-summary.ts` |
| 현재 그룹 박스 UI | `frontend/src/components/chat/tool-ui/deep-research-summary-ui.tsx` |
| 그룹핑 와이어링(레거시) | `frontend/src/lib/chat/use-chat-runtime.ts:467` |
| 접기 UX 재사용 | `frontend/src/components/chat/tool-ui/collapsible-pill.tsx` |
| 출처 집계 재사용 | `frontend/src/lib/chat/search-results.ts` |
| 신규 도구 메타 맵 | `frontend/src/lib/chat/tool-group-meta.ts` (신규) |
| vendored 그룹 컨테이너 | `frontend/src/components/assistant-ui/tool-group.tsx` (shadcn add, 신규) |
| vendored fallback(참고) | `frontend/src/components/assistant-ui/tool-fallback.tsx` (shadcn add; approval 제거) |
| scripted E2E 모델(테스트 마커) | `backend/app/agent_runtime/e2e_scripted_model.py` |
| tavily 도구 정의(registry) | `backend/app/tools/definitions/tavily_search.py` |
| 기존 그룹핑 테스트 | `frontend/src/lib/chat/deep-research-summary.test.ts` |
| 공식 registry 원본(로컬 참고) | `/Users/chester/dev/ref/assistant-ui/packages/ui/src/components/assistant-ui/{tool-group,tool-fallback}.tsx` |
