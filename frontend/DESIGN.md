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
- `DialogShell`은 지정된 크기와 관계없이 viewport 좌우 1rem gutter 안에 머물러야
  한다. 두 열 선택기는 1024px 미만에서 한 열로 전환하고, 현재 선택 목록의 높이를
  제한해 다음 탐색 영역과 닫기 동작을 같은 스크롤 흐름에서 사용할 수 있어야 한다.
- 한국어 설명 문구는 단어 내부의 마지막 음절이 고립되지 않도록 `break-keep`과
  균형 잡힌 wrapping을 사용한다. URL, 이메일, 사용자 입력처럼 긴 비분절 값은
  별도의 `break-words` 또는 가로 스크롤 경계를 둔다.
- 설정 사이드바의 navigation 영역과 하단 utility footer는 서로 겹치지 않는다.
  메뉴가 늘어나면 navigation 스크롤을 유지하고 마지막 항목이 footer 경계에서
  부분적으로 잘리지 않도록 그룹 밀도와 하단 경계를 함께 검증한다.

## Interaction contract

- 반복 탐색 앞에는 키보드로 초점 가능한 본문 건너뛰기 링크를 두고, 앱 셸의
  `main` landmark는 페이지당 하나만 유지한다.
- `primary-strong` 배경 위 텍스트는 테마별 `primary-strong-foreground` 토큰을
  사용하며, 작은 상태 배지와 사이드바 캡션도 WCAG AA 명도 대비를 만족해야 한다.
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
- tool renderer는 `defineToolkit` 도메인 registry로 선언하고 각 runtime
  provider에 `AuiConfig({ tools: Tools({ toolkit }) })`로 주입한다. renderer는
  backend가 실행한 tool call을 표시만 하며 client executor를 추가하지 않는다.
- main chat과 Assistant Panel은 approval을 포함한 `ALL_TOOLKIT`, settings 테스트
  chat은 paused-run HITL만 뺀 `SETTINGS_TEST_TOOLKIT`, Builder는 전용
  `BUILDER_TOOLKIT`을 사용한다.
- 등록되지 않은 tool은 grouped-parts fallback이 담당하고, search-shaped 결과의
  rich rendering을 유지한다. `DataUI`는 기존 `AssistantThread` 등록 경로를
  그대로 사용한다.

## Selected text and side chat

- Codex 참조 이미지의 세 가지 동작(채팅에 추가 / 더 자세히 / 사이드 채팅에
  질문하기)을 흰색 선택 메뉴로 제공한다. 도구 출력이나 버튼 텍스트는 선택 대상에서
  제외하고, 하나의 메시지 본문 안에서 선택한 텍스트만 인용한다.
- 인용 칩은 원문, 출처 대화, 선택한 메시지, 사용자 댓글을 구분한다. 호버·초점으로
  미리보기, 클릭으로 댓글 편집·원문 이동, 개별 제거를 지원한다. 원문은 수정하지 않는다.
- 본 채팅과 사이드 채팅은 같은 에이전트를 사용하되 대화 ID, 실행, 입력 초안,
  대기열과 런타임 상태를 분리한다. 전달은 명시적으로 선택한 인용만 사용하며 전체
  대화를 자동 복제하지 않는다. 기존 설정용 Assistant나 파일 뷰어는 대체하지 않는다.
- 사이드 대화는 서버에 저장하지만 일반 목록에는 자동 노출하지 않는다. 패널 닫기는
  삭제가 아니며, 명시적 저장 동작으로 일반 대화로 전환한다. 임시·자동 삭제라고
  표시하지 않는다. 모바일은 단일 포커스 dialog, 데스크톱은 오른쪽 분할 패널이다.
- 부모-사이드 연결은 DB에 저장하여 새로고침 뒤에도 같은 대화를 연다. 부모 삭제 시
  숨겨진 사이드 기록은 일반 목록에 보존한다. M77 마이그레이션이 필요하다.
- base-ui anchored popover의 collision/focus/escape 동작을 사용한다. beui tooltip의
  호버·초점·터치 접근성 원칙을 참고하되 추가 모션 라이브러리는 설치하지 않는다.
  색상 전환은 기존 150ms 토큰을 사용하고 reduced-motion에서는 움직임을 제거한다.

## MCP Apps policy

- MCP Apps의 resource 읽기와 tool 호출은 대화·run·tool-call에 결합된 서버 proxy만
  사용한다. 브라우저의 URI, server ID, binding ID, URL, credential은 권한 근거가
  아니다.
- app iframe은 추가 form, popup, download 권한 없이 격리하고, resource CSP는
  기본 거부 후 서버가 검증한 HTTPS origin만 허용한다.
- 일반 링크 열기와 대화 메시지 전송은 Moldy host policy에서 항상 오류로 거부한다.
  `@assistant-ui/react` 0.15.18의 공개 API는 handler를 생략하면 각각
  `window.open`과 thread append 기본값을 활성화하고, 거부 handler를 주면
  initialize 응답에 capability를 표시한다. 따라서 현재는 명시적인 거부 handler로
  side effect를 차단하며, widget에 action이 보인 뒤 policy error가 반환될 수 있는
  SDK 호환성 debt를 수용한다. 비공개 protocol shim이나 package patch는 사용하지
  않는다.
