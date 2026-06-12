import { test, expect } from './fixtures'

// Real auth UI journeys against the live backend: signup, login (success +
// failure), and logout. The seeded E2E user (super_user) is reused for login;
// signup creates a fresh unique user each run (throwaway DB).
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'
const NAME = process.env.E2E_USER_NAME ?? process.env.E2E_NAME ?? 'E2E User'

// Start these tests signed OUT, overriding the global logged-in storageState.
const LOGGED_OUT = { storageState: { cookies: [], origins: [] } }

test.describe('Auth — login', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.use(LOGGED_OUT)

  test('rejects wrong credentials with an inline error and stays on /login', async ({ page }) => {
    await page.goto('/login')
    await page.locator('#login-email').fill(EMAIL)
    await page.locator('#login-password').fill('definitely-the-wrong-password')
    await page.getByRole('button', { name: '로그인', exact: true }).click()

    await expect(page.getByText('이메일 또는 비밀번호가 올바르지 않습니다')).toBeVisible()
    await expect(page).toHaveURL(/\/login(\?|$)/)
  })

  test('logs in the seeded user and lands on the authenticated dashboard', async ({ page }) => {
    await page.goto('/login')
    await page.locator('#login-email').fill(EMAIL)
    await page.locator('#login-password').fill(PASSWORD)
    await page.getByRole('button', { name: '로그인', exact: true }).click()

    await page.waitForURL((url) => url.pathname === '/')
    await expect(page.getByRole('button', { name: NAME }).first()).toBeVisible()
  })
})

test.describe('Auth — signup', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.use(LOGGED_OUT)

  test('registers a new user and signs them straight in', async ({ page }) => {
    const email = `e2e-signup-${Date.now()}@moldy.dev`
    const displayName = `Signup ${Date.now()}`

    await page.goto('/register')
    await page.locator('#reg-display-name').fill(displayName)
    await page.locator('#reg-email').fill(email)
    await page.locator('#reg-password').fill('correct horse battery staple 42')
    await page.getByRole('checkbox').check()
    await page.getByRole('button', { name: '가입하기', exact: true }).click()

    // Register auto-authenticates and redirects to the dashboard.
    await page.waitForURL((url) => url.pathname === '/')
    await expect(page.getByRole('button', { name: displayName }).first()).toBeVisible()
  })
})

test.describe('Auth — logout', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  // Uses the default logged-in storageState.

  test('logs out from the user menu and returns to /login', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: NAME }).first().click()
    await page.getByRole('menuitem', { name: '로그아웃' }).click()

    await page.waitForURL(/\/login(\?|$)/)
    await expect(page.locator('#login-email')).toBeVisible()
  })
})
