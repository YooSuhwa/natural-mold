# Moldy Backend

FastAPI + SQLAlchemy + LangChain/LangGraph로 동작하는 AI 에이전트 빌더 백엔드.

전체 프로젝트 개요/스택/세팅은 루트 [`README.md`](../README.md) 참조.

## 빠른 시작

```bash
# 의존성 설치
uv sync

# DB 마이그레이션
uv run alembic upgrade head

# 개발 서버 (http://localhost:8001/docs)
uv run uvicorn app.main:app --reload --port 8001
```

## 주요 명령

```bash
uv run pytest                # aiosqlite 기반 단위 테스트 (Postgres 불필요)
uv run pytest -m integration # Postgres가 필요한 통합 테스트 (기본 비활성)
uv run ruff check .          # 린트
uv run ruff format .         # 포맷
uv run alembic revision -m "..." --autogenerate  # 새 마이그레이션
```

## 디렉토리 구조

`app/main.py`가 FastAPI 앱 팩토리. `routers/`(HTTP) → `services/`(비즈니스) →
`models/`(SQLAlchemy ORM) 3계층 구조. AI 실행은 `agent_runtime/` 격리.
세부 사항은 루트 `CLAUDE.md` 참조.
