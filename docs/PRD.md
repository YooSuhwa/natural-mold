## Moldy — PRD (Product Requirements Document)

> 작성일: 2026-04-01
> 버전: v0.1

### Changelog

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| v0.1 | 2026-04-01 | 초안 작성 |

---

### 1. 서비스 개요

- **한 줄 소개**: 대화로 만드는 AI 에이전트 빌더
- **핵심 문제**:
  - **As-Is**: 사내 직원들이 이메일 정리, 데이터 수집, 일정 관리 등 반복 업무를 수작업(복붙, 수동 정리)으로 처리. 자동화가 필요하면 개발팀에 요청해야 하며 대기 시간이 길거나 우선순위에서 밀림.
  - **To-Be**: 비개발 직원이 자연어로 원하는 업무를 설명하면 AI가 에이전트를 자동 구성하고, MCP 도구와 연결하여 반복 업무를 자동으로 처리. "원하는 틀(Mold)에 맞춰 무엇이든 만든다."
- **핵심 가치**: 노코드, 자연어 기반, 도구 연동
- **프로젝트 단계**: PoC (Proof of Concept) — 핵심 기능이 동작함을 증명하는 단계

---

### 2. 사용자 정의

| 사용자 유형 | 설명 | 주요 행동 |
|------------|------|----------|
| 사내 직원 | 모든 사내 구성원 (단일 권한) | 에이전트 생성·수정·삭제, 채팅, 도구 연결·등록, 템플릿 사용 |

**권한 정책 (PoC)**: 모든 사용자가 동일한 권한. 에이전트는 생성한 사용자 본인만 접근 가능(개인 소유). 인증은 PoC에서 간이 방식(이메일+비밀번호) 또는 미적용. [미정 — 확인 필요: PoC 인증 방식]

---

### 3. 기능 요구사항

#### 3.1 기능 목록

| 우선순위 | 기능명 | 한 줄 설명 | 사용자 유형 |
|---------|--------|-----------|------------|
| P1 | 대화형 에이전트 생성 | 자연어로 목적을 설명하면 AI가 질문하며 에이전트를 자동 구성 | 사내 직원 |
| P1 | MCP 도구 연동 | 외부 MCP 서버 등록 + UI에서 간단한 도구 정의·등록 | 사내 직원 |
| P1 | 에이전트 템플릿 | 사전 제작된 템플릿으로 에이전트를 빠르게 생성 | 사내 직원 |
| P1 | 에이전트 채팅 | 생성된 에이전트와 웹 채팅 UI에서 대화하며 업무 수행 | 사내 직원 |
| P1 | 에이전트 관리 | 에이전트 목록 조회, 설정 수정, 삭제 | 사내 직원 |
| P1 | LLM 모델 선택 | 에이전트별로 사용할 LLM 모델을 선택 (Model Neutral) | 사내 직원 |
| P1 | 토큰 사용량 추적 | 에이전트별·대화별 토큰 소비량과 비용 표시 | 사내 직원 |
| P2 | 트리거 | 이벤트 또는 스케줄 기반 자동 실행 | 사내 직원 |
| P2 | 스킬 시스템 | 자주 쓰는 작업 패턴을 스킬로 저장·재사용 | 사내 직원 |
| P2 | 에이전트 메모리 | 이전 대화 내용을 기억하고 자기 학습 | 사내 직원 |
| P3 | 에이전트 공유 | 에이전트를 다른 직원과 공유 | 사내 직원 |
| P3 | RAG | 문서 업로드 후 문서 기반 검색·답변 | 사내 직원 |

- **P1 (PoC 필수)**: 이것 없이는 서비스 동작 불가. **7개**
- **P2 (PoC 직후)**: PoC 검증 후 바로 추가
- **P3 (검증 후)**: PoC 성공 시 추가 고려
- **Not Doing**: 마켓플레이스, 에이전트 평가, 커뮤니티 공유, 멀티에이전트 팀, Human-in-the-loop, 비주얼 워크플로우 빌더, 모바일 앱
- **장기 로드맵**: 멀티에이전트 팀 구성, 에이전트 품질 평가 시스템, 마켓플레이스(에이전트·도구·스킬 공유), 커뮤니티 공유·외부 링크 실행, Human-in-the-loop 승인 워크플로우

#### 3.2 기능 상세

