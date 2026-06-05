# 실제 브라우저 기반 E2E 도입 가이드

이 문서는 Moldy의 현재 E2E 구성을 다른 프로젝트로 옮길 때 필요한 것과 작업 순서를
정리한다. 기준 소스는 다음 파일들이다.

- `frontend/playwright.config.ts`
- `frontend/e2e/global-setup.mjs`
- `frontend/e2e/fixtures.ts`
- `frontend/e2e/smoke.spec.ts`
- `frontend/e2e/*.spec.ts`
- `backend/app/seed/e2e_user.py`
- `backend/app/main.py`
- `backend/app/config.py`
- `backend/.env.example`
- `frontend/.env.example`
- `frontend/.gitignore`

목표는 단순 DOM 단위 테스트가 아니라, 실제 브라우저에서 실제 dev server를 띄우고,
로그인된 세션으로 주요 메뉴를 이동하며, 필요하면 화면 이미지를 캡처해 공유할 수
있는 운영 가능한 E2E 체계를 만드는 것이다.

---

## Moldy의 현재 구조

Moldy는 Playwright를 프론트엔드 패키지에 둔다. `frontend/package.json`에는 아래
스크립트가 있다.

```json
{
  "test:e2e": "playwright test",
  "test:e2e:ui": "playwright test --ui"
}
```

`frontend/playwright.config.ts`가 테스트 실행 시 서버를 함께 띄운다.

- `E2E_FRONTEND_PORT` 기본값은 `3000`
- `E2E_BACKEND_PORT` 기본값은 `8001`
- `E2E_BASE_URL` 기본값은 `http://localhost:<frontendPort>`
- `E2E_API_BASE_URL` 기본값은 `http://localhost:<backendPort>`
- 백엔드는 `CORS_ALLOWED_ORIGINS`를 현재 frontend origin에 맞춰 실행
- 프론트엔드는 `NEXT_PUBLIC_API_BASE_URL`을 현재 backend URL에 맞춰 실행
- `reuseExistingServer: true`라서 이미 켜진 서버가 있으면 재사용

핵심은 frontend port, backend port, CORS origin, API base URL을 한 묶음으로
움직이는 것이다. 이 네 값이 어긋나면 브라우저 쿠키, CSRF, CORS, API 요청이 서로
다른 서버를 보는 것처럼 실패한다.

`frontend/e2e/global-setup.mjs`는 브라우저마다 로그인 폼을 반복하지 않는다.
대신 Playwright API client로 한 번 로그인하고, 성공한 cookie storage를
`frontend/e2e/.auth/user.json`에 저장한다.

현재 인증 흐름은 다음 순서다.

1. `POST /api/auth/login`
2. 실패하면 `POST /api/auth/register`
3. `409 Conflict`이면 다시 `POST /api/auth/login`
4. 성공하면 `api.storageState({ path: authFile })`
5. Playwright config의 `use.storageState`가 모든 테스트 브라우저에 주입

Moldy는 백엔드에도 E2E 계정 bootstrap을 둔다. `backend/app/seed/e2e_user.py`는
`E2E_SEED_USER_ENABLED=true`일 때 로컬 전용 테스트 사용자를 만들거나 갱신한다.
단, `APP_ENV=production`이면 항상 스킵한다. 이 계정은 Moldy의 admin 권한이 필요한
화면까지 검증할 수 있도록 `is_super_user=True`로 생성된다.

---

## 다른 프로젝트에 필요한 구성요소

### 1. 브라우저 테스트 러너

Moldy는 Playwright를 쓴다. 다른 프로젝트도 실제 브라우저 조작, storage state,
trace, screenshot, route mocking을 한 번에 쓰려면 Playwright가 가장 단순하다.

필요한 패키지와 스크립트:

```bash
pnpm add -D @playwright/test
pnpm exec playwright install chromium
```

```json
{
  "scripts": {
    "test:e2e": "playwright test",
    "test:e2e:ui": "playwright test --ui"
  }
}
```

### 2. 서버를 테스트 실행 안에서 띄우는 설정

Moldy의 `webServer` 패턴을 옮긴다. 핵심은 테스트 실행자가 frontend와 backend를
직접 켜는 것이다.

