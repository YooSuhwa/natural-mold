# Moldy Chat Design Contract

## Baseline

이 문서는 assistant-ui 0.15 마이그레이션 시 유지해야 하는 현재 Moldy 채팅의
소스 기준선이다. 외부 제품이나 Aside UI를 관찰해 복제한 기준선이 아니다.

- `@assistant-ui/react`는 메시지·컴포저·도구 렌더링 표면을 담당한다.
- 기존 Moldy SSE와 LangGraph v3 BFF가 실행 상태의 기준이다. UI 라이브러리
  업데이트가 전송 프로토콜이나 체크포인트 수명주기를 바꾸지 않는다.
- 기본 대화, Builder, Assistant Panel은 같은 메시지 프리미티브를 공유하지만
  각 표면의 도구 및 HITL 정책은 유지한다.

## Layout and surfaces

- 스레드는 세로 전체 높이를 사용하며, 스크롤 가능한 transcript와 하단 고정
  composer를 분리한다.
- 일반 transcript 폭은 `max-w-3xl`, Builder transcript 폭은 `max-w-4xl`이다.
- 사용자 메시지는 오른쪽 정렬된 primary bubble과 사용자 avatar를 사용한다.
  어시스턴트 메시지는 왼쪽 avatar 뒤의 평면 content column으로 렌더한다.
- artifact와 subagent 상세는 기존 right rail에서 연다. 모바일 artifact layer와
  rail 너비/resize 동작은 별도 Moldy surface 계약으로 유지한다.
- surface, radius, shadow, status는 `moldy-*` 클래스와 semantic token을 사용한다.

## Interaction contract

- Enter는 전송, Shift+Enter는 줄바꿈이며 한국어 IME 조합 중에는 전송하지 않는다.
- 실행 중에는 Stop이 현재 run 취소 경로를 사용한다. 실패 메시지는 transcript에
  남고 재시도 affordance를 제공한다.
- 첨부는 composer와 메시지에 유지되며 paste/add-attachment capability를 존중한다.
- 편집, 재생성, branch picker, copy, feedback, token usage, timestamp 동작을
  메시지 metadata와 함께 유지한다.
- streaming loading, tool state, HITL decision, subagent progress, artifact cards,
  reconnect indicator, memory/compaction 표시는 서버 이벤트를 기준으로 유지한다.
- transcript가 바닥에서 벗어나면 scroll-to-bottom control을 노출하고, 검색은
  현재 viewport가 보이는 경우에만 Cmd/Ctrl+F로 연다.

## assistant-ui 0.15 compatibility

- scope action은 `useAui()`의 property accessor(`aui.thread`, `aui.composer`,
  `aui.message`)를 사용한다. scope가 선택 사항이면 `aui.optional.<scope>`로
  availability를 확인한다.
- 상태 구독은 `useAuiState`를 사용한다. 제거된 legacy runtime/context hook은
  사용하지 않는다.
- LangChain 메시지 변환기는 0.0.29의 단일-message 반환 계약을 따르면서 Moldy의
  usage, branch, terminal notice, `moldy_ui` data part metadata를 보존한다.
- Toolkit/AuiConfig 전환과 surface별 tool registry 재구성은 후속 작업 범위다.
