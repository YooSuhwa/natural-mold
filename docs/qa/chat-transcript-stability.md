# Chat Transcript Stability QA

채팅 런타임을 수정할 때 아래 묶음은 하나의 회귀 기준으로 취급한다. 한 항목을 고치면서
다른 항목이 흔들리면 통과로 보지 않는다.

## 자동 실행

```bash
cd frontend
pnpm test:e2e:chat-transcript-stability
```

이 스위트는 실제 backend/frontend Playwright 서버를 사용한다. 앱 기본 채팅 런타임은
`langgraph_v3`이므로 `pnpm test:e2e` 전체 실행에도 포함된다. 단,
`NEXT_PUBLIC_CHAT_RUNTIME=legacy`로 명시한 legacy 실행에서는 skip된다.

## 고정 기준

1. 새 대화 `/conversations/new`는 메시지를 보내기 전까지 실제 conversation row를
   만들지 않고, 다른 화면으로 이동하면 빈 draft가 남지 않는다.
2. 새 대화의 첫 메시지를 보내면 URL이 실제 `conversationId`로 전환되고, 사이드바의
   `새 대화` 임시 row가 실제 대화 row로 승격된다. 같은 대화가 중복 row로 추가되면
   실패다.
3. 첫 메시지 이후 오프너/캐릭터 empty state가 다시 나타나면 실패다.
4. 새 대화에서 3턴 이상 연속으로 보내도 기존 사용자 메시지와 assistant 메시지가
   사라졌다가 다시 나타나면 실패다.
5. 사용자 메시지를 수정하면 수정 대상 아래의 기존 assistant 답변은 즉시 제거되고,
   같은 사용자 메시지가 임시 bubble로 중복 표시되면 실패다.
6. 사용자 메시지를 여러 번 수정한 뒤 최신 branch가 선택되어야 한다. 재생성은 최신
   사용자 branch 기준으로 실행되어야 하고, branch index가 과거 branch로 밀리면 실패다.
7. LLM 답변 재생성은 새 assistant branch를 마지막 branch로 표시해야 한다. branch
   picker는 hover 상태가 유지되는 동안 사라지면 실패다.
8. `ask_user` interrupt는 같은 요청에 대해 카드가 정확히 1개만 표시되어야 한다.
   카드가 나타나는 동안 사용자가 보낸 문장이 사라지거나 빈 bubble로 바뀌면 실패다.
9. 스트리밍 중 run notice/tool status는 같은 상태가 여러 카드로 중복 표시되면 실패다.
   표시할 거면 하나가 안정적으로 유지되고, 표시하지 않을 거면 잠깐 나타났다가 사라지면
   안 된다.
10. assistant rich output은 일반 텍스트와 같은 transcript 안정성 규칙을 따른다. 코드 블록,
    인라인 코드, GFM 표/체크리스트, KaTeX 수식, 이미지, 링크, blockquote, Mermaid가
    렌더링되고 reload 뒤에도 유지되어야 한다. 이 출력 중 하나라도 사라지거나 사용자의
    prompt가 빈 bubble로 바뀌면 실패다. 이 경로는 내부 테스트 마커가 아니라 사용자가
    실제로 입력할 법한 자연어 요청으로 출력 형식을 유도해야 한다.

## 테스트 매핑

- `frontend/e2e/draft-conversation-langgraph-v3.spec.ts`
  - draft 생성/폐기
  - `/new` → 실제 conversation 전환
  - empty state 재등장 방지
  - 3턴 이상 메시지 안정성
  - 사용자 메시지 수정과 branch 최신성
- `frontend/e2e/chat-langgraph-v3-regressions.spec.ts`
  - 재생성 branch 복원
  - slow stream reconnect 중복 방지
  - interrupted/HITL 상태 복원
- `frontend/e2e/chat-transcript-stability.spec.ts`
  - `ask_user` 카드 단일 표시
  - `ask_user` 렌더링 중 사용자 prompt 유지
  - `/new` draft promotion과 `ask_user` 동시 경로
  - rich assistant output 렌더링: 코드, 표, 수식, 이미지, 링크, 인용, 체크리스트, Mermaid
  - rich output 렌더링 중 사용자 prompt 유지와 reload persistence
