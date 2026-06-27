# 검토: assistant-ui 빌트인 기능 9종 도입 타당성

> 작성 2026-06-26 · 브랜치 `worktree-feature+generic-tool-call-grouping`
> 동기: 직접 구현한 채팅 UI가 많은데, assistant-ui 기본 제공을 쓰고 비주얼만 우리 디자인에 맞추면 안정성이 더 좋지 않을까? — 9종 전수 검토.
> 방법: tool-group 때처럼 우리 채팅 코드 + ref 레포(0.14.24) docs/registry 소스를 대조. 3개 서브에이전트 병렬 분석 후 종합.

---

## 0. 한 줄 결론

**9종 중 "공식 컴포넌트(registry `.tsx`) 채택(vendoring)"이 순이득인 건 없다.** 두 가지 구조적 벽 때문이다:

1. **데이터 모델 불일치** — 우리 채팅은 `reasoning`/`source`/`file` 같은 **공식 message-part type을 emit하지 않는다**. (reasoning=`data` part+activity, sources=tool result 임베딩, file=M59 artifact 시스템.) 공식 프리미티브에 넣을 입력 자체가 없다.
2. **디자인 토큰 비용** — registry `.tsx`는 전부 raw tailwind(`rounded-lg`/`shadow-*`/hex/arbitrary spacing)라 우리 `pnpm lint:design-system`을 위반한다. 복붙 불가, moldy 시맨틱 토큰으로 재작성해야 한다. (이게 Phase-2b에서 공식 `tool-group.tsx` 대신 `CollapsiblePill`을 재사용한 이유와 동일.)

대신 **선별적으로 가치 있는 것**은 있다: ① **공식 런타임 프리미티브(훅/슬롯)**를 쓰되 비주얼은 우리 토큰으로, ② **우리에게 아예 없는 기능**의 신규 도입. 아래 표가 핵심.

---

## 1. 9종 판정 요약

| # | 기능 | 우리 현황 | 판정 | 우선순위 | 0.14.18 가능 |
|---|------|----------|------|:---:|:---:|
| 1 | **part-grouping** | Phase-2b로 공식 `GroupedParts` 채택 | **현행유지(올바름)** | 하 | O |
| 2 | **tool-group** | `CollapsiblePill` 재사용(의도적) | **유지 + `useScrollLock` 보강** | 중 | O |
| 3 | **tool-fallback** | `generic-tool-ui` 자체 | **유지 + argsText/error 보강** | 하 | 일부 O |
| 4 | **reasoning** | `data` part + "thinking" activity | **유지** (모델 불일치) | 하 | 입력 부재 |
| 5 | **sources** | tool result 파싱 + 집계 | **유지** (모델 불일치) | 하 | 입력 부재 |
| 6 | **file** | M59 `conversation_artifacts` | **유지** (모델 불일치) | 하 | 입력 부재 |
| 7 | **attachment** | 컴포저 칩만 official, 메시지 표시 없음 | **부분도입(신규)** | **중** | O |
| 8 | **context-display** | 토큰/비용은 `TokenUsagePopover`로 **이미 있음**, 컨텍스트 창 % 게이지만 없음 | **부분신규**(게이지만) | 하 | registry-only |
| 9 | **directive-text** | 없음(슬래시/멘션 미사용) | **스킵** | 하 | unstable |
| 10 | **message-timing** (TTFT·tok/s) | 없음 | **신규도입(추천)** | 중 | O (`useMessageTiming` 가용) |

---

## 2. "실익 있는 것"만 추린 액션 (권장 순)

### A. attachment — 메시지 본문 첨부 표시 (우선순위: 중) ⭐ 가장 실익 큼
- **문제**: 우리는 컴포저 스테이징 칩만 official(`AttachmentPrimitive`)로 만들고, **`MessagePrimitive.Attachments`/`UserMessageAttachments`를 어디에도 렌더하지 않는다**(grep 0건). 변환기(`convert-message.ts`)가 과거 user 메시지에 첨부를 붙여도 **transcript에 안 보인다** → 사용자가 "내가 뭘 보냈는지" 대화 기록에서 확인 불가.
- **추가로 official에 있는데 우리에 없는 것**: 이미지 **썸네일 타일** + 클릭 시 **확대 다이얼로그**(registry `attachment.tsx`의 `AttachmentPreviewDialog`). 우리 칩은 아이콘만.
- **가능 여부**: `MessagePrimitive.Attachments`는 0.14.18 가용 → 업그레이드 불필요. registry tsx는 참고만, 비주얼은 moldy 토큰.
- **선결 조건**: 백엔드 첨부 hydration 계약 확인 — CLAUDE.md에 "첨부는 message_id=null이라 메시지 응답에 echo 안 됨"이 명시. UI만 붙이면 빈 렌더일 수 있으니 **GET /messages가 첨부를 hydrate하는지부터** 확인.