```ts
import { defineConfig } from "@playwright/test";

const skipBackend = process.env.PW_SKIP_BACKEND === "1";
const frontendPort = Number(process.env.E2E_FRONTEND_PORT ?? "3000");
const backendPort = Number(process.env.E2E_BACKEND_PORT ?? "8001");
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${frontendPort}`;
const apiBaseURL =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${backendPort}`;
const corsOrigins = `http://localhost:${frontendPort},http://127.0.0.1:${frontendPort}`;

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.mjs",
  use: {
    baseURL,
    storageState: "./e2e/.auth/user.json",
    trace: "on-first-retry",
  },
  webServer: [
    ...(skipBackend
      ? []
      : [
          {
            command: `cd ../backend && CORS_ALLOWED_ORIGINS=${corsOrigins} uv run uvicorn app.main:app --port ${backendPort}`,
            port: backendPort,
            reuseExistingServer: true,
          },
        ]),
    {
      command: `NEXT_PUBLIC_API_BASE_URL=${apiBaseURL} pnpm dev --port ${frontendPort}`,
      port: frontendPort,
      reuseExistingServer: true,
    },
  ],
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
```

대상 프로젝트가 FastAPI/Next.js가 아니어도 원리는 같다.

- backend command를 그 프로젝트의 API 서버 실행 명령으로 바꾼다.
- frontend command를 그 프로젝트의 web dev server 명령으로 바꾼다.
- frontend가 읽는 API URL env 이름을 실제 이름으로 바꾼다.
- cookie 인증이면 API 서버의 CORS allow credentials 설정과 allowed origins를 맞춘다.

### 3. 로컬 E2E 전용 계정

테스트가 로그인 화면을 실제로 지나가거나 API login을 하려면 예측 가능한 계정이
필요하다. Moldy는 두 겹으로 처리한다.

첫 번째는 백엔드 seed다.

- env: `E2E_SEED_USER_ENABLED`
- env: `E2E_USER_EMAIL`
- env: `E2E_USER_PASSWORD`
- env: `E2E_USER_NAME`
- production에서는 무조건 skip
- 기존 계정이 있으면 이름, 활성 상태, 권한, 비밀번호를 갱신

두 번째는 Playwright global setup의 register fallback이다. seed가 아직 실행되지
않았거나 새 DB인 경우에도 테스트가 스스로 계정을 만들 수 있다.

다른 프로젝트에도 아래 원칙을 적용한다.

- 테스트 계정은 실제 사용자 계정과 분리한다.
- production/staging 공유 계정 비밀번호를 git에 넣지 않는다.
- production boot에서는 테스트 계정 자동 생성을 차단한다.
- admin 화면까지 돌려야 한다면 테스트 계정 권한을 명시적으로 올린다.
- 테스트가 만든 데이터는 테스트 종료 후 삭제하거나 고유 prefix를 붙인다.

### 4. 로그인 storage state

Moldy의 `global-setup.mjs` 패턴은 거의 그대로 재사용할 수 있다.

```js
import { request } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dirname = path.dirname(fileURLToPath(import.meta.url));
const authFile = path.join(dirname, ".auth", "user.json");

const backendPort = process.env.E2E_BACKEND_PORT ?? "8001";
const apiBase =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${backendPort}`;
const email = process.env.E2E_USER_EMAIL ?? "playwright-e2e@example.local";
const password = process.env.E2E_USER_PASSWORD ?? "change-me-for-local-e2e";
const name = process.env.E2E_USER_NAME ?? "E2E User";

export default async function globalSetup() {
  await fs.mkdir(path.dirname(authFile), { recursive: true });

  const api = await request.newContext({ baseURL: apiBase });
  try {
    let response = await api.post("/api/auth/login", {
      data: { email, password },
    });

    if (!response.ok()) {
      response = await api.post("/api/auth/register", {
        data: { email, password, name },
      });
    }

    if (!response.ok() && response.status() === 409) {
      response = await api.post("/api/auth/login", {
        data: { email, password },
      });
    }

    if (!response.ok()) {
      const body = await response.text().catch(() => "");
      throw new Error(
        `E2E authentication setup failed (${response.status()}): ${body}`,
      );
    }

    await api.storageState({ path: authFile });
  } finally {
    await api.dispose();
  }
}
```

프로젝트별로 바꿀 부분:

- login endpoint
- register endpoint
- request body shape
- CSRF token이 response body가 아닌 cookie/header로 오는 경우 처리
- MFA, OAuth, SSO처럼 API login이 어려운 인증 방식이면 테스트용 password login 또는
  test-only session mint endpoint를 별도로 둔다.