**[대화형 에이전트 생성]**
- 설명: 사용자가 자연어로 원하는 업무를 설명하면, AI가 후속 질문으로 목적·도구·행동 규칙을 구체화하고 시스템 프롬프트(Instructions)를 자동 생성하여 에이전트를 완성하는 기능
- 동작 방식:
  1. 사용자가 "이메일을 분류해주는 에이전트를 만들고 싶어"와 같이 목적을 입력
  2. AI가 후속 질문으로 세부 사항 수집 (예: "어떤 기준으로 분류할까요?")
  3. 충분한 정보가 모이면 AI가 에이전트 이름, 설명, 시스템 프롬프트, 추천 도구, 추천 모델을 자동 생성
  4. 사용자가 생성된 구성을 확인·수정 후 저장
- 입력: 자연어 텍스트 (에이전트의 목적, AI 질문에 대한 답변)
- 출력: 에이전트 구성 (이름, 설명, 시스템 프롬프트, 추천 도구 목록, 추천 모델)
- 예외 상황:
  - 사용자 설명이 너무 모호한 경우 → AI가 구체적 예시를 제시하며 재질문
  - 생성 세션 중간에 이탈 → 임시 저장하여 이어서 진행 가능
  - 추천 도구가 미연결 → "이 도구를 연결하면 더 잘 동작합니다" 안내

**[MCP 도구 연동]**
- 설명: 에이전트가 외부 서비스와 상호작용할 수 있도록, (A) 외부 MCP 서버를 URL로 등록하거나 (B) Moldy UI에서 간단한 도구(이름, API URL, 파라미터)를 직접 정의하여 에이전트에 연결하는 기능
- 동작 방식:
  - (A) MCP 서버 등록: 서버 URL + 인증 정보 입력 → 연결 테스트 → 도구 자동 발견(discovery) → 등록
  - (B) 간단 도구 등록: 도구 이름, 설명, API URL, HTTP 메서드, 파라미터 스키마, 인증 방식을 UI 폼으로 입력 → 등록
  - 에이전트 설정에서 등록된 도구 중 필요한 것을 선택하여 연결
- 입력: (A) MCP 서버 URL, 인증 방식, 인증 정보 / (B) 도구 정의 정보
- 출력: 도구 목록 (이름, 설명, 파라미터 스키마), 도구 호출 결과
- 예외 상황:
  - MCP 서버 연결 실패 → "서버에 연결할 수 없습니다. URL과 인증 정보를 확인해주세요"
  - 도구 호출 타임아웃 → 30초 후 에이전트가 실패 안내
  - 인증 만료 → "도구 인증이 만료되었습니다. 재인증이 필요합니다"

**[에이전트 템플릿]**
- 설명: 사전 제작된 에이전트 구성을 템플릿으로 제공하여, 빠르게 에이전트를 생성할 수 있는 기능
- 동작 방식:
  1. 템플릿 목록에서 카테고리별 탐색
  2. 템플릿 선택 시 미리보기(이름, 설명, 필요 도구, 사용 예시) 확인
  3. "이 템플릿으로 생성" 클릭 → 에이전트 생성 + 필요 도구 연결 안내
  4. 생성된 에이전트의 프롬프트·설정을 자유롭게 수정 가능
- 입력: 템플릿 선택, (선택적) 수정
- 출력: 생성된 에이전트
- 예외 상황:
  - 필요 도구 미연결 → "이 에이전트는 [Gmail] 도구가 필요합니다. 연결하시겠습니까?"
  - 템플릿 0개 → "아직 등록된 템플릿이 없습니다. 대화형으로 만들어보세요"

**[에이전트 채팅]**
- 설명: 생성된 에이전트와 웹 채팅 UI에서 대화하며 업무를 수행
- 동작 방식:
  1. 에이전트 선택 → 채팅 화면 진입
  2. 메시지 입력 → 에이전트 응답 (스트리밍)
  3. 도구 호출 시 호출 과정과 결과를 UI에 표시
  4. 대화는 스레드 단위로 관리
- 입력: 사용자 메시지
- 출력: 에이전트 응답 (텍스트 + 도구 호출 결과)
- 예외 상황:
  - LLM API 실패 → "응답을 생성할 수 없습니다. 잠시 후 다시 시도해주세요"
  - 도구 호출 실패 → 에이전트가 실패를 안내하고 대안 제시
  - 빈 대화 → "첫 메시지를 보내보세요!"

