# CHECKPOINT — Credential 중앙 관리 시스템

## M1: Backend Core (Credential CRUD + Migration)
- [x] credential 모델, 스키마, 레지스트리, 서비스, 라우터 생성
- [x] Alembic 마이그레이션 (credentials 테이블 + Tool/MCPServer FK)
- [x] tool.py 모델에 credential_id FK 추가
- [x] __init__.py, main.py, error_codes.py 수정
- 검증: `cd backend && uv run ruff check . && uv run pytest tests/`
- done-when: ruff 에러 0, 테스트 전체 통과
- 상태: done

## M2: Backend Runtime Integration
- [x] chat_service.py 크리덴셜 해석 로직 + eager loading 확장
- [x] tool_service.py credential_id 지원
- [x] tool schema server_key_available 제거
- 검증: `cd backend && uv run ruff check . && uv run pytest tests/`
- done-when: ruff 에러 0, 테스트 전체 통과
- 상태: done

## M3: Frontend Connections 페이지
- [x] API 클라이언트, hooks, types 추가
- [x] Connections 페이지 (CRUD + 동적 폼)
- [x] 사이드바 네비게이션 + i18n 키
- 검증: `cd frontend && pnpm build`
- done-when: 빌드 성공 (타입 에러 0)
- 상태: done

## M4: Frontend Tool/MCP 크리덴셜 연결
- [x] prebuilt-auth-dialog 크리덴셜 선택
- [x] mcp-auth-dialog 크리덴셜 선택
- [x] add-tool-dialog MCP 등록 시 크리덴셜 선택
- 검증: `cd frontend && pnpm build && pnpm lint`
- done-when: 빌드 성공, 린트 통과
- 상태: done

## M5: Final Verification
- [x] Backend: ruff + pytest 전체 통과
- [x] Frontend: build + lint 전체 통과
- 검증: `cd backend && uv run ruff check . && uv run pytest tests/ && cd ../frontend && pnpm build && pnpm lint`
- done-when: 모든 검증 통과
- 상태: done
