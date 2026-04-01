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

## Phase 6: 통합 + 폴리시

- [ ] E2E 시나리오 검증 (PRD 섹션 4)
- [x] 에러 핸들링, loading skeleton, empty state
- [x] Docker Compose 전체 구동 설정 (Dockerfile + docker-compose.yml)
- [ ] 접근성, 키보드 네비게이션, 성능 검증
