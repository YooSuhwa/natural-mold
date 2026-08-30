# Moldy Frontend

Next.js 16 + React 19 기반 Moldy 웹 클라이언트입니다. 전체 프로젝트 세팅은
루트 [`README.md`](../README.md) / [`README_KO.md`](../README_KO.md)를 먼저 참고하세요.

## 빠른 시작

```bash
pnpm install
cp .env.example .env.local
pnpm dev -- --port 3000
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

`NEXT_PUBLIC_API_BASE_URL`은 실제 backend 포트를 가리켜야 합니다. 기본값은
`http://localhost:8001`입니다.

## Worktree 포트/CORS

frontend 포트가 바뀌면 backend의 `CORS_ALLOWED_ORIGINS`도 같은 origin을 허용해야
합니다. 예: frontend `3010`, backend `8010`.

```bash
# backend
cd ../backend
CORS_ALLOWED_ORIGINS=http://localhost:3010,http://127.0.0.1:3010 \
  uv run uvicorn app.main:app --reload --reload-dir app --port 8010

# frontend
cd ../frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8010 pnpm dev -- --port 3010
```

Next.js가 포트 충돌로 자동 선택한 포트를 그대로 쓰면 CORS/cookie/CSRF가 어긋날 수
있으므로 `--port`로 고정하세요.

## 테스트

```bash
pnpm lint
pnpm exec tsc --noEmit
pnpm test --run
pnpm build
pnpm test:e2e
```

## Playwright E2E 인증

E2E는 각 테스트가 로그인 폼을 반복해서 통과하지 않고, global setup에서 한 번 API
로그인 세션을 만들어 `storageState`로 주입하는 방식을 사용합니다.

`frontend/.env.example`의 테스트 계정 값을 필요하면 로컬/CI에서 덮어씁니다:

```env
E2E_USER_EMAIL=e2e@moldy.local
E2E_USER_PASSWORD=e2e-password-change-me
```

권장 흐름은 `login → register fallback → login → e2e/.auth/<lane>-user.json 저장`입니다.
scripted/live는 각각 `scripted-user.json`/`live-user.json`을 사용하며,
`E2E_AUTH_STATE_PATH`로 명시적으로 덮어쓸 수 있습니다. `e2e/.auth/`는 생성
산출물이므로 커밋하지 않습니다. `PW_SKIP_BACKEND=1`은
모든 `/api/*` 요청을 mock하는 spec에서만 사용하세요.

## Playwright E2E lane

E2E는 공유 개발 DB를 사용하지 않습니다. `DATABASE_URL`은
`postgresql+asyncpg://`, `DATABASE_URL_SYNC`는 `postgresql://`로 같은 lane 전용
DB를 가리켜야 합니다. scripted는 기본 포트 `3100/8101`과
`moldy_e2e_scripted` 또는 `moldy_e2e_scripted_...` DB 이름을, live는 `3200/8201`과
`moldy_e2e_live` 또는 `moldy_e2e_live_...` 이름을 사용합니다. 포트는 환경변수로
덮어쓸 수 있습니다.

```bash
# scripted throwaway DB 생성 및 migration
docker run -d --name moldy-e2e-scripted-pg -p 5433:5432 \
  -e POSTGRES_DB=moldy_e2e_scripted_local -e POSTGRES_USER=moldy -e POSTGRES_PASSWORD=moldy postgres:16-alpine
until docker exec moldy-e2e-scripted-pg pg_isready -U moldy -d moldy_e2e_scripted_local; do sleep 1; done
cd ../backend && DATABASE_URL='postgresql+asyncpg://moldy:moldy@localhost:5433/moldy_e2e_scripted_local' \
  DATABASE_URL_SYNC='postgresql://moldy:moldy@localhost:5433/moldy_e2e_scripted_local' uv run alembic upgrade head

# frontend에서 scripted lane 실행 (E2E_LLM_*는 실행기가 제거)
cd ../frontend
DATABASE_URL='postgresql+asyncpg://moldy:moldy@localhost:5433/moldy_e2e_scripted_local' \
DATABASE_URL_SYNC='postgresql://moldy:moldy@localhost:5433/moldy_e2e_scripted_local' \
pnpm test:e2e:scripted

docker rm -f moldy-e2e-scripted-pg
```

live는 scripted DB에 같은 이름의 database만 추가해 재사용하지 말고, 별도
throwaway 컨테이너를 만들고 마이그레이션한 뒤 실행합니다. 예를 들어 다음은
live의 기본 포트(`3200/8201`)와 별도 DB 포트(`5434`)를 사용합니다.

```bash
docker run -d --name moldy-e2e-live-pg -p 5434:5432 \
  -e POSTGRES_DB=moldy_e2e_live_local -e POSTGRES_USER=moldy -e POSTGRES_PASSWORD=moldy postgres:16-alpine
until docker exec moldy-e2e-live-pg pg_isready -U moldy -d moldy_e2e_live_local; do sleep 1; done
cd ../backend && DATABASE_URL='postgresql+asyncpg://moldy:moldy@localhost:5434/moldy_e2e_live_local' \
  DATABASE_URL_SYNC='postgresql://moldy:moldy@localhost:5434/moldy_e2e_live_local' uv run alembic upgrade head

cd ../frontend
DATABASE_URL='postgresql+asyncpg://moldy:moldy@localhost:5434/moldy_e2e_live_local' \
DATABASE_URL_SYNC='postgresql://moldy:moldy@localhost:5434/moldy_e2e_live_local' \
E2E_LLM_BASE_URL='...' E2E_LLM_API_KEY='...' E2E_LLM_MODEL='...' \
pnpm test:e2e:live

docker rm -f moldy-e2e-live-pg
```
