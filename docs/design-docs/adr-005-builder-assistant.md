# ADR-005: Builder/Assistant 아키텍처

| 항목 | 값 |
|------|-----|
| 상태 | 제안됨 |
| 날짜 | 2026-04-07 |
| 영향 범위 | agent_runtime, routers, services, schemas, models |

---

## 컨텍스트

현재 에이전트 생성/수정 시스템:
- **creation_agent.py**: 단일 GPT-4o, tools=[], 4단계 대화형 (~220줄)
- **fix_agent.py**: 단일 LLM, JSON changes 출력 → 코드 적용 (~150줄)

한계:
1. creation_agent가 도구/미들웨어 카탈로그를 시스템 프롬프트에 텍스트로 주입 → 할루시네이션 위험
2. fix_agent가 구조화된 변경이 아닌 free-form JSON → 파싱 실패 빈번
3. 두 에이전트 모두 도구를 사용하지 않아 DB와의 정합성 보장 불가
4. 시스템 프롬프트 개선이 전체 교체만 가능 (부분 수정 불가)

## 결정

**Builder** (오케스트레이터 + 4 서브에이전트)와 **Assistant** (도구 기반 단일 에이전트)로 교체한다.

### Builder: LangGraph StateGraph 파이프라인

7개 Phase를 순차 실행하는 StateGraph. Phase 2-5는 각각 전문 서브에이전트가 처리.

```
Phase 1 (init) → Phase 2 (intent) → Phase 3 (tools) → Phase 4 (middlewares)
                                                                    ↓
                                    Phase 7 (build) ← Phase 6 (config) ← Phase 5 (prompt)
```

**서브에이전트 격리 원칙:**
- 각 서브에이전트는 자신의 시스템 프롬프트 + 오케스트레이터가 보낸 description만 수신
- 오케스트레이터의 내부 state, 다른 서브에이전트의 출력에 직접 접근 불가
- 오케스트레이터가 이전 Phase 결과를 description에 포함시켜 전달

**서브에이전트 구현:**
- `create_deep_agent(model, tools=[], system_prompt=sub_prompt)` → `.ainvoke()`
- 도구 없이 순수 추론만 수행 (JSON 출력)
- 출력 파싱 실패 시 1회 재시도 후 기본값 사용

### Assistant: 도구 기반 에이전트

`create_deep_agent`로 생성. 32개 도구를 바인딩하여 DB를 직접 조작.

**핵심 원칙: VERIFY before MODIFY**
- 모든 수정 전에 get_agent_config 호출
- 도구가 DB를 직접 수정하므로 별도 confirm 단계 불필요
- 시스템 프롬프트는 edit_system_prompt (부분 수정) 우선, update_system_prompt (전체 교체) 보조

## 핵심 아키텍처 결정

### AD-1: Builder를 LangGraph StateGraph로 구현

**채택:** LangGraph StateGraph (노드 기반)
**기각:** 단일 에이전트 + 도구 / 단순 함수 체인

이유:
- Phase간 의존성을 그래프 엣지로 명시적 표현
- 각 Phase를 독립 노드로 격리 → 개별 테스트 용이
- 에러 시 특정 Phase부터 재시도 가능 (state 저장)
- SSE 스트리밍: 노드 실행 이벤트를 자연스럽게 SSE로 변환

### AD-2: 서브에이전트를 create_deep_agent(tools=[])로 생성

**채택:** create_deep_agent + ainvoke
**기각:** LLM 직접 호출 (model.ainvoke) / LangGraph 서브그래프

이유:
- create_deep_agent가 프로젝트 표준 에이전트 생성 방식
- 현재 creation_agent.py도 이미 build_agent(tools=[]) 사용
- 서브그래프는 불필요한 복잡성 (도구 없는 단순 추론)
- 향후 서브에이전트에 도구 추가 시 자연스러운 확장

### AD-3: Assistant 도구가 DB를 직접 수정

**채택:** 도구 내부에서 AsyncSession을 받아 DB 직접 수정
**기각:** 도구가 변경사항을 반환 → 서비스에서 일괄 적용

이유:
- VERIFY-MODIFY 루프가 도구 단위로 작동해야 함
- 도구가 즉시 DB에 반영 → get_agent_config로 즉시 확인 가능
- fix_agent의 JSON 파싱 실패 문제 근본 해결
- 트랜잭션은 도구 단위로 관리 (실패 시 해당 도구만 롤백)

