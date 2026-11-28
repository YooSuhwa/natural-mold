// Feature: auth-login (features/auth-login.md)
// Logged-out browser: wrong password stays on /login with an inline error; the seeded
// user's real credentials land on the dashboard; /api/auth/me then returns that user.
export const options = { loggedOut: true }

export default async function ({ page, state, step, expect, context }) {
  await page.goto('/login')
  await expect(page.locator('#login-email')).toBeVisible({ timeout: 60_000 })
  await step('login-page')

  await page.locator('#login-email').fill(state.USER_EMAIL)
  await page.locator('#login-password').fill('definitely-the-wrong-password')
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await expect(page.getByText('이메일 또는 비밀번호가 올바르지 않습니다')).toBeVisible({ timeout: 15_000 })
  await expect(page).toHaveURL(/\/login(\?|$)/)
  await step('wrong-password-rejected')

  await page.locator('#login-password').fill(state.USER_PASSWORD)
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await page.waitForURL((url) => url.pathname === '/', { timeout: 30_000 })
  await expect(page.getByRole('heading', { name: new RegExp(`${state.USER_NAME}님`) })).toBeVisible({ timeout: 30_000 })
  await step('dashboard')

  const me = await (await context.request.get(`${state.API_URL}/api/auth/me`)).json()
  const email = me.email ?? me.user?.email
  if (email !== state.USER_EMAIL) throw new Error(`/api/auth/me returned ${JSON.stringify(me).slice(0, 200)}`)
  return { email }
}
