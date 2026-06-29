# 메인 채팅(v3 에이전트 런) 기능 갭 분석

- **작성일**: 2026-06-30
- **상태**: 발견(discovery) — 우선순위 합의 전. 구현/일정 미확정.
- **대상**: v3 메인 채팅(`langgraph_v3`, `use-moldy-langgraph-stream.ts` + `useExternalStoreRuntime`). 빌더/어시스턴트 패널은 범위 밖.
- **목적**: "에이전트가 도는 채팅"에서 **부족한 필수 기능 / 있으면 좋을 기능**을 근거(코드·문서)와 함께 정리하고 우선순위를 제안한다.
- **방법**: 프론트 UI/컴포저, 백엔드 런 엔진, 문서/PRD/계획을 각각 인벤토리(병렬 탐색)한 뒤 "현재 있는 것 vs 없는 것"을 교차 대조.

---

## 0. TL;DR

현재 v3 채팅은 **완성도가 높다** — 스트리밍·리치 마크다운(코드/표/수식/mermaid/이미지)·도구 그룹핑·검색 출처집계·HITL 승인/ask_user·아티팩트 프리뷰(20+ 타입)·첨부 표시·컨텍스트 게이지·토큰/비용 팝오버·자동 compaction·서브에이전트·메모리(propose/save)·브랜치/재생성·공유·네비게이터·Generative UI(DataTable/Chart/Stats/Terminal). 메시지 피드백(👍👎)도 배선됨.

따라서 갭은 대부분 **에이전트 능력 / 런 제어 / 마무리(export·검색)** 쪽에 몰려 있다. 가장 임팩트 큰 단일 갭은 **멀티모달 모델 입력**(첨부 이미지/문서를 에이전트가 실제로 못 봄).

---

## 1. 현재 강점 (이미 있는 것 — 갭 아님)

갭을 맥락 안에서 보기 위한 요약. (상세 파일 앵커는 §5)

| 영역 | 있는 기능 |
|---|---|
| 메시지 액션 | copy / edit / regenerate / branch picker(`<n/m>`) / **피드백 👍👎** / 토큰·비용 팝오버 / 타임스탬프 |
| 컴포저 | 멀티라인+Enter 전송 / 파일 첨부(붙여넣기·드래그) / IME 안전 / opener questions / 컨텍스트 게이지 / queue(steer) 모드(Ctrl+Shift+Enter) |
| 스트리밍 UX | witty 로딩 / activity strip / stop / reconnect 배지 / SSE resume / 컨텍스트 게이지(80% amber·95% red) / TTFT·tok-s·비용 팝오버 / 자동 compaction 마커 |
| 도구/에이전트 | 도구 pill·그룹핑 / 검색 6종+출처집계 / 승인 카드(approve/edit/reject·멀티액션 인덱스) / ask_user(option list·question flow) / reasoning·phase timeline / 서브에이전트 카드 / deepagents state 패널 / 메모리 카드 / 코드·diff 프리뷰 / **Generative UI 데이터 카드** |
| 아티팩트 | 인라인 카드 + 우측 레일 프리뷰(PDF/DOCX/HWP/XLSX/PPTX/Mermaid/이미지/코드/표/data) + 라이브러리 |
| 대화 | 네비게이터(pin/rename/delete/검색/무한스크롤/⌘1-9) / 공유 링크(생성·복사·취소) / jump-to-message / 우측 레일 리사이즈 |
| 백엔드 런 | start/stream/stop·cancel / SSE resume(replay) / regenerate / branch·checkpoint fork / edit-and-rerun / 모델 fallback 체인 / 메모리(propose·save·proposal) / 서브에이전트 / 트리거(스케줄) / 토큰·비용·Langfuse·audit |

---

## 2. 갭 분석

각 항목: **설명 · 현재 상태(근거) · 가치 · 노력 · 비고(문서상 deferred 여부)**.

### 🥇 Tier 1 — 가장 큰 기능 갭 (에이전트 능력에 직접 영향)