### 5. 공통 fixture

Moldy의 `frontend/e2e/fixtures.ts`는 두 가지를 한다.

- `PW_SKIP_BACKEND=1`일 때 `/api/auth/me`를 mock해서 로그인 상태처럼 보이게 한다.
- console error, page exception, request failure를 수집하고 테스트 끝에서 검증한다.

다른 프로젝트에도 최소한 아래 fixture를 둔다.

```ts
import { test as base, expect } from "@playwright/test";

type ErrorCollector = {
  console: string[];
  page: string[];
  network: string[];
};

export const test = base.extend<{ errors: ErrorCollector }>({
  errors: async ({ page }, use) => {
    const errors: ErrorCollector = { console: [], page: [], network: [] };

    page.on("console", (msg) => {
      if (msg.type() === "error") errors.console.push(msg.text());
    });
    page.on("pageerror", (err) => errors.page.push(err.message));
    page.on("requestfailed", (req) =>
      errors.network.push(`${req.method()} ${req.url()}`),
    );

    await use(errors);

    expect(errors.page, "JS exceptions detected").toEqual([]);
  },
});

export { expect };
```

실제 프로젝트에서는 favicon, devtools 안내, analytics 차단처럼 알려진 benign error만
좁게 무시한다.

### 6. smoke spec

Moldy의 `frontend/e2e/smoke.spec.ts`는 가장 중요한 메뉴와 dialog가 깨지지 않는지
넓게 확인한다.

현재 Moldy smoke는 다음을 포함한다.

- `/` dashboard
- `/agents/new`
- `/agents/new/template`
- `/tools`
- `/models`
- `/usage`
- agent 생성 후 chat/settings/redirect 확인
- 모델 추가 dialog
- 도구 생성 dialog
- 사전 구성 도구 credential dialog
- agent 삭제 confirmation dialog
- 대화형 agent 생성 화면

다른 프로젝트의 첫 E2E도 이 정도의 “메뉴 순회 smoke”부터 시작한다.

권장 패턴:

```ts
import { test, expect } from "./fixtures";

test.describe("Smoke", () => {
  test("/dashboard loads", async ({ page, errors }) => {
    await page.goto("/");
    await page.waitForLoadState("domcontentloaded");

    await expect(
      page.getByRole("heading", { name: /dashboard/i }),
    ).toBeVisible();

    expect(errors.console).toEqual([]);
    expect(errors.network).toEqual([]);
  });

  test("/settings opens dialog", async ({ page, errors }) => {
    await page.goto("/settings");
    await page.getByRole("button", { name: /new|add|create/i }).click();
    await expect(page.getByRole("dialog")).toBeVisible();

    expect(errors.console).toEqual([]);
    expect(errors.network).toEqual([]);
  });
});
```

셀렉터 원칙:

- `getByRole`, `getByLabel`, `getByPlaceholder`를 우선 사용한다.
- 복잡한 컴포넌트에는 안정적인 `data-testid`를 추가한다.
- CSS class selector는 레이아웃 구현에 묶이므로 마지막 수단으로 둔다.
- E2E 검증 문구가 i18n에 따라 바뀌는 프로젝트는 locale을 고정하거나 key에 가까운
  접근성 label을 사용한다.

### 7. 실제 backend와 mock-only 모드 분리

Moldy는 두 모드를 모두 지원한다.

실제 backend 모드:

- 기본값
- FastAPI와 Next.js를 둘 다 실행
- login storage state가 실제 cookie를 가진다.
- smoke, 동적 페이지, CRUD 흐름 검증에 사용

mock-only 모드:

- `PW_SKIP_BACKEND=1`
- 백엔드를 띄우지 않는다.
- 모든 `/api/*` 요청을 `page.route`로 mock해야 한다.
- UI 상태, dialog, table, chart처럼 backend 안정성에 의존하지 않는 화면 검증에 사용

Moldy 예시:

- `credentials.spec.ts`: credential catalog/create API를 route mock
- `mcp-server-wizard.spec.ts`: MCP probe/discover API를 route mock
- `health-check.spec.ts`: model health API와 history API를 route mock
- `model-test.spec.ts`: provider test success/auth error 응답을 route mock

다른 프로젝트에도 규칙을 명확히 둔다.