**[에이전트 관리]**
- 설명: 에이전트 목록 조회, 설정 수정, 삭제
- 동작 방식:
  1. 대시보드에서 카드 형태로 조회
  2. 카드 클릭 → 채팅, 설정 아이콘 → 설정 화면
  3. 설정에서 이름, 설명, 프롬프트, 도구, 모델 수정
  4. 삭제 시 확인 다이얼로그 후 삭제
- 입력: 수정할 에이전트 정보
- 출력: 업데이트된 에이전트
- 예외 상황:
  - 에이전트 0개 → "아직 에이전트가 없습니다. 새로 만들어보세요!" + CTA
  - 삭제 후 되돌리기 → PoC 미지원

**[LLM 모델 선택]**
- 설명: 에이전트별 LLM 모델 선택. 멀티 프로바이더 + 커스텀 모델 지원
- 동작 방식:
  1. 에이전트 설정에서 모델 선택 드롭다운
  2. 사전 등록 모델 선택 또는 커스텀 모델 추가
  3. 커스텀: 프로바이더, 모델 ID, Base URL, API Key 입력
  4. 변경 시 이후 새 메시지부터 적용
- 입력: 모델 선택 또는 커스텀 모델 정보
- 출력: 에이전트에 모델 할당
- 예외 상황:
  - API Key 무효 → "API Key를 확인해주세요"
  - 모델 0개 → 시스템 디폴트 적용 [미정 — 확인 필요: 기본 모델]

**[토큰 사용량 추적]**
- 설명: 에이전트별·대화별 토큰 소비량과 추정 비용을 기록·표시
- 동작 방식:
  1. LLM API 호출 시 응답의 토큰 수를 DB에 저장
  2. 대시보드에서 에이전트별/대화별 사용량 조회
  3. 모델 단가 기반 추정 비용 표시
- 입력: (자동 수집)
- 출력: 토큰 수, 추정 비용
- 예외 상황:
  - 토큰 정보 미반환 → "사용량 정보 없음"
  - 모델 단가 미설정 → 토큰 수만 표시

---

### 4. 사용자 시나리오

#### 시나리오 1: 대화형으로 이메일 분류 에이전트 생성

사용자: 사내 직원 / 목적: 이메일 자동 분류 에이전트 생성

1. 대시보드에서 [+ 새 에이전트] 클릭
2. "대화로 만들기" 선택
3. "매일 받는 이메일을 중요도별로 분류해주는 에이전트를 만들고 싶어" 입력
4. AI가 "어떤 기준으로 중요도를 나눌까요?" 질문
5. "발신자가 팀장 이상이면 긴급, 외부 메일은 보통, 뉴스레터는 낮음" 답변
6. AI가 에이전트 구성 생성 → 사용자 확인 후 [에이전트 생성] 클릭
7. Gmail 도구 연결 안내 → OAuth 인증
8. 에이전트 생성 완료, 채팅 화면으로 이동

검증 기준:
- Given 유효한 목적 설명, When 대화형 생성 완료, Then 에이전트 생성·목록 표시
- Given 모호한 설명, When 입력, Then AI가 재질문
- Given 생성 중 이탈, When 재접속, Then 이전 세션 이어서 진행

#### 시나리오 2: 템플릿으로 일정 브리핑 에이전트 생성

사용자: 사내 직원 / 목적: 오늘 일정 요약 에이전트 빠르게 생성

1. [+ 새 에이전트] → "템플릿으로 만들기"
2. "Daily Calendar Brief" 템플릿 선택
3. 미리보기 확인 → [이 템플릿으로 생성]
4. Google Calendar 연결 → 에이전트 생성
5. 채팅에서 "오늘 일정 알려줘"로 테스트

검증 기준:
- Given 템플릿 선택, When 생성, Then 템플릿 구성 적용된 에이전트 생성
- Given 필요 도구 미연결, When 생성, Then 도구 연결 안내
- Given 생성된 에이전트, When 프롬프트 수정, Then 변경 저장·반영

#### 시나리오 3: 에이전트와 채팅하여 업무 수행

사용자: 사내 직원 / 목적: 에이전트에게 업무 지시

