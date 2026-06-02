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

권장 흐름은 `login → register fallback → login → e2e/.auth/user.json 저장`입니다.
`e2e/.auth/`는 생성 산출물이므로 커밋하지 않습니다. `PW_SKIP_BACKEND=1`은
모든 `/api/*` 요청을 mock하는 spec에서만 사용하세요.
