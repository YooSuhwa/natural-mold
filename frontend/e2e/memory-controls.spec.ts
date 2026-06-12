import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

// Memory controls (/settings/memory), real backend, no LLM needed: create a
// user-scope memory through the form, edit it, and delete it (each step
// verified via /api/memories), plus persist a memory write-policy change via
// /api/me/memory-settings. The page is instrumented with data-testids.
const API = process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

type Memory = { id: string; content: string; scope: string }

async function searchMemories(request: APIRequestContext, q: string): Promise<Memory[]> {
  const res = await request.get(`${API}/api/memories?q=${encodeURIComponent(q)}`)
  expect(res.ok()).toBeTruthy()
  return (await res.json()) as Memory[]
}

test.describe('Memory controls', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let csrf: Record<string, string>
  const tag = Date.now()
  const prefix = `E2E memory fact ${tag}`
  const content = prefix
  const editedContent = `${prefix} (edited)`

  test.beforeAll(async ({ request }) => {
    csrf = await login(request)
  })

  test.afterAll(async ({ request }) => {
    for (const m of await searchMemories(request, prefix)) {
      await request.delete(`${API}/api/memories/${m.id}`, { headers: csrf })
    }
  })

  test('creates, edits, and deletes a user memory via the UI', async ({ page, request }) => {
    test.setTimeout(60_000)
    await page.goto('/settings/memory')
    await expect(page.getByTestId('memory-settings-page')).toBeVisible()

    // 1. Create a user-scope memory (scope defaults to "user").
    await page.getByTestId('memory-content-input').fill(content)
    await page.getByTestId('memory-create-submit').click()

    let memoryId = ''
    await expect
      .poll(async () => {
        const found = (await searchMemories(request, prefix)).find((m) => m.content === content)
        memoryId = found?.id ?? ''
        return found?.content
      }, { timeout: 15_000 })
      .toBe(content)

    // Pin to the record by id so content edits don't move the locator.
    const item = page.getByTestId(`memory-item-${memoryId}`)
    await expect(item).toBeVisible()

    // 2. Edit the content inline and save.
    await item.getByTestId('memory-edit-button').click()
    await item.getByTestId('memory-edit-content').fill(editedContent)
    await item.getByTestId('memory-save-edit').click()
    await expect
      .poll(async () => (await searchMemories(request, prefix)).find((m) => m.id === memoryId)?.content, {
        timeout: 15_000,
      })
      .toBe(editedContent)

    // 3. Delete it (inline confirm) — the API drops the record.
    await item.getByTestId('memory-delete-button').click()
    await item.getByTestId('memory-delete-confirm').getByRole('button', { name: '삭제 확인' }).click()
    await expect
      .poll(async () => (await searchMemories(request, prefix)).length, { timeout: 15_000 })
      .toBe(0)
  })

  test('changes the memory write policy and persists it', async ({ page, request }) => {
    const before = (await (await request.get(`${API}/api/me/memory-settings`)).json()) as {
      memory_write_policy: string
    }
    const target = before.memory_write_policy === 'auto' ? 'ask' : 'auto'

    await page.goto('/settings/memory')
    await expect(page.getByTestId('memory-settings-page')).toBeVisible()

    await page.getByTestId('memory-write-policy-trigger').click()
    await page.getByTestId(`memory-write-policy-option-${target}`).click()
    await page.getByTestId('memory-settings-save').click()

    await expect
      .poll(
        async () =>
          (
            (await (await request.get(`${API}/api/me/memory-settings`)).json()) as {
              memory_write_policy: string
            }
          ).memory_write_policy,
        { timeout: 15_000 },
      )
      .toBe(target)
  })
})
