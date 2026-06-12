# Contributing to Moldy

Moldy는 PoC 단계의 AI 에이전트 빌더입니다. 기여를 환영합니다.

## 시작하기

전체 세팅은 루트 [`README.md`](./README.md) / [`README_KO.md`](./README_KO.md) +
백엔드 [`backend/README.md`](./backend/README.md) 참조.

```bash
# 런타임: Python 3.12 → uv가 자동 설치 · Node 22 → 직접 설치 (.node-version = 22)

# DB
docker-compose up -d postgres

# Backend
cd backend && uv sync && uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8001

# Frontend
cd frontend && pnpm install && pnpm dev
```

## 브랜치 / 커밋 규칙

- main 직접 커밋 금지 — 기능은 `feature/{이름}`, 버그는 `fix/{이슈}`, 청소는 `chore/{대상}` 브랜치에서 작업 후 PR 머지
- 커밋 메시지: `[타입] 제목` (한국어, 50자 이내)
  - 타입: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`
- 본문은 변경 의도(WHY) 위주. 무엇을 했는지(WHAT)는 diff에서 보이므로 핵심만
- 커밋 끝에 `Co-Authored-By: Claude <noreply@anthropic.com>` 권장 (Claude Code 사용 시)

## 코드 컨벤션

세부 컨벤션은 루트 `CLAUDE.md` + `frontend/AGENTS.md`에 정리되어 있습니다. 핵심:

### Backend (Python 3.12, FastAPI, SQLAlchemy 2.0 async, ruff, pyright)

- 타입 힌트 필수, async/await + `select()` 쿼리
- 라우터 → 서비스 → 모델 3계층, 비즈니스 로직은 `services/`
- 새 테이블 추가 시 `uv run alembic revision -m "..." --autogenerate` 후 검토
- 테스트: `aiosqlite` in-memory 기반 단위 테스트 (Postgres 불필요). 통합 테스트는 `pytest -m integration`
- 린트: `uv run ruff check . && uv run ruff format .`

### Frontend (Next.js 16 + React 19 + TailwindCSS v4 + shadcn/ui)

- TypeScript strict, `any` 금지 — `unknown` + 타입 가드
- React 19 Server Components 우선, `'use client'` 최소화
- 다이얼로그는 `<DialogShell>` + `DIALOG_SIZE`/`DIALOG_HEIGHT` 토큰 (직접 `<DialogContent>` 금지)
- 폼 footer는 `FormFooter` 재사용 (`onCancel` + `onSubmit` + `pending`)
- 헤더 chrome 없는 다이얼로그는 `<DialogShell.Header srOnly title="..." />`
- 한국어 날짜는 `formatLongDate`/`formatMediumDate` (`lib/utils/format-relative-time.ts`) — KST 고정
- 빌드: `pnpm lint && pnpm build`

### 디자인 토큰

- 강조색: `--primary-strong` (emerald 어두운 톤). `text-primary`는 옅은 surface — 강조 의도면 `text-primary-strong`
- 시맨틱 상태: `--status-{success,info,warn,danger,accent}` — raw `bg-amber-*`, `bg-sky-*` 금지
- 자세한 스펙: `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`

## PR 체크리스트

- [ ] 테스트 통과 (`uv run pytest`, `pnpm build`)
- [ ] 린트 통과 (`uv run ruff check .`, `pnpm lint`)
- [ ] DB 변경 시 alembic 마이그레이션 포함
- [ ] PR 본문에 변경 의도 + 검증 결과 명시
- [ ] 관련 이슈 번호 연결 (있을 경우)

## 보안 이슈

보안 취약점은 공개 Issue로 보고하지 마시고 [`SECURITY.md`](./SECURITY.md)의 절차를 따라주세요.

## 라이선스

기여하신 내용은 MIT 라이선스로 배포됩니다 (`LICENSE` 참조).
