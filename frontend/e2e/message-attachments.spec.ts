import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

// Message attachments (P1-7): attach a file in the chat composer, send a turn
// against the keyless scripted model, and verify the file is uploaded and
// persisted. The composer's paperclip opens a native file chooser; the upload
// goes through POST /api/uploads during send. (The row is linked to the
// conversation, not the message — `message_id` is intentionally left null per
// chat_service.link_attachments_to_conversation — so verification asserts the
// real upload write + that the stored file is retrievable.)
const API = process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

test.describe('Message attachments', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let csrf: Record<string, string>
  let agentId: string
  let conversationId: string
  const filename = `e2e-note-${Date.now()}.txt`

  test.beforeAll(async ({ request }) => {
    csrf = await login(request)
    const models = (await (await request.get(`${API}/api/models`)).json()) as {
      id: string
      provider: string
    }[]
    const scripted = models.find((m) => m.provider === 'e2e_scripted')!
    const agent = (await (
      await request.post(`${API}/api/agents`, {
        headers: csrf,
        data: { name: `E2E Attach Agent ${Date.now()}`, system_prompt: 'x', model_id: scripted.id },
      })
    ).json()) as { id: string }
    agentId = agent.id
    const conv = (await (
      await request.post(`${API}/api/agents/${agentId}/conversations`, {
        headers: csrf,
        data: { title: 'Attachment E2E' },
      })
    ).json()) as { id: string }
    conversationId = conv.id
  })

  test.afterAll(async ({ request }) => {
    if (agentId) await request.delete(`${API}/api/agents/${agentId}`, { headers: csrf })
  })

  test('attaches a file in the composer and uploads it on send', async ({ page, request }) => {
    test.setTimeout(90_000)
    await page.goto(`/agents/${agentId}/conversations/${conversationId}`)

    const composer = page.getByPlaceholder('메시지 입력...')
    await expect(composer).toBeVisible()

    // 1. Attach a text file via the paperclip → native file chooser.
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByRole('button', { name: '파일 첨부' }).click(),
    ])
    await chooser.setFiles({
      name: filename,
      mimeType: 'text/plain',
      buffer: Buffer.from('hello from an E2E attachment'),
    })
    // The staged-attachment chip surfaces the filename before sending.
    await expect(page.getByText(filename).first()).toBeVisible()

    // 2. Send the turn; the upload fires during send, then the keyless scripted
    //    model replies deterministically.
    const uploadResponse = page.waitForResponse(
      (r) => r.url().includes('/api/uploads') && r.request().method() === 'POST',
    )
    await composer.fill('Here is a file.')
    await page.getByRole('button', { name: /전송/ }).click()

    // 3. The upload is a real backend write (201) carrying the filename.
    const upload = await uploadResponse
    expect(upload.status()).toBe(201)
    const uploaded = (await upload.json()) as { id: string; filename: string; url: string }
    expect(uploaded.filename).toBe(filename)

    await expect(page.getByText('E2E scripted document model is ready.').first()).toBeVisible({
      timeout: 60_000,
    })

    // 4. The stored file is persisted and retrievable.
    const fetched = await request.get(`${API}/api/uploads/${uploaded.id}`)
    expect(fetched.ok()).toBeTruthy()
  })
})
