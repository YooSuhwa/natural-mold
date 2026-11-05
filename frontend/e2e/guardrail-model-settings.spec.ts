import { createServer, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'

import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

// Guardrail model settings (operator screen, guardrail-minimum-plan.md §2.1). A tiny fake
// pii-detector on a free local port answers the connection test; the backend probes it.
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function csrfHeaders(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

function startFakeDetector(): Promise<Server> {
  const server = createServer((req, res) => {
    if (req.method === 'POST' && req.url === '/detect') {
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(
        JSON.stringify({
          model: 'fake-detector@e2e',
          results: [[{ entity: 'PERSON', start: 4, end: 7, score: 0.99 }]],
        }),
      )
      return
    }
    res.writeHead(404)
    res.end()
  })
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)))
}

test.describe('Guardrail model settings (super_user)', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let detector: Server
  let detectorUrl: string
  let csrf: Record<string, string>

  test.beforeAll(async ({ request }) => {
    detector = await startFakeDetector()
    detectorUrl = `http://127.0.0.1:${(detector.address() as AddressInfo).port}`
    csrf = await csrfHeaders(request)
    await request.delete(`${API}/api/guardrail-model-settings`, { headers: csrf })
  })

  test.afterAll(async ({ request }) => {
    await request.delete(`${API}/api/guardrail-model-settings`, { headers: csrf })
    await new Promise((resolve) => detector.close(resolve))
  })

  test('tests an address, saves it on and off, and resets to the default', async ({
    page,
    request,
  }) => {
    test.setTimeout(60_000)
    await page.goto('/settings/guardrail-model')
    await expect(page.getByText('운영자 전용')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText('기본값(환경변수)', { exact: true })).toBeVisible()

    const url = page.getByLabel('서비스 주소')
    const probe = page.getByTestId('guardrail-model-probe')

    // A dead port: the probe reports it without saving anything.
    await url.fill('http://127.0.0.1:9')
    await page.getByRole('button', { name: '연결 테스트' }).click()
    await expect(probe).toContainText('서비스에 연결할 수 없습니다', { timeout: 15_000 })

    // The fake detector: connected, with its model name.
    await url.fill(detectorUrl)
    await page.getByRole('button', { name: '연결 테스트' }).click()
    await expect(probe).toContainText('연결되었습니다', { timeout: 15_000 })
    await expect(probe).toContainText('fake-detector@e2e')

    // Save it switched on, then off: the saved row wins over the environment either way.
    const toggle = page.getByRole('switch', { name: '모델 사용' })
    for (const enabled of [true, false]) {
      if ((await toggle.isChecked()) !== enabled) await toggle.click()
      await page.getByRole('button', { name: '저장', exact: true }).click()
      await expect(page.getByText('저장된 설정', { exact: true })).toBeVisible({ timeout: 15_000 })
      await expect
        .poll(async () => await (await request.get(`${API}/api/guardrail-model-settings`)).json(), {
          timeout: 15_000,
        })
        .toMatchObject({ source: 'saved', enabled, url: detectorUrl })
    }

    // Reset: the environment applies again.
    await page.getByRole('button', { name: '기본값으로 되돌리기' }).click()
    await expect(page.getByText('기본값(환경변수)', { exact: true })).toBeVisible({
      timeout: 15_000,
    })
    await expect
      .poll(
        async () =>
          (
            (await (await request.get(`${API}/api/guardrail-model-settings`)).json()) as {
              source: string
            }
          ).source,
        { timeout: 15_000 },
      )
      .toBe('environment')
  })

  test('rejects an address with a user name and password', async ({ page }) => {
    await page.goto('/settings/guardrail-model')
    await page.getByLabel('서비스 주소').fill('http://user:pw@pii-detector:8095')
    await page.getByRole('button', { name: '저장', exact: true }).click()
    await expect(page.getByText('주소에 사용자 이름이나 비밀번호를 넣을 수 없습니다')).toBeVisible({
      timeout: 15_000,
    })
  })
})