#### G1. 멀티모달 모델 입력 — 첨부 이미지/문서를 에이전트가 실제로 못 봄 ⭐ 최우선
- **설명**: 첨부 **표시**(P1)는 됐으나, 붙인 이미지가 vision 블록으로, 문서가 텍스트 추출/RAG로 **모델 메시지에 전달되지 않는다**. 사용자는 이미지를 붙일 수 있는데 에이전트는 보지 못한다.
- **근거**: 백엔드 — "image_url 메시지 스키마는 지원하나 frontend 첨부→메시지 변환 없음"(`conversation_files.py`, `model_factory.py`). 이미지 생성은 빌더 전용(`builder_v3/image_gen.py`). 문서: `docs/design-docs/chat-attachments-dev-plan.md` §3 D2/D4가 "P2(다음 phase)"로 명시 deferred.
- **가치**: ↑↑ (요즘 에이전트의 사실상 필수). **노력**: 중 (표시 파이프라인이 이미 있어 "첨부→모델 메시지 변환" + provider capability 게이팅 위주).
- **비고**: 문서상 의도적 deferred(P2). provider별 multimodal 지원 분기 필요.

#### G2. 에러 후 재시도(Retry) — 낮은 노력·높은 가치
- **설명**: 도구/모델 실패 시 메시지 액션에 **retry 버튼이 없다**. regenerate는 성공한 턴 재생성용이라 에러 복구와 다르다. 런이 깨지면 사용자가 다시 입력해야 함.
- **근거**: 프론트 — 액션바에 `ActionBarPrimitive.Reload`(regenerate)만, 에러 후 retry 미배선. 백엔드 — `tool_retry` 미들웨어는 있으나 자동 도구 재시도용이고 사용자 트리거 런-레벨 retry는 별개.
- **가치**: 중~상(신뢰성 UX). **노력**: 소.

#### G3. 런 도중 개입/스티어링(mid-run inject) — 부분만 존재
- **설명**: 실행 중 런에 **컨텍스트/정정을 주입**하는 경로가 없다(중단만 가능). Ctrl+Shift+Enter "queue(steer)"는 *다음* 메시지 큐잉일 뿐 현재 런에 끼어들지 않는다.
- **근거**: 백엔드 — "mid-graph Command 전송 없음 / live prompt·context steering 없음"(`conversation_agent_protocol_commands.py`).
- **가치**: 중~상(에이전트 제어). **노력**: 중~대(런 라이프사이클 + 그래프 Command 경로).

#### G4. 구조화 출력 / tool_choice 강제 — 미노출
- **설명**: LangChain 모델은 JSON mode·structured output·`tool_choice="required"`를 지원하나 **에이전트 설정에 노출 안 됨**. 신뢰성 있는 구조화 응답이 필요한 에이전트에 제약.
- **근거**: 백엔드 — "structured output / JSON mode: 모델은 지원하나 config로 미노출"(`model_factory.py`).
- **가치**: 중(빌더·자동화 에이전트). **노력**: 중.

### 🥈 Tier 2 — 있으면 좋을 기능

#### G5. 대화 export / 다운로드 (markdown · JSON · PDF)
- 전혀 없음. 흔하고 실용적(공유·기록·디버깅). **가치 중 / 노력 소~중**. (공유 링크는 있으나 정적 파일 export는 별개.)

#### G6. 대화 내(in-conversation) 전문 검색
- 네비게이터(대화 **간**) 검색만 있고, **한 대화 안의 메시지/도구결과 전문 검색**은 없다. 긴 에이전트 런에서 과거 결과 찾기 불편. jump-to-message는 아티팩트에서만. **가치 중 / 노력 중**.

#### G7. 컴포저 모델 선택 / 턴별 모델 전환
- 모델이 런 시작 시 고정, 중간 전환 불가(에러 시 fallback 체인만). 싼↔강한 모델 토글 등 유용. **가치 중 / 노력 중**. (`model_factory`: model bound at run init.)

#### G8. 수동 compaction 버튼 (사용자 주도 "지금 압축")
- 자동 compaction만 있고 수동은 deferred(Optional). Claude-Code식. **가치 중 / 노력 소~중**. 문서: `dev-plan-context-compaction-marker.md` — "후속(Optional) 수동 compact 도구+버튼".

#### G9. 슬래시 커맨드 / @멘션
- assistant-ui 프리미티브는 있으나 **미사용**(i18n 키도 없음). `/clear`·`/model`·`/summarize`, 파일·에이전트·도구 멘션. **가치 중 / 노력 중**.

#### G10. 서브에이전트 스트리밍 가시성
- 부모가 **최종 결과만** 보고 내부 진행/사고는 안 보임("subagent progress visibility: 최종 결과만"). 관찰성↑. **가치 중 / 노력 중**.

#### G11. Generative UI 데이터 카드 확장
- 방금 추가한 DataTable/Chart/Stats/Terminal에 **CSV export · 컬럼 토글 · 차트 인터랙션(드릴다운/필터)**. 차트는 현재 plain SVG 정적 렌더. **가치 소~중 / 노력 소(증분)**.