1. "이메일 분류 도우미" 카드 클릭
2. "오늘 받은 이메일을 분류해줘" 입력
3. 에이전트 Gmail 도구 호출 (상태 표시)
4. 분류 결과 응답 → "긴급 이메일 요약해줘" 추가 요청
5. 에이전트가 요약 제공

검증 기준:
- Given 도구 연결 정상, When 메시지 전송, Then 도구 호출·결과 응답
- Given 도구 타임아웃, When 30초 초과, Then 실패 안내
- Given LLM 장애, When 메시지 전송, Then 에러 표시

#### 시나리오 4: 커스텀 도구를 UI에서 등록

사용자: 사내 직원 / 목적: 날씨 API를 도구로 등록

1. 도구 관리 → [+ 도구 추가] → "직접 정의" 탭
2. 이름("날씨 조회"), API URL, GET, 파라미터(city: string) 입력
3. API Key 인증 설정 → [등록]
4. 에이전트 설정에서 "날씨 조회" 도구 연결
5. 채팅에서 "서울 날씨 알려줘" → 도구 호출 → 결과 응답

검증 기준:
- Given 유효한 도구 정의, When 등록, Then 도구 목록에 표시·에이전트에서 선택 가능
- Given 필수 필드 누락, When 등록 시도, Then 유효성 에러
- Given 등록된 도구, When 에이전트 호출, Then API 호출·결과 전달

#### 예외 시나리오: MCP 서버 등록 실패

1. 도구 관리 → [+ 도구 추가] → "MCP 서버" 탭
2. 잘못된 URL → [연결 테스트] → 에러
3. URL 수정 → 재시도 → 성공, 도구 목록 표시
4. [등록] 완료

검증 기준:
- Given 유효한 URL, When 연결 테스트, Then 도구 자동 발견·표시
- Given 잘못된 URL, When 테스트, Then 연결 실패 에러
- Given 인증 필요 서버, When Key 누락, Then 인증 에러

---

### 5. 데이터 모델

#### 5.1 엔티티 관계

```
[User] 1──N [Agent] 1──N [Conversation] 1──N [Message]
                |                                 |
                N──N [Tool] (via AgentTool)        1──1 [TokenUsage]
                |
                N──1 [Model]

[Template] ──생성──> [Agent]
[MCPServer] 1──N [Tool]
[Tool] ── type: mcp | custom
```

#### 5.2 주요 테이블

**users**

| 필드명 | 타입 | 제약조건 | 설명 |
|--------|------|---------|------|
| id | UUID | PK | 사용자 고유 ID |
| email | VARCHAR(255) | UNIQUE, NOT NULL | 이메일 |
| name | VARCHAR(100) | NOT NULL | 사용자 이름 |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | 생성일시 |

**agents**

| 필드명 | 타입 | 제약조건 | 설명 |
|--------|------|---------|------|
| id | UUID | PK | 에이전트 고유 ID |
| user_id | UUID | FK(users.id), NOT NULL | 소유자 |
| name | VARCHAR(100) | NOT NULL | 에이전트 이름 |
| description | TEXT | | 에이전트 설명 |
| system_prompt | TEXT | NOT NULL | 시스템 프롬프트 |
| model_id | UUID | FK(models.id), NOT NULL | 사용 모델 |
| status | VARCHAR(20) | NOT NULL, DEFAULT 'active' | active / archived |
| template_id | UUID | FK(templates.id), NULLABLE | 원본 템플릿 |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | 생성일시 |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW | 수정일시 |

**models**

| 필드명 | 타입 | 제약조건 | 설명 |
|--------|------|---------|------|
| id | UUID | PK | 모델 고유 ID |
| provider | VARCHAR(50) | NOT NULL | openai / anthropic / google / custom |
| model_name | VARCHAR(100) | NOT NULL | 모델 ID |
| display_name | VARCHAR(100) | NOT NULL | 표시 이름 |
| base_url | VARCHAR(500) | | API Base URL (커스텀용) |
| api_key_encrypted | TEXT | | 암호화된 API Key |
| is_default | BOOLEAN | DEFAULT FALSE | 기본 모델 여부 |
| cost_per_input_token | DECIMAL(12,8) | | 입력 토큰당 단가 (USD) |
| cost_per_output_token | DECIMAL(12,8) | | 출력 토큰당 단가 (USD) |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | 생성일시 |

