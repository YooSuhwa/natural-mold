# Moldy — TASKS

## Phase 0: 프로젝트 부트스트랩

- [x] git init + .gitignore
- [x] .mise.toml (Python 3.12, Node 22)
- [x] docker-compose.yml (PostgreSQL)
- [x] backend/ 스캐폴딩 (pyproject.toml, app factory, config, database)
- [x] frontend/ 스캐폴딩 (pnpm create next-app, TailwindCSS v4, shadcn/ui)

## Phase 1: Backend — DB + 기본 CRUD

- [x] SQLAlchemy 모델 11개 테이블
- [x] Alembic 초기 마이그레이션
- [x] Mock user dependency + Pydantic 스키마
- [x] 시드 데이터 (기본 모델, 템플릿 4개)
- [x] Agent CRUD API (5 endpoints) + 테스트
- [x] Template API (2 endpoints) + 테스트
- [x] Model API (3 endpoints) + 테스트
- [x] Tool API (5 endpoints) + 테스트

## Phase 2: Backend — 채팅 엔진 (LangChain/LangGraph)

- [x] agent_runtime/model_factory.py
- [x] agent_runtime/tool_factory.py
- [x] agent_runtime/executor.py (create_agent + astream)
- [x] agent_runtime/streaming.py (LangGraph → SSE)
- [x] agent_runtime/token_tracker.py
- [x] Conversation API (4 endpoints) + LangGraph PostgresSaver
- [ ] 채팅 엔진 통합 테스트

## Phase 3: Backend — MCP + 대화형 생성 + 사용량

- [x] agent_runtime/mcp_client.py + MCP 연결 테스트 endpoint
- [x] agent_runtime/creation_agent.py (대화형 생성 메타 에이전트)
- [x] Agent creation session API (4 endpoints) + 테스트
- [x] Usage API (2 endpoints) + 테스트

## Phase 4: Frontend — 레이아웃 + 대시보드 + CRUD 화면

- [x] TypeScript 타입 + API 클라이언트 + TanStack Query hooks
- [x] SSE 스트리밍 클라이언트 + Jotai stores
- [x] 공통 레이아웃 (사이드바, 헤더)
- [x] 대시보드 (에이전트 카드 그리드 + 사용량 요약)
- [x] 에이전트 설정 페이지
- [x] 도구 관리 페이지 (MCP/Custom 등록 모달)
- [x] 모델 관리 페이지
- [x] 사용량 대시보드

## Phase 5: Frontend — 채팅 + 에이전트 생성

- [x] 에이전트 채팅 페이지
- [x] 대화형 에이전트 생성 페이지
- [x] 템플릿 선택 페이지

## Phase 7A: Backend — 프리빌트 도구 카탈로그

- [x] Tool 모델 스키마 변경 (user_id nullable, is_system 플래그) + 마이그레이션
- [x] 빌트인 도구 구현 (Web Search, Web Scraper, Current DateTime) in tool_factory.py
- [x] 시드 데이터 (default_tools.py) + main.py 시딩
- [x] 서비스 레이어 수정 (list_tools 시스템 도구 포함, 삭제 방지)
- [x] Executor builtin 타입 처리
- [x] 스키마/타입 업데이트 (is_system)
- [x] 테스트

## Phase 7B: Backend — 에이전트 생성 시 도구 자동 연결

- [x] confirm_creation()에서 recommended_tool_names 이름 매칭 → 자동 링크
- [x] send_message()에서 시스템 도구 컨텍스트 제공
- [x] 템플릿 생성 시 도구 자동 연결
- [x] 테스트

## Phase 7C: Backend — 트리거/스케줄러 시스템

- [x] AgentTrigger 모델 + 마이그레이션
- [x] 트리거 스키마 (Pydantic)
- [x] 트리거 서비스 (CRUD)
- [x] 트리거 실행기 (trigger_executor.py)
- [x] APScheduler 통합 (scheduler.py + main.py)
- [x] 트리거 API (4 endpoints)
- [x] 테스트