#### G12. 음성 입력 / draft 자동저장
- dictation 훅(assistant-ui)만 있고 **UI 미배선**. 컴포저 텍스트 세션 간 미보존. **가치 소~중 / 노력 소**.

### 🥉 Tier 3 — 더 큰/운영성 (문서상 의도적 deferred 또는 ops)

#### G13. HITL 표준화 (드래프트, 미머지)
- "edit"가 일급 enum이 아니라 payload shape일 뿐 / 멀티액션이 카드 N개로 흩어짐(통합 "N건 대기" UX 없음) / 서브에이전트 interrupt 상속 버그 / 표준 interrupt가 UI에 완전 미배선. 문서: `docs/design-docs/hitl-ask-user-standardization-plan.md`(설계만). **가치 중 / 노력 대**.

#### G14. 비용 알림 / 사용량 쿼터
- 임계 경고·쿼터 강제 없음(추적·집계만, `daily_spend_*`). **가치 중(ops) / 노력 중**.

#### G15. 트리거 입력 파라미터화 / 출력 액션
- 스케줄 런이 **매번 같은 프롬프트**, 동적 입력·웹훅/알림 출력·dry-run 없음(`trigger_executor.py`). **가치 중 / 노력 중**.

#### G16. 기타 아키텍처
- 채팅 내 이미지 **생성**(현재 빌더 전용) / **LangSmith**(현재 Langfuse만) / **AG-UI 어댑터**(ADR-020, Phase P6 deferred) / 크로스-대화·RAG 메모리 자동 주입(현재 도구 수동).

---

## 3. 우선순위 + 추천

| 순위 | 항목 | 가치 | 노력 | 비고 |
|---|---|---|---|---|
| **1** | **G1 멀티모달 입력**(이미지 vision + 문서 추출) | ↑↑ | 중 | 표시 P1 위에 "첨부→모델 메시지" 연결 |
| **2** | **G2 에러 retry** | 중~상 | 소 | 빠른 신뢰성 win |
| **3** | **G5 대화 export**(md/json) | 중 | 소~중 | 실용·독립적 |
| 4 | G3 mid-run 스티어링 | 중~상 | 중~대 | 런 라이프사이클 손댐 |
| 5 | G4 구조화 출력 노출 | 중 | 중 | 빌더 신뢰성 |
| 6 | G6 대화 내 검색 / G8 수동 compaction / G9 슬래시 커맨드 | 중 | 중 | UX |

**추천: G1(멀티모달 입력)을 먼저.** 표시 파이프라인(P1)이 이미 깔려 있어 "첨부 → 모델 메시지 변환 + provider capability 게이팅"만 이으면 되고, 에이전트가 이미지를 보고 문서를 읽는 건 체감이 가장 크다. 빠른 win을 원하면 G2(retry)·G5(export)를 곁들이는 조합.

진행 방식 제안: 선택 항목을 (Generative UI처럼) **Phase 0 스파이크 → 설계 문서 → 단계별 구현 + 회귀 게이트**로.

---

## 4. 주의 / 한계

- 본 분석은 코드/문서 인벤토리 기반의 **발견**이며, 각 갭의 정확한 구현 난이도/리스크는 해당 항목 착수 시 스파이크로 확정해야 한다.
- 일부 항목(G1·G8·G13·G15·G16)은 문서에 **의도적 deferred**로 기록돼 있어 "누락"이 아니라 "후속 phase". 우선순위는 deferred 여부와 무관하게 가치·노력으로 매겼다.
- "강점"으로 분류한 기능도 세부 폴리시(예: 멀티액션 HITL 통합, 차트 인터랙션)는 미완일 수 있다.

## 5. 참조 (인벤토리 출처)

- 프론트 UI/컴포저: `frontend/src/components/chat/`(assistant-thread.tsx, composer, tool-ui/, right-rail/, navigator), `frontend/src/lib/chat/`.
- 백엔드 런 엔진: `backend/app/agent_runtime/`(langgraph_streaming, runtime_component_builder, model_factory, subagents, trigger_executor, middleware_registry), `backend/app/routers/conversation_agent_protocol_*`, `backend/app/services/`.
- 계획/문서: `docs/PRD.md`, `docs/design-docs/`(ADR-012/016/019/020, chat-attachments-dev-plan, chat-generative-ui-dev-plan, dev-plan-context-compaction[-marker], generic-tool-call-grouping-plan, hitl-ask-user-standardization-plan), `TASKS.md`.
</content>