**mcp_servers**

| 필드명 | 타입 | 제약조건 | 설명 |
|--------|------|---------|------|
| id | UUID | PK | MCP 서버 고유 ID |
| name | VARCHAR(100) | NOT NULL | 서버 이름 |
| url | VARCHAR(500) | NOT NULL | MCP 서버 URL |
| auth_type | VARCHAR(20) | NOT NULL | none / api_key / oauth |
| auth_config | JSONB | | 인증 설정 (암호화) |
| status | VARCHAR(20) | NOT NULL, DEFAULT 'active' | active / inactive / error |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | 생성일시 |

**tools**

| 필드명 | 타입 | 제약조건 | 설명 |
|--------|------|---------|------|
| id | UUID | PK | 도구 고유 ID |
| type | VARCHAR(20) | NOT NULL | mcp / custom |
| mcp_server_id | UUID | FK, NULLABLE | MCP 서버 (type=mcp) |
| name | VARCHAR(100) | NOT NULL | 도구 이름 |
| description | TEXT | | 도구 설명 |
| parameters_schema | JSONB | | 파라미터 JSON Schema |
| api_url | VARCHAR(500) | | API URL (type=custom) |
| http_method | VARCHAR(10) | | GET / POST / PUT / DELETE |
| auth_type | VARCHAR(20) | | 인증 방식 (type=custom) |
| auth_config | JSONB | | 인증 설정 (암호화) |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | 생성일시 |

**agent_tools**

| 필드명 | 타입 | 제약조건 | 설명 |
|--------|------|---------|------|
| agent_id | UUID | FK(agents.id), PK | 에이전트 |
| tool_id | UUID | FK(tools.id), PK | 도구 |

**templates**

| 필드명 | 타입 | 제약조건 | 설명 |
|--------|------|---------|------|
| id | UUID | PK | 템플릿 고유 ID |
| name | VARCHAR(100) | NOT NULL | 템플릿 이름 |
| description | TEXT | | 설명 |
| category | VARCHAR(50) | NOT NULL | 카테고리 |
| system_prompt | TEXT | NOT NULL | 기본 시스템 프롬프트 |
| recommended_tools | JSONB | | 추천 도구 이름 목록 |
| recommended_model_id | UUID | FK, NULLABLE | 추천 모델 |
| usage_example | TEXT | | 사용 예시 |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | 생성일시 |

**conversations**

| 필드명 | 타입 | 제약조건 | 설명 |
|--------|------|---------|------|
| id | UUID | PK | 대화 고유 ID |
| agent_id | UUID | FK(agents.id), NOT NULL | 에이전트 |
| title | VARCHAR(200) | | 대화 제목 (자동 생성) |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | 생성일시 |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW | 최종 메시지 시각 |

**messages**

| 필드명 | 타입 | 제약조건 | 설명 |
|--------|------|---------|------|
| id | UUID | PK | 메시지 고유 ID |
| conversation_id | UUID | FK, NOT NULL | 대화 |
| role | VARCHAR(20) | NOT NULL | user / assistant / tool |
| content | TEXT | NOT NULL | 메시지 내용 |
| tool_calls | JSONB | NULLABLE | 도구 호출 정보 |
| tool_call_id | VARCHAR(100) | NULLABLE | 도구 호출 ID (role=tool) |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | 생성일시 |

**token_usages**

| 필드명 | 타입 | 제약조건 | 설명 |
|--------|------|---------|------|
| id | UUID | PK | 고유 ID |
| message_id | UUID | FK(messages.id), NOT NULL | 해당 메시지 |
| agent_id | UUID | FK(agents.id), NOT NULL | 에이전트 (집계용) |
| model_name | VARCHAR(100) | NOT NULL | 사용된 모델 |
| prompt_tokens | INTEGER | NOT NULL | 입력 토큰 수 |
| completion_tokens | INTEGER | NOT NULL | 출력 토큰 수 |
| total_tokens | INTEGER | NOT NULL | 총 토큰 수 |
| estimated_cost | DECIMAL(10,6) | | 추정 비용 (USD) |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | 생성일시 |

**agent_creation_sessions**