## Phase 8: Frontend — 도구/트리거 UI

- [x] TypeScript 타입 + API 클라이언트 + hooks (triggers)
- [x] 도구 관리 페이지 — 시스템 도구 표시
- [x] 에이전트 설정 — 트리거 설정 섹션
- [x] 대시보드 — 에이전트 카드에 도구명 표시

## Phase 9: Backend — 빌트인 도구 확장 (네이버/Google)

- [x] config.py — 네이버/Google API 키 설정 추가
- [x] naver_tools.py — 네이버 검색 API 제네릭 빌더 (Blog, News, Image, Shopping, Local)
- [x] google_tools.py — Google Custom Search API 빌더 (Web, News, Image)
- [x] tool_factory.py — 8개 새 도구 등록 + auth_config 지원
- [x] executor.py — auth_config 전달
- [x] default_tools.py — 8개 시드 데이터 추가
- [x] default_templates.py — 3개 템플릿 추가 (뉴스 모니터, 쇼핑 비교, 맛집 탐색)
- [x] main.py — 시드 로직 upsert 방식으로 개선
- [x] 테스트 (15개 통과)

## Phase 10A: Backend — Google Chat Webhook 도구 (P1)

- [x] google_workspace_tools.py — Google Chat Webhook send 구현
- [x] config.py — google_chat_webhook_url 설정 추가
- [x] tool_factory.py — prebuilt 레지스트리 등록
- [x] default_tools.py — 시드 데이터 추가
- [x] 테스트 (20개 통과)

## Phase 10B: Backend — Google OAuth2 인프라 + Gmail 도구 (P2)

- [x] google-auth, google-api-python-client 의존성 추가
- [x] config.py — OAuth2 설정 (client_id, client_secret, refresh_token)
- [x] google_auth.py — OAuth2 토큰 관리 헬퍼 (자동 갱신)
- [x] scripts/google_oauth_setup.py — 1회성 refresh_token 발급 스크립트
- [x] google_workspace_tools.py — Gmail Read (목록 조회 + 본문 읽기)
- [x] google_workspace_tools.py — Gmail Send (이메일 전송)
- [x] tool_factory.py — Gmail 도구 2개 등록
- [x] default_tools.py — Gmail 시드 데이터 추가
- [x] 테스트 (25개 통과)

## Phase 10C: Backend — Google Calendar 도구 (P3)

- [x] google_workspace_tools.py — Calendar List Events (일정 조회)
- [x] google_workspace_tools.py — Calendar Create Event (일정 생성)
- [x] google_workspace_tools.py — Calendar Update Event (일정 수정)
- [x] tool_factory.py — Calendar 도구 3개 등록
- [x] default_tools.py — Calendar 시드 데이터 추가
- [x] default_templates.py — "이메일 어시스턴트", "Daily Brief" 템플릿 업데이트
- [x] 테스트 (29개 통과)

## Phase 10D: Backend — 에이전트별 도구 설정 (agent_tools.config)

- [x] agent_tools 테이블에 config(JSON) 컬럼 추가 + Alembic 마이그레이션
- [x] AgentToolLink 모델 (association object 패턴) + Agent.tool_links 관계
- [x] tools_config 빌드 시 agent_tool.config → tool.auth_config에 merge
- [x] AgentCreate/Update 스키마에 tool_configs 필드 추가
- [x] agent_service — 도구 연결 시 config 저장
- [x] conversations.py + trigger_executor.py — merge 로직 적용
- [x] 테스트 (48개 전체 통과)

## Phase 6: 통합 + 폴리시

- [ ] E2E 시나리오 검증 (PRD 섹션 4)
- [x] 에러 핸들링, loading skeleton, empty state
- [x] Docker Compose 전체 구동 설정 (Dockerfile + docker-compose.yml)
- [ ] 접근성, 키보드 네비게이션, 성능 검증
