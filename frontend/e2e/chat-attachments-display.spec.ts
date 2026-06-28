import { test, expect } from './fixtures'
import type { APIRequestContext, Browser } from '@playwright/test'

// Chat attachments DISPLAY (Phase 0 + P1). Proves the end-to-end flow a unit
// test can't: a real run backfills message_attachments.message_id, the read
// path echoes the attachment, it renders inline on the user bubble, survives a
// reload, and is gated out of the public share. The file-panel badge / preview
// dialog / jump-to-message UI is covered deterministically by vitest component
// tests (message-attachments, artifact-panel-content, jump-to-message); the
// rail only opens from a generated-artifact card, so an attachment-only
// conversation can't surface that panel — hence the list contract is asserted
// here via GET /files instead.

const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const FRONTEND =
  process.env.E2E_FRONTEND_BASE_URL ?? `http://localhost:${process.env.E2E_FRONTEND_PORT ?? '3000'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

// 1×1 transparent PNG.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
)

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

// CSRF double-submit header matching THIS request context's own moldy_csrf
// cookie (the per-test `request` is authenticated from the shared storageState,
// not from beforeAll's login — so its cookie value is what must be echoed).
async function csrfHeader(request: APIRequestContext): Promise<Record<string, string>> {
  const { cookies } = await request.storageState()
  const token = cookies.find((c) => c.name === 'moldy_csrf')?.value ?? ''
  return { 'X-CSRF-Token': token }
}

interface MessageRow {
  role: string
  attachments?: { id: string; filename: string; url: string }[] | null
}
interface FileRow {
  source: string
  id: string
  name: string
  message_id?: string | null
  editable: boolean
  preview_url: string
  download_url: string
}

test.describe('Chat attachments display', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let csrf: Record<string, string>
  let agentId: string
  let conversationId: string
  let uploadId: string
  const imageName = `e2e-photo-${Date.now()}.png`

  test.beforeAll(async ({ request }) => {
    csrf = await login(request)
    const models = (await (await request.get(`${API}/api/models`)).json()) as {
      id: string
      provider: string
    }[]
    const scripted = models.find((m) => m.provider === 'e2e_scripted')
    if (!scripted) throw new Error('e2e_scripted model should be seeded')
    agentId = (
      (await (
        await request.post(`${API}/api/agents`, {
          headers: csrf,
          data: { name: `E2E Attach Display ${Date.now()}`, system_prompt: 'x', model_id: scripted.id },
        })
      ).json()) as { id: string }
    ).id
    conversationId = (
      (await (
        await request.post(`${API}/api/agents/${agentId}/conversations`, {
          headers: csrf,
          data: { title: 'Attachment display E2E' },
        })
      ).json()) as { id: string }
    ).id
  })

  test.afterAll(async ({ request }) => {
    if (agentId) await request.delete(`${API}/api/agents/${agentId}`, { headers: csrf })
  })

  test('sent image renders inline, survives reload, and is echoed via the read path', async ({
    page,
    request,
  }) => {
    // Generous timeout — `next dev` cold-compiles the chat route on first hit.
    test.setTimeout(150_000)
    await page.goto(`/agents/${agentId}/conversations/${conversationId}`, {
      waitUntil: 'domcontentloaded',
    })

    const composer = page.getByPlaceholder('메시지 입력...')
    await expect(composer).toBeVisible({ timeout: 60_000 })

    // Attach an image via the paperclip → native file chooser.
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByRole('button', { name: '파일 첨부' }).click(),
    ])
    await chooser.setFiles({ name: imageName, mimeType: 'image/png', buffer: PNG })
    await expect(page.getByText(imageName).first()).toBeVisible()

    // Send — the upload fires during send, then the scripted model replies.
    const uploadResponse = page.waitForResponse(
      (r) => r.url().includes('/api/uploads') && r.request().method() === 'POST',
    )
    await composer.fill('Here is an image.')
    await page.getByRole('button', { name: /전송/ }).click()
    const upload = await uploadResponse
    expect(upload.status()).toBe(201)
    uploadId = ((await upload.json()) as { id: string }).id

    await expect(page.getByText('E2E scripted document model is ready.').first()).toBeVisible({
      timeout: 60_000,
    })

    // 2a — WITHOUT a reload, the just-sent attachment appears inline once the run
    //      completes and the /files query is refetched (message_id backfill).
    const liveUserMsg = page.locator('[data-moldy-message-role="user"]').last()
    await expect(liveUserMsg.getByRole('img', { name: imageName })).toBeVisible({
      timeout: 25_000,
    })
    // The bubble body stays clean — no raw `[attachment: …](url)` markdown next
    // to the thumbnail (the attachment rides its id, not body text).
    await expect(liveUserMsg).not.toContainText('[attachment:')

    // fix 1 — the composer "파일 목록" button opens the file panel even though this
    //         conversation has no generated artifact card to click; the attachment
    //         shows there under "내가 보낸 파일" with the 첨부 badge.
    await page.getByRole('button', { name: '파일 목록' }).click()
    // (the rail renders in both the desktop + overlay slots → match the first)
    await expect(page.getByText('내가 보낸 파일').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('첨부').first()).toBeVisible()

    // 1. Read path echoes the attachment on the user message (M1 backfill).
    const envelope = (await (
      await request.get(`${API}/api/conversations/${conversationId}/messages`)
    ).json()) as { messages: MessageRow[] }
    const userWithAttachment = envelope.messages.find(
      (m) => m.role === 'user' && (m.attachments ?? []).some((a) => a.id === uploadId),
    )
    expect(userWithAttachment, 'user message should echo the attachment').toBeTruthy()

    // 2. After a reload the attachment renders inline on the user bubble as a
    //    thumbnail, looked up by message id from /files (persistence proof).
    await page.reload({ waitUntil: 'domcontentloaded' })
    const reloadedUserMsg = page.locator('[data-moldy-message-role="user"]').last()
    await expect(reloadedUserMsg.getByRole('img', { name: imageName })).toBeVisible({
      timeout: 30_000,
    })
    // ...and the stored content is still clean after reload.
    await expect(reloadedUserMsg).not.toContainText('[attachment:')

    // 4. Unified /files lists it as a read-only attachment linked to a message (M3).
    const files = (await (
      await request.get(`${API}/api/conversations/${conversationId}/files`)
    ).json()) as FileRow[]
    const attached = files.find((f) => f.id === uploadId)
    expect(attached, 'attachment should appear in /files').toBeTruthy()
    expect(attached?.source).toBe('attached')
    expect(attached?.name).toBe(imageName)
    expect(attached?.editable).toBe(false)
    expect(attached?.message_id, 'attachment should be linked to a message').toBeTruthy()
    expect(attached?.download_url).toContain(`/api/uploads/${uploadId}`)

    // 5. The owner can fetch the bytes (Phase 0 auth — same authed context).
    expect((await request.get(`${API}/api/uploads/${uploadId}`)).status()).toBe(200)
  })

  test('public share excludes the attachment and the files endpoint requires auth', async ({
    request,
    browser,
  }: {
    request: APIRequestContext
    browser: Browser
  }) => {
    // Publish the conversation (CSRF header must match the per-test cookie).
    const shareRes = await request.post(`${API}/api/conversations/${conversationId}/share`, {
      headers: await csrfHeader(request),
    })
    expect(shareRes.ok(), `share create ${shareRes.status()}`).toBeTruthy()
    const shareToken = ((await shareRes.json()) as { share_token: string }).share_token
    expect(shareToken).toBeTruthy()

    // Anonymous (no cookies) context for the public surfaces.
    const anon = await browser.newContext({ storageState: { cookies: [], origins: [] } })
    try {
      // D11/M2 — the public share snapshot shows NO attachments on any message.
      const shared = (await (
        await anon.request.get(`${API}/api/shares/${shareToken}/messages`)
      ).json()) as { messages: MessageRow[] }
      for (const msg of shared.messages) {
        expect(msg.attachments ?? [], 'share view must not expose attachments').toHaveLength(0)
      }

      // The unified files endpoint is auth-gated (no public/anon access).
      const anonFiles = await anon.request.get(
        `${API}/api/conversations/${conversationId}/files`,
      )
      expect(anonFiles.status(), 'files endpoint must reject anon').toBeGreaterThanOrEqual(401)
      expect(anonFiles.ok()).toBeFalsy()

      // The raw upload is not retrievable without auth (Phase 0).
      const anonUpload = await anon.request.get(`${API}/api/uploads/${uploadId}`)
      expect(anonUpload.ok(), 'upload must reject anon').toBeFalsy()
    } finally {
      await anon.close()
    }
  })
})