### B. tool-group — `useScrollLock` 보강 (우선순위: 중) ⭐ 최저비용·고가성비
- **문제**: 긴 채팅에서 그룹을 접을 때 스크롤 위치가 튈 수 있다. 공식 `tool-group.tsx`는 `useScrollLock`으로 방지.
- **핵심**: `useScrollLock`은 **0.14.18 런타임 export** → vendoring 없이 우리 `CollapsiblePill`에 훅만 끌어와 한 줄 보강 가능.
- (collapse 애니메이션·자식 stagger는 폴리시 항목 → 우선순위 하.)

### C. tool-fallback — 스트리밍 argsText + error reason (우선순위: 하)
- **갭**: 우리는 완성된 `args` 객체만 `JSON.stringify` → 도구 호출이 길면 스트리밍 중 빈 패널. 공식은 part의 `argsText`(부분 JSON)를 실시간 표시. 또 `incomplete.reason`(error/cancelled 사유)을 분리 표시. 둘 다 **0.14.18에서 보강 가능**.
- duration 표시(`useToolCallElapsed`)는 0.14.24 신규라 지금은 보류.
- **approval은 보강 불필요** — 우리 `ApprovalCard`(수정후승인·카운트다운·redaction·멀티액션 HiTL)가 공식 `ToolFallbackApproval`보다 우위.

### D. message-timing (TTFT·tok/s·총시간) — `TokenUsagePopover` 한 줄 추가 (우선순위: 중) ⭐ 실익 큼
- **공식 제공**: `useMessageTiming()`(0.14.18 가용) — `message.metadata.timing`을 읽음. `message-timing.tsx`가 **First token(TTFT)·총 스트림 시간·tok/s·chunks**를 표시.
- **왜 깔끔한가**: reasoning/sources/file과 달리 **part type이 아니라 그냥 메타데이터 슬롯**이라 데이터 모델 충돌이 없다. 우리는 이미 `convertMessage`가 `metadata.custom`에 usage를 박고 `TokenUsagePopover`가 그걸 읽으므로, **timing을 usage breakdown에 합치면** 토큰/비용이 가는 모든 경로(v3/legacy + 영속)를 그대로 탄다.
- **측정 지점**: 백엔드 streaming 2경로(`streaming.py`·`langgraph_streaming.py`). **총시간은 runner가 이미 `time.monotonic()`으로 잰다**(`agent_stream_runner.py`) → 노출만. TTFT는 첫 `CONTENT_DELTA` yield에 1줄. tok/s = `completion_tokens` ÷ 생성시간.
- **UI**: 정보 과밀 방지를 위해 **미니멀 한 줄**(`45 tok/s · 5.2s · 첫토큰 0.42s`)로 팝오버 하단에 추가. 평소 화면은 `ⓘ 1,234` 그대로.
- 우선순위 중. attachment 다음급. (구현 진행 중.)

### E. context-display (컨텍스트 창 % 게이지) — 선택 (우선순위: 하)
- **정정**: 토큰/비용은 이미 `TokenUsagePopover`(메시지별 4종 토큰 + 비용) + `TokenBar`(세션 누적)로 **표시 중**. context-display가 유일하게 더 주는 건 **"컨텍스트 창 한도 대비 % 게이지"**(한도 경고)뿐.
- 그 게이지마저 데이터(`model.context_window` + 누적 토큰)는 보유. 공식 컴포넌트는 `@assistant-ui/react-ai-sdk` 의존(미설치)이라 부적합 → 원하면 `TokenBar`/`TokenUsagePopover`에 **자체 % 게이지** 추가. 있으면 좋은 정도.

---

## 3. "유지" 판정의 근거 (4·5·6 = reasoning/sources/file)

세 기능 모두 **공식 message-part type을 우리가 emit하지 않는 것**이 근본 원인 — 채택하려면 백엔드 part-type 신설 + 변환 계층 재작성이 선행돼야 해 **요청 범위를 초과**한다.

