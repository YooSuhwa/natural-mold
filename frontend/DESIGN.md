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