| 필드명 | 타입 | 제약조건 | 설명 |
|--------|------|---------|------|
| id | UUID | PK | 세션 고유 ID |
| user_id | UUID | FK(users.id), NOT NULL | 사용자 |
| conversation_history | JSONB | NOT NULL | 생성 대화 이력 |
| draft_config | JSONB | | 중간 에이전트 구성 |
| status | VARCHAR(20) | DEFAULT 'in_progress' | in_progress / completed / abandoned |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | 생성일시 |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW | 수정일시 |

---

### 6. API 설계

#### 6.1 엔드포인트 목록

**에이전트 CRUD**

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | /api/agents | 내 에이전트 목록 | 필요 |
| POST | /api/agents | 에이전트 생성 | 필요 |
| GET | /api/agents/:id | 에이전트 상세 | 필요 |
| PUT | /api/agents/:id | 에이전트 수정 | 필요 |
| DELETE | /api/agents/:id | 에이전트 삭제 | 필요 |

**대화형 생성**

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| POST | /api/agents/create-session | 생성 세션 시작 | 필요 |
| POST | /api/agents/create-session/:id/message | 생성 대화 메시지 | 필요 |
| GET | /api/agents/create-session/:id | 세션 상태 조회 | 필요 |
| POST | /api/agents/create-session/:id/confirm | 생성 확정 | 필요 |

**채팅**

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | /api/agents/:id/conversations | 대화 목록 | 필요 |
| POST | /api/agents/:id/conversations | 새 대화 생성 | 필요 |
| GET | /api/conversations/:id/messages | 메시지 목록 | 필요 |
| POST | /api/conversations/:id/messages | 메시지 전송 (SSE) | 필요 |

**도구**

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | /api/tools | 도구 목록 | 필요 |
| POST | /api/tools/mcp-server | MCP 서버 등록 | 필요 |
| POST | /api/tools/mcp-server/:id/test | 연결 테스트 | 필요 |
| POST | /api/tools/custom | 커스텀 도구 등록 | 필요 |
| DELETE | /api/tools/:id | 도구 삭제 | 필요 |

**모델·템플릿·사용량**

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | /api/models | 모델 목록 | 필요 |
| POST | /api/models | 커스텀 모델 등록 | 필요 |
| DELETE | /api/models/:id | 모델 삭제 | 필요 |
| GET | /api/templates | 템플릿 목록 | 필요 |
| GET | /api/templates/:id | 템플릿 상세 | 필요 |
| GET | /api/agents/:id/usage | 에이전트 토큰 사용량 | 필요 |
| GET | /api/usage/summary | 전체 사용량 요약 | 필요 |

#### 6.2 주요 요청/응답 예시

```json
// POST /api/agents — 에이전트 생성
// Request
{
  "name": "이메일 분류 도우미",
  "description": "이메일을 중요도별로 분류합니다",
  "system_prompt": "당신은 이메일 분류 전문가입니다...",
  "model_id": "model_abc123",
  "tool_ids": ["tool_gmail_001"]
}
// Response 201
{
  "id": "agent_xyz789",
  "name": "이메일 분류 도우미",
  "model": { "id": "model_abc123", "display_name": "GPT-4o" },
  "tools": [{ "id": "tool_gmail_001", "name": "Gmail" }],
  "status": "active",
  "created_at": "2026-04-01T10:00:00Z"
}
// Response 400
{ "error": "VALIDATION_ERROR", "message": "name은 필수입니다" }
```

```json
// POST /api/tools/custom — 커스텀 도구 등록
// Request
{
  "name": "날씨 조회",
  "description": "도시의 현재 날씨를 조회합니다",
  "api_url": "https://internal-api.company.com/weather",
  "http_method": "GET",
  "parameters_schema": {
    "type": "object",
    "properties": { "city": { "type": "string", "description": "도시 이름" } },
    "required": ["city"]
  },
  "auth_type": "api_key",
  "auth_config": { "header_name": "X-API-Key", "api_key": "sk-..." }
}
// Response 201
{
  "id": "tool_custom_001",
  "type": "custom",
  "name": "날씨 조회",
  "created_at": "2026-04-01T11:00:00Z"
}
```