### AD-4: Builder API는 2단계 (start + SSE → confirm)

**채택:** POST /start → SSE /stream → POST /confirm
**기각:** 단일 POST (동기) / WebSocket

이유:
- 7 Phase 실행이 수십 초 소요 → SSE로 진행 상황 실시간 보고
- confirm 단계에서 사용자가 draft_config 검토 후 수정/승인
- SSE는 기존 채팅 인프라(streaming.py) 재사용 가능
- WebSocket은 서버 리소스 부담 + 기존 인프라 불일치

### AD-5: BuilderSession DB 모델로 중간 상태 영속화

**채택:** 기존 AgentCreationSession 확장 → BuilderSession
**기각:** 메모리 전용 (세션 종료 시 유실) / 파일 시스템

이유:
- 서버 재시작/크래시 시에도 빌드 재개 가능
- Phase별 중간 결과(intent, tools, middlewares, prompt)를 JSON 컬럼에 저장
- 기존 agent_creation_sessions 테이블 마이그레이션으로 전환
- 파일 시스템은 기획서의 구상이나, PoC 단계에서는 DB가 단순

### AD-6: Assistant는 기존 대화(conversation) 인프라 재사용

**채택:** conversations 테이블 + checkpointer 기반 히스토리
**기각:** 별도 assistant_sessions 테이블

이유:
- Assistant 대화는 일반 채팅과 동일한 구조 (user/assistant 메시지)
- checkpointer가 히스토리 관리 → 추가 테이블 불필요
- conversation에 `type` 필드 추가로 구분 (chat / assistant)
- 기존 SSE 인프라 완전 재사용

### AD-7: 서브에이전트에 실제 카탈로그를 동적 주입

**채택:** 서브에이전트 호출 시 DB에서 카탈로그를 조회하여 description에 포함
**기각:** 시스템 프롬프트에 카탈로그 하드코딩

이유:
- 도구/미들웨어가 동적으로 추가/삭제되므로 하드코딩은 drift 위험
- 서브에이전트는 도구를 사용하지 않으므로 API 직접 호출 불가
- 오케스트레이터가 DB 조회 → description 템플릿에 주입
- 카탈로그 크기가 크지 않으므로 컨텍스트 비용 허용 범위

## 인터페이스 계약

### Builder API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/builder/start` | 빌드 세션 시작 → BuilderSessionResponse |
| GET | `/api/builder/{session_id}/stream` | SSE 스트리밍 (빌드 진행) |
| GET | `/api/builder/{session_id}` | 세션 상태 조회 |
| POST | `/api/builder/{session_id}/confirm` | 빌드 확인 → AgentResponse |

### Assistant API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/agents/{agent_id}/assistant/message` | SSE 메시지 (도구 실행 포함) |
| GET | `/api/agents/{agent_id}/assistant/config` | 현재 에이전트 설정 조회 |

### Builder SSE 이벤트

```typescript
// phase_progress: 단계 진행
{phase: number, status: "started" | "completed" | "failed", message?: string}

// sub_agent_start/end: 서브에이전트 실행
{phase: number, agent_name: string}
{phase: number, result_summary: string}

// build_preview: 빌드 프리뷰
{draft_config: DraftAgentConfig}

// error: 오류
{phase: number, message: string, recoverable: boolean}
```

## 결과

### 긍정적
- 서브에이전트 격리로 각 Phase 독립 테스트 가능
- Assistant 도구 기반으로 DB 정합성 보장
- 기존 인프라(SSE, checkpointer, executor) 최대 재사용
- 부분 수정(edit_system_prompt) 지원으로 프롬프트 품질 향상

### 부정적/위험
- Builder 7 Phase 실행 시간이 길어 사용자 경험 우려 → SSE 진행 보고로 완화
- 서브에이전트 4개의 LLM 호출 비용 → 저비용 모델(gpt-4o-mini) 고려
- Assistant 32개 도구의 스키마가 커서 토큰 소모 → 필요 시 도구 그룹핑

### 마이그레이션 전략
1. builder/, assistant/ 디렉토리 신규 생성
2. 새 routers/services 추가 (기존과 공존)
3. 프론트엔드에서 새 API로 전환
4. 기존 creation_agent.py, fix_agent.py 및 관련 코드 삭제
