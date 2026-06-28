# cg47 subagent-completion stall — 진단 + 수정

브랜치: `fix/subagent-completion-stall` (worktree, main `e370c3cf` 기준)
대상 실패: `frontend/e2e/chat-langgraph-v3.spec.ts:47` — line 91
`getByText('E2E subagent scoped result ready.')` (child subagent scoped result)

## 시나리오 흐름 (정적 분석 완료)
parent prompt `E2E_LANGGRAPH_V3 subagent=agent_XXXX` →
1. `write_todos` 3개 — "Render delegated subagent progress"는 스크립트가
   `in_progress`로 **하드코딩, 절대 갱신 안 함** (진행중인 게 정상 상태)
2. `task`(subagent 위임) — child가 `E2E_SUBAGENT` 프롬프트로
   "E2E subagent scoped result ready." 4파트 스트리밍 (각 0.75s sleep)
3. `execute_in_skill`(docx) → HITL interrupt → 승인 → 아티팩트 → final

## 핵심 재해석 (메모리 가설 수정 필요)
- 메모리는 "Render delegated subagent progress 진행중 = stall"로 봤지만,
  그 TODO는 스크립트 하드코딩 상태다 → stall 증거 아님.
- parent final text가 렌더된다 = parent가 `task`를 지나 final까지 도달 =
  **subagent task는 백엔드에서 리턴됨** (stall 아님 가능성).
- line 90 childRuntimeName chip은 통과(추정), line 91 결과 텍스트만 실패 →
  **subagent는 discovery되나 inner 결과 텍스트가 UI에 surface 안 됨**이 가설.

## 진단 계획 (추측 패치 금지 — 로그/DB로 확정)
- [ ] M1: throwaway 스택 기동 (PG 5434 + backend 8102 + next 3101),
      CLAUDE.md "E2E 포트/DB 격리" 절차. 백엔드 DEBUG 로깅 활성.
- [ ] M2: `chat-langgraph-v3.spec.ts:47` 재현 (3/3 결정론 실패 재확인).
- [ ] M3: 근본 원인 확정 — 두 갈래를 로그/DB ground-truth로 분리:
      (a) 백엔드: throwaway DB `message_events`/chunks 쿼리 →
          subagent inner "messages" 이벤트가 persist됐나? namespace/path는?
          `task` 툴/subagent lifecycle 이벤트의 status·output은?
      (b) 1순위 의심 검증: `subagents.py:116` child thread_id=parent 공유 →
          checkpointer 풀 경쟁으로 subagent 스트림이 실제 stall/drop 되나,
          아니면 surface(extract_subagent_discovery/프론트 렌더) 문제인가.
- [ ] M4: 확정된 원인에 deepagents 권장 방식으로 수정.
- [ ] M5: 동일 throwaway 스택에서 :47 그린 확인 (--retries=0, 반복).
      v3 회귀 6스펙 + 관련 백엔드 pytest 회귀 없음 확인.

## ✅ M3 근본 원인 확정 (2026-06-28) — 백엔드 stall 반증, 프론트 hydration 갭
증거:
- 백엔드 emit/persist 정상: subagent "E2E subagent scoped result ready."가
  `messages` ns=['tools:<uuid>'] + `task` tool-finished ToolMessage(ns=[]) +
  합성 task chip 으로 스트림, `message_event_chunks.events`에 3중 persist 확인.
  (raw astream_events(v3) 프로브 `MOLDY_CG47_DEBUG=1` + psql 덤프)
- 3개 의심 모두 반증: #1 shares_thread=True지만 stall 없음 / #3 child interrupt_on
  존재하나 미트리거(텍스트만 반환) / #2 완료 신호 도달함.
- "진행중" TODO는 스크립트 하드코딩 상태(스톨 증거 아님).
- 라이브 스크린샷(01-live-state.png, reload 전): subagent 카드 **펼침 + 텍스트 보임**.
  reload+resume 후 DOM(error-context.md): 카드 **접힘**, 텍스트 없음.
근본 원인 = **프론트 v3 history hydration 갭**: reload 시 `loadServerThreadState`가
`values.messages`만 복원하고 langchain-react SDK의 `stream.subagents`(+scoped 메시지)를
재구성하지 않음 → `useSubagentSnapshot`=null → `canRenderScopedDetails=false` →
SubagentCard 접힘 → scoped 결과 텍스트 소실. 수정은 **프론트엔드**, subagents.py 아님.

## ⏸ M4 수정 방향 — 사용자 확인 대기 (premise가 backend→frontend로 전환됨)

## 진단용 임시 변경 (수정 확정 후 제거 예정)
- backend `langgraph_streaming.py` / `subagents.py`: `MOLDY_CG47_DEBUG` 게이트 프로브
- frontend `chat-langgraph-v3.spec.ts`: teardown을 `CG47_KEEP_DATA` 게이트로 감쌈

## 검증 커맨드
- repro: throwaway(PG5434/be8102/fe3101) +
  `pnpm exec playwright test e2e/chat-langgraph-v3.spec.ts --grep "streams DeepAgents" --retries=0`
- done-when: :47 결정론적 green, 6 v3 스펙 green, 백엔드 pytest green

## ✅ M4 수정 (1파일) — `collapsible-pill.tsx`
정확한 메커니즘(네트워크 로깅 probe로 확정): reload 시 SDK는 getState 체크포인트
메시지에서 subagent **discovery**를 seed하지만, 카드가 그 비동기 seed 전에 mount →
mount 시 `useSubagentSnapshot`=null → `canRenderScopedDetails`/`defaultExpanded`=false →
`CollapsiblePill`이 `useState(defaultExpanded)`로 **mount 시 1회만** expand 초기화 →
seed 후 defaultExpanded가 true로 바뀌어도 다시 안 펼쳐짐 → `SubagentDetails` 미마운트 →
lazy `tools:<uuid>` namespace 해석(getHistory) 미발생 → scoped 텍스트 inline 미렌더.
(우측 레일은 클릭 시 자체 마운트라 보임 — 데이터는 항상 복원 가능했음.)
수정: `defaultExpanded` false→true 상승 에지에서 `setExpanded(true)` (useRef+useEffect),
사용자 수동 collapse는 보존. 백엔드 무수정.

## ✅ M5 검증 결과
- `chat-langgraph-v3.spec.ts:47` — **3/3 결정론적 green** (`--repeat-each=3`; 이전 0/3)
- v3 E2E 풀스윕 18/19 green
- frontend vitest **1049/1049**, tsc(src) 0, lint clean, tool-ui/subagent 단위 green
- ⚠️ 1개 실패 = `visual-matrix:146` — **내 변경 무관 기존 결함**(원본 코드 4/4 실패로 확정).
  스트리밍 중 활동패널 "작업 목록" + 메시지 "Plan" 카드가 todos 동시 렌더 → strict-mode
  2-match. 별개 테스트/별개 원인, 별도 작업으로 분리(`.first()` 추가 등).

## 잔여
- 커밋/PR은 사용자 지시 대기. throwaway docker `moldy-cg47-pg` 정리 필요 시 제거.