```json
// POST /api/conversations/:id/messages — 메시지 전송 (SSE 응답)
// Request
{ "content": "오늘 받은 이메일을 분류해줘" }
// Response 200 (SSE 스트리밍)
// event: message_start
// data: {"id":"msg_001","role":"assistant"}
// event: content_delta
// data: {"delta":"이메일을 확인하겠습니다. "}
// event: tool_call_start
// data: {"tool_name":"gmail_list","parameters":{"query":"after:today"}}
// event: tool_call_result
// data: {"tool_name":"gmail_list","result":{"count":20}}
// event: content_delta
// data: {"delta":"긴급 3건, 보통 12건, 낮음 5건입니다."}
// event: message_end
// data: {"usage":{"prompt_tokens":520,"completion_tokens":180}}
```

```json
// GET /api/agents/:id/usage — 토큰 사용량
// Response 200
{
  "agent_id": "agent_xyz789",
  "period": "2026-04",
  "total_tokens": 23500,
  "estimated_cost_usd": 0.47,
  "conversations": [
    { "conversation_id": "conv_001", "title": "이메일 분류", "total_tokens": 3200 }
  ]
}
```

---

> 화면 설계는 `docs/PRD-screens.md` 참고

---

### 8. 기술 스택

| 영역 | 기술 | 비고 |
|------|------|------|
| Frontend | Next.js | 전역 CLAUDE.md 기본 스택 |
| Backend | Python FastAPI | 전역 CLAUDE.md 기본 스택 |
| DB | PostgreSQL | JSONB 지원 필요 |
| LLM 연동 | LiteLLM 또는 자체 추상화 레이어 | Model Neutral 통합 인터페이스 |
| MCP | MCP Python SDK | 자체 구축 + 외부 연결 |
| 실시간 통신 | SSE (Server-Sent Events) | 채팅 스트리밍 |
| 배포 | 로컬 (Docker Compose) | 추후 클라우드 확장 가능 |
| 인증 | [미정 — 확인 필요] | PoC: 간이 인증 또는 미적용 |

---

### 9. 비기능 요구사항

| 항목 | 기준 |
|------|------|
| 응답 시간 | 페이지 로드 2초 이내, 채팅 첫 토큰 3초 이내 |
| 보안 | API Key 암호화 저장, HTTPS (배포 시) |
| 접근성 | 키보드 네비게이션, 기본 시맨틱 HTML |
| 동시 접속 | PoC 기준 10명 |
| 데이터 백업 | PostgreSQL 일일 백업 (로컬) |

---

### 10. 제약사항 및 가정

**제약사항**: PoC 단계로 프로덕션 수준 안정성·확장성은 목표가 아님. LLM API 비용은 각 프로바이더 Key로 직접 부담. MCP 서버 개발·테스트는 Moldy 외부에서 수행. 온프레미스 배포 전제로 외부 클라우드 의존도 최소화.

**가정**: 사내 네트워크에서 접근. LLM API에 네트워크 접근 가능. 사용자는 기본 AI 채팅 경험 보유. MCP 서버는 별도 프로세스로 HTTP 통신.

---

### 11. 용어 정의

| 용어 | 설명 |
|------|------|
| 에이전트 (Agent) | 시스템 프롬프트와 도구를 갖춘, 사용자 메시지에 응답하며 업무를 수행하는 AI 단위 |
| 시스템 프롬프트 (Instructions) | 에이전트의 행동 규칙과 역할을 정의하는 텍스트 |
| MCP (Model Context Protocol) | LLM이 외부 서비스·데이터와 상호작용하기 위한 표준 프로토콜 |
| 도구 (Tool) | 에이전트가 호출할 수 있는 외부 기능 단위 |
| 템플릿 (Template) | 사전 정의된 에이전트 구성. 복제하여 빠르게 에이전트 생성 |
| 스레드 (Conversation) | 에이전트와의 대화 단위 |
| 토큰 (Token) | LLM이 텍스트를 처리하는 최소 단위. 비용 산정 기준 |
| 스킬 (Skill) | 자주 쓰는 작업 패턴을 재사용 가능하게 저장한 것 (P2) |
| 트리거 (Trigger) | 에이전트 자동 실행 조건 — 이벤트/시간 기반 (P2) |
| RAG | 문서를 검색하여 LLM 응답 근거로 활용하는 기법 (P3) |
| PoC | 핵심 기능이 동작함을 증명하는 프로토타입 단계 |
| SSE | 서버→클라이언트 실시간 단방향 데이터 전송 프로토콜 |