- smoke와 핵심 사용자 journey는 실제 backend로 돌린다.
- 외부 API, 결제, LLM, OAuth, webhook처럼 불안정하거나 비용이 드는 경계는 mock한다.
- `PW_SKIP_BACKEND=1`로 돌릴 수 있는 spec은 모든 API를 명시적으로 mock한다.
- 실제 backend가 필요한 spec은 `test.skip(process.env.PW_SKIP_BACKEND === '1', ...)`를 둔다.

### 8. CSRF와 상태 변경 API

Moldy는 HttpOnly cookie + CSRF double-submit을 쓰기 때문에, Playwright request로
테스트 데이터를 만들 때 CSRF header가 필요하다.

`frontend/e2e/smoke.spec.ts`의 `loginApi()`는 다음 흐름을 가진다.

1. `POST /api/auth/login`
2. 응답 body의 `csrf_token` 추출
3. 상태 변경 요청에 `X-CSRF-Token` header 추가

다른 프로젝트에서 cookie 인증과 CSRF를 쓰면 이 헬퍼를 먼저 만든다.

```ts
async function loginApi(request) {
  const res = await request.post(`${API_BASE}/api/auth/login`, {
    data: { email: E2E_EMAIL, password: E2E_PASSWORD },
  });
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  return { "X-CSRF-Token": body.csrf_token };
}
```

Bearer token 인증 프로젝트라면 같은 자리에 `Authorization: Bearer <token>`을
반환하면 된다.

### 9. 테스트 데이터 생성과 정리

Moldy smoke는 동적 페이지 검증을 위해 API로 agent와 conversation을 만든 뒤,
`afterAll`에서 agent를 삭제한다.

다른 프로젝트에도 다음 규칙을 둔다.

- UI로 만들 필요가 없는 전제 데이터는 API로 만든다.
- 사용자가 실제로 밟아야 하는 핵심 flow만 UI로 만든다.
- 데이터 이름에는 `E2E` prefix를 붙인다.
- 테스트가 생성한 데이터 id를 저장하고 `afterAll` 또는 teardown에서 삭제한다.
- 실패 시에도 재실행 가능한 idempotent setup을 선호한다.

### 10. 화면 캡처 산출물

Moldy의 repo 규칙은 E2E 산출물을 repo root에 흩뿌리지 않고 아래 경로에 모으는 것이다.

```text
output/e2e-captures/<YYYYMMDD>-<feature>/
```

`output/`은 `.gitignore`에 포함되어 있다. Playwright 자체 산출물도
`frontend/.gitignore`에서 제외한다.

- `frontend/e2e/.auth/`
- `frontend/test-results/`
- `frontend/playwright-report/`

다른 프로젝트에도 같은 규칙을 둔다.

캡처용 helper 예시:

```ts
import fs from "node:fs/promises";
import path from "node:path";

export async function capturePage(page, feature, name) {
  const stamp = new Date().toISOString().slice(0, 10).replaceAll("-", "");
  const dir = path.resolve(
    process.cwd(),
    "..",
    "output",
    "e2e-captures",
    `${stamp}-${feature}`,
  );
  await fs.mkdir(dir, { recursive: true });

  const file = path.join(dir, `${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}