- **reasoning**: 백엔드가 `moldy.reasoning` custom 이벤트 → 프론트에서 `data` part(`ReasoningDataUI`) + streaming "thinking" activity로 **이원화**. 공식은 `reasoning` part 전제. 우리 `CollapsiblePill`이 이미 접이식 UX 제공 → 순이득 제한적.
- **sources**: 출처가 **tool result JSON 내부 임베딩**(백엔드가 native `source` part 안 만듦). 공식 `SourceMessagePartComponent`에 넣을 입력이 없다. 우리 `search-results.ts`+`ToolGroupContainer` 집계(도메인 배지+"출처 N개")가 이미 LITE 요구 충족(61b2367d).
- **file**: 채팅 파일 = **M59 `conversation_artifacts`**(URL/메타/버전/우측레일 프리뷰) 또는 deepagents 가상 FS state. 공식 file은 inline base64 part 전제 → 구조적으로 무관. 프리미티브로 바꾸면 레일 토글·버전·열람기록 등 **기능 후퇴**.

> 차용 가능한 최대치는 "프리미티브 채택"이 아니라 **유틸/비주얼 참고**: `file.tsx`의 `getMimeTypeIcon`/`formatFileSize`, `sources.tsx`의 favicon+이니셜 fallback 패턴을 필요 시 우리 토큰으로 재구현하는 선.

---

## 4. ⚠️ 분석 중 발견한 사실 정정 (메모리/plan-doc 오류)

서브에이전트가 설치된 `0.14.18`(core `0.2.14`)의 실제 `.d.ts`를 검증하며 두 가지를 정정:

1. **`makeAssistantToolUI`는 0.14.24가 아니라 이미 `0.14.18`에서 `@deprecated`다.** (deprecation 메시지: "Put render/renderText on the matching toolkit entry, or use MessagePrimitive.Parts inline tool render overrides".) 즉 우리 27개 tool UI(`tool-ui-registry.ts`)는 **이미 deprecated API 위**에 있다 → toolkit `render` 마이그레이션의 시급성이 기존 인지보다 높음(여전히 별도 0.14.24 트랙).
2. **`respondToApproval`(서버 approval gate)은 0.14.19+가 아니라 이미 `0.14.18`에 존재**한다(`ToolCallMessagePartProps`에 `addResult`/`resume`/`respondToApproval` 모두). plan-doc §5.3의 "0.14.19+" 기재는 오류. (단 우리는 deepagents interrupt 기반 `useHiTL`+LangGraph resume을 쓰는 게 정당하므로 교체 불필요.)
3. **context-display 초안 정정**: 초기 분석은 "토큰/비용 표시 기능 없음(TokenBar만)"이라 했으나, **assistant 메시지 푸터의 `TokenUsagePopover`가 이미 토큰 4종 + 비용을 표시**한다(서브에이전트가 누락). 따라서 context-display는 "신규 기능"이 아니라 "토큰/비용은 이미 있고 컨텍스트 창 % 게이지만 없는" 상태. (§2-E로 반영.)
4. **`useMessageTiming`은 자동 측정이 아님**: `message.metadata.timing`을 **읽기만** 한다(`hooks/useMessageTiming.js`). 측정값(TTFT/total/tok-s)은 우리가 채워야 함 → §2-D 참고.

---

## 5. 관통 원칙 + 다음 스텝

**원칙**: 공식 **런타임 프리미티브(훅/슬롯)**는 가치 있을 때 채택하되, registry `.tsx` **비주얼은 우리 moldy 토큰으로 재구현**한다. (Phase-2b의 CollapsiblePill 선택과 일관.) 데이터 모델(part type) 불일치가 reasoning/sources/file 채택을 막는 근본 벽이고, 0.14.24 업그레이드는 vitest 회귀 전력이 있는 **별도 트랙**(대부분 보강은 0.14.18에서 가능).

**다음 스텝 제안** (각각 독립 태스크):
- (중) **message-timing (TTFT·tok/s)** — `TokenUsagePopover`에 미니멀 한 줄. usage breakdown에 timing 합쳐 영속까지 편승. **← 현재 구현 진행 중.**
- (중) **attachment 메시지 표시** — 백엔드 echo 계약 확인 → `MessagePrimitive.Attachments` + 이미지 확대 다이얼로그를 moldy 토큰으로.
- (중) **tool-group `useScrollLock`** — 한 줄 보강, 가성비 최고.
- (하) tool-fallback 스트리밍 argsText / error reason.
- (하) context-display = 컨텍스트 창 % 게이지 자체 확장.
- (별도) **0.14.24 + toolkit 마이그레이션 트랙** — `makeAssistantToolUI`(이미 deprecated)→toolkit `render`. 이걸 하면 part-grouping의 HiTL 제외 하드코딩(`NON_GROUPABLE_TOOLS`)도 공식 `"standalone-tool-call"` 자동 분류로 대체돼 신규 HiTL 도구 회귀를 구조적으로 제거.

**스킵**: directive-text(슬래시/멘션 기능 자체 없음, unstable API), file/sources/reasoning 공식 채택(데이터 모델 불일치).