```

운영 규칙:

- 캡처 전에 로그인 계정과 화면 상태가 맞는지 확인한다.
- secret 화면은 실제 secret 대신 더미 값을 사용한다.
- 캡처 후 `file output/e2e-captures/.../*.png`로 실제 PNG와 해상도를 확인한다.
- 사람에게 공유하기 전 직접 열어 텍스트 잘림, 빈 화면, 깨진 이미지가 없는지 확인한다.
- trace/video/raw capture도 같은 feature directory 아래에 모은다.

### 11. CI와 로컬 명령

로컬 기본 명령:

```bash
cd frontend
pnpm test:e2e
```

포트 충돌이 있거나 여러 worktree를 동시에 돌리는 경우:

```bash
cd frontend
E2E_FRONTEND_PORT=3010 \
E2E_BACKEND_PORT=8010 \
E2E_BASE_URL=http://localhost:3010 \
E2E_API_BASE_URL=http://localhost:8010 \
pnpm test:e2e
```

mock-only spec만 지정해서 돌리는 경우:

```bash
cd frontend
PW_SKIP_BACKEND=1 pnpm exec playwright test e2e/credentials.spec.ts
```

CI에서는 다음을 추가로 고정한다.

- DB service 또는 test database
- migration 실행
- E2E env 값
- Playwright browser install cache
- test result, trace, screenshot artifact upload
- worker 수 제한: `E2E_WORKERS=1` 또는 작은 값부터 시작

---

## 이식 체크리스트

### Backend

- [ ] test database를 띄우는 명령이 있다.
- [ ] migration을 테스트 전에 실행할 수 있다.
- [ ] 로컬 E2E 계정 seed가 있다.
- [ ] production에서는 E2E seed가 무조건 비활성화된다.
- [ ] cookie 인증이면 CORS allow credentials와 allowed origins가 명확하다.
- [ ] CSRF가 있으면 테스트용 header helper가 있다.
- [ ] 테스트 데이터 cleanup API 또는 seed reset 방법이 있다.

### Frontend

- [ ] Playwright가 설치되어 있다.
- [ ] `test:e2e`, `test:e2e:ui` 스크립트가 있다.
- [ ] `playwright.config.ts`가 frontend/backend 서버를 함께 띄운다.
- [ ] frontend API base URL env가 테스트 backend port를 본다.
- [ ] `globalSetup`이 login/register fallback 후 storage state를 저장한다.
- [ ] `.auth/`, `test-results/`, `playwright-report/`가 gitignore에 있다.
- [ ] 공통 fixture가 console/page/network error를 수집한다.

### Spec

- [ ] dashboard 또는 home route smoke가 있다.
- [ ] 주요 메뉴 route smoke가 있다.
- [ ] 최소 1개 dialog/open-close smoke가 있다.
- [ ] 실제 backend가 필요한 CRUD journey가 1개 이상 있다.
- [ ] 외부 API 또는 비용 발생 경계는 route mock spec으로 분리되어 있다.
- [ ] `PW_SKIP_BACKEND=1` 모드에서 mock-only spec이 돌아간다.
- [ ] 주요 셀렉터가 role/label/testid 기반이다.

### Capture

- [ ] screenshot/video/trace 저장 경로가 정해져 있다.
- [ ] 저장 경로가 gitignore에 있다.
- [ ] 캡처 전에 더미 secret만 사용한다.
- [ ] 공유 전 PNG 파일 여부와 해상도를 확인한다.
- [ ] 공유 전 이미지를 직접 열어 UI가 깨지지 않았는지 확인한다.

---

## 권장 도입 순서

1. Playwright 설치와 `test:e2e` 스크립트를 추가한다.
2. frontend만 띄워 정적 smoke 한 개를 통과시킨다.
3. backend webServer를 붙이고 CORS/API base URL을 맞춘다.
4. E2E 계정 seed와 `globalSetup` storage state를 추가한다.
5. 로그인된 dashboard smoke를 통과시킨다.
6. 주요 메뉴 smoke를 5~10개 추가한다.
7. API setup + UI 검증이 섞인 실제 CRUD journey를 1개 추가한다.
8. 외부 API가 필요한 화면은 route mock spec으로 분리한다.
9. 캡처 helper와 `output/e2e-captures/` 규칙을 추가한다.
10. CI artifact로 Playwright report, trace, screenshot을 올린다.

이 순서로 가면 처음부터 모든 기능을 E2E로 덮으려다 느려지는 일을 피하면서도,
"서버가 실제로 뜨고, 로그인되고, 브라우저에서 핵심 메뉴가 깨지지 않는다"는 신호를
빠르게 얻을 수 있다.

---

## Moldy에서 배울 점

- 로그인은 매 테스트마다 UI로 반복하지 않고 `storageState`로 공유한다.
- 실제 backend smoke와 route mock spec을 분리한다.
- port, CORS, API base URL은 한 세트로 다룬다.
- 테스트 계정은 backend seed와 Playwright register fallback으로 이중 안전망을 둔다.
- production에서는 테스트 seed를 강제로 막는다.
- 상태 변경 API는 CSRF/auth helper를 만들어 재사용한다.
- E2E 산출물은 정해진 ignored directory에 모은다.
- 화면 검증은 role/label/testid 기반으로 작성한다.

이 구성을 다른 프로젝트에 옮길 때 가장 먼저 복제할 파일은
`playwright.config.ts`, `global-setup.mjs`, `fixtures.ts`, `smoke.spec.ts` 네 개다.
그 다음 프로젝트의 인증 방식과 서버 실행 방식에 맞춰 backend seed, env, cleanup
흐름을 붙이면 된다.
