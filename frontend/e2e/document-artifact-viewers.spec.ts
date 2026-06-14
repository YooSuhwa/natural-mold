import fs from 'node:fs/promises'
import path from 'node:path'
import {
  request as playwrightRequest,
  type APIRequestContext,
  type Locator,
  type Page,
} from '@playwright/test'
import { test, expect } from './fixtures'

const BACKEND_PORT = process.env.E2E_BACKEND_PORT ?? '8001'
const API_BASE = process.env.E2E_API_BASE_URL ?? `http://localhost:${BACKEND_PORT}`
const E2E_EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const E2E_PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

const SKILL_SLUGS = [
  'docx-document',
  'xlsx-spreadsheet',
  'pptx-presentation',
  'patent-hwpx-generator',
] as const

type SkillSlug = (typeof SKILL_SLUGS)[number]

interface MarketplaceItem {
  id: string
  slug: string
  installation: {
    installed_resource_id?: string | null
  }
}

interface MarketplaceInstallation {
  installed_skill_id?: string | null
}

interface ModelRow {
  id: string
  provider: string
  model_name: string
}

interface AgentRow {
  id: string
}

interface ConversationRow {
  id: string
}

interface ArtifactRow {
  id: string
  display_name: string
  extension?: string | null
  status: string
}

interface RunRow {
  id: string
  status: string
}

interface E2ESetup {
  agentId: string
  conversationId: string
  csrfHeaders: Record<string, string>
}

interface ViewerCase {
  marker: string
  filename: string
  extension: string
  verify: (page: Page) => Promise<void>
}

async function failWithBody(
  label: string,
  response: { status: () => number; text: () => Promise<string> },
) {
  const body = await response.text().catch(() => '')
  throw new Error(`${label} failed (${response.status()}): ${body.slice(0, 800)}`)
}

async function loginApi(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API_BASE}/api/auth/login`, {
    data: { email: E2E_EMAIL, password: E2E_PASSWORD },
  })
  if (!res.ok()) {
    await failWithBody('E2E API login', res)
  }
  const body = (await res.json()) as { csrf_token: string }
  return { 'X-CSRF-Token': body.csrf_token }
}

async function getJson<T>(request: APIRequestContext, url: string): Promise<T> {
  const res = await request.get(url)
  if (!res.ok()) {
    await failWithBody(`GET ${url}`, res)
  }
  return (await res.json()) as T
}

async function postJson<T>(
  request: APIRequestContext,
  url: string,
  csrfHeaders: Record<string, string>,
  data: Record<string, unknown>,
): Promise<T> {
  const res = await request.post(url, { headers: csrfHeaders, data })
  if (!res.ok()) {
    await failWithBody(`POST ${url}`, res)
  }
  return (await res.json()) as T
}

async function deleteOk(
  request: APIRequestContext,
  url: string,
  csrfHeaders: Record<string, string>,
): Promise<void> {
  const res = await request.delete(url, { headers: csrfHeaders })
  if (!res.ok() && res.status() !== 404) {
    await failWithBody(`DELETE ${url}`, res)
  }
}

function yyyymmddSeoul(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
    .format(new Date())
    .replaceAll('-', '')
}

async function setupDocumentAgent(request: APIRequestContext): Promise<E2ESetup> {
  const csrfHeaders = await loginApi(request)
  const items = await getJson<MarketplaceItem[]>(
    request,
    `${API_BASE}/api/marketplace/items?resource_type=skill&source_kind=system_seed&limit=200`,
  )
  const itemBySlug = new Map(items.map((item) => [item.slug, item]))
  const missing = SKILL_SLUGS.filter((slug) => !itemBySlug.has(slug))
  expect(missing, 'document marketplace skills should be seeded').toEqual([])

  const installedSkillIds: string[] = []
  for (const slug of SKILL_SLUGS) {
    const item = itemBySlug.get(slug as SkillSlug)
    if (!item) throw new Error(`Missing marketplace item: ${slug}`)
    const installation = await postJson<MarketplaceInstallation>(
      request,
      `${API_BASE}/api/marketplace/items/${item.id}/install`,
      csrfHeaders,
      { install_mode: 'overwrite_existing' },
    )
    const installedSkillId =
      installation.installed_skill_id ?? item.installation.installed_resource_id
    if (!installedSkillId) throw new Error(`Marketplace install did not return a skill id: ${slug}`)
    installedSkillIds.push(installedSkillId)
  }

  const models = await getJson<ModelRow[]>(request, `${API_BASE}/api/models`)
  const scriptedModel = models.find(
    (model) =>
      model.provider === 'e2e_scripted' && model.model_name === 'document-artifact-scripted',
  )
  expect(scriptedModel, 'E2E scripted document model should be seeded').toBeTruthy()

  const agent = await postJson<AgentRow>(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name: `E2E Document Artifact Agent ${Date.now()}`,
    description: 'Generates deterministic document artifacts for E2E viewer verification.',
    system_prompt:
      'You are an E2E document artifact test agent. Use execute_in_skill exactly when the scripted model requests it.',
    model_id: scriptedModel?.id,
    tool_ids: [],
    mcp_tool_ids: [],
    skill_ids: installedSkillIds,
    sub_agent_ids: [],
    middleware_configs: [],
  })

  const conversation = await postJson<ConversationRow>(
    request,
    `${API_BASE}/api/agents/${agent.id}/conversations`,
    csrfHeaders,
    { title: 'Document Artifact Viewer E2E' },
  )

  return { agentId: agent.id, conversationId: conversation.id, csrfHeaders }
}

async function sendMessage(page: Page, text: string): Promise<void> {
  const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
  await expect(composer).toBeVisible()
  await composer.fill(text)
  await composer.press('Enter')
}

async function approveExecuteInSkill(page: Page): Promise<void> {
  await expect(page.getByText(/승인이 필요합니다|Approval Required/).last()).toBeVisible({
    timeout: 30_000,
  })
  const approveButton = page.getByRole('button', { name: /승인|Approve/ }).last()
  await expect(approveButton).toBeVisible()
  await approveButton.click()
  await expect(approveButton).toBeHidden({ timeout: 30_000 })
}

async function waitForArtifactByName(
  request: APIRequestContext,
  conversationId: string,
  filename: string,
): Promise<ArtifactRow> {
  let latest: ArtifactRow[] = []
  await expect
    .poll(
      async () => {
        latest = await getJson<ArtifactRow[]>(
          request,
          `${API_BASE}/api/conversations/${conversationId}/artifacts`,
        )
        return latest.some((artifact) => artifact.display_name === filename)
      },
      { timeout: 45_000, intervals: [500, 1000, 2000] },
    )
    .toBe(true)
  const artifact = latest.find((item) => item.display_name === filename)
  if (!artifact) throw new Error(`Artifact not found after polling: ${filename}`)
  return artifact
}

async function waitForActiveRun(
  request: APIRequestContext,
  conversationId: string,
): Promise<RunRow> {
  let latest: RunRow | null = null
  await expect
    .poll(
      async () => {
        latest = await getJson<RunRow | null>(
          request,
          `${API_BASE}/api/conversations/${conversationId}/runs/active`,
        )
        return latest?.status ?? null
      },
      { timeout: 20_000, intervals: [300, 500, 1000] },
    )
    .toMatch(/queued|running|canceling/)
  if (!latest) throw new Error('No active run found')
  return latest
}

async function waitForRunStatus(
  request: APIRequestContext,
  conversationId: string,
  runId: string,
  status: string,
): Promise<RunRow> {
  let latest: RunRow | null = null
  await expect
    .poll(
      async () => {
        latest = await getJson<RunRow>(
          request,
          `${API_BASE}/api/conversations/${conversationId}/runs/${runId}`,
        )
        return latest.status
      },
      { timeout: 20_000, intervals: [300, 500, 1000] },
    )
    .toBe(status)
  if (!latest) throw new Error(`Run ${runId} polling never returned a row`)
  return latest
}

async function openArtifactViewer(page: Page, filename: string): Promise<void> {
  await page.getByRole('button', { name: /파일 패널|Artifacts/ }).click()
  const artifactButton = page.getByRole('button', { name: new RegExp(filename) }).last()
  await expect(artifactButton).toBeVisible({ timeout: 20_000 })
  await artifactButton.click()
  await expect(
    artifactViewerPanel(page, filename).getByRole('heading', { name: filename }),
  ).toBeVisible()
}

function artifactViewerPanel(page: Page, filename: string): Locator {
  return page
    .getByRole('complementary')
    .filter({ has: page.getByRole('heading', { name: filename }) })
    .first()
}

async function visibleCanvasPixelCount(canvas: Locator): Promise<number> {
  return canvas.evaluate((element) => {
    const canvasElement = element as HTMLCanvasElement
    const context = canvasElement.getContext('2d')
    if (!context) return 0
    const width = Math.min(canvasElement.width, 640)
    const height = Math.min(canvasElement.height, 360)
    const data = context.getImageData(0, 0, width, height).data
    let count = 0
    for (let index = 0; index < data.length; index += 4) {
      const alpha = data[index + 3] ?? 0
      const red = data[index] ?? 255
      const green = data[index + 1] ?? 255
      const blue = data[index + 2] ?? 255
      if (alpha > 0 && (red < 245 || green < 245 || blue < 245)) count += 1
    }
    return count
  })
}

async function screenshot(page: Page, captureDir: string, filename: string): Promise<void> {
  await page.screenshot({
    path: path.join(captureDir, filename),
    fullPage: true,
    animations: 'disabled',
  })
}

async function locatorWidth(locator: Locator): Promise<number> {
  const box = await locator.boundingBox()
  if (!box) throw new Error('Expected locator to have a bounding box')
  return box.width
}

async function dragHorizontally(page: Page, locator: Locator, deltaX: number): Promise<void> {
  const box = await locator.boundingBox()
  if (!box) throw new Error('Expected drag handle to have a bounding box')
  const startX = box.x + box.width / 2
  const startY = box.y + Math.min(40, box.height / 2)
  await page.mouse.move(startX, startY)
  await page.mouse.down()
  await page.mouse.move(startX + deltaX, startY)
  await page.mouse.up()
}

async function verifyRightRailResize(page: Page, filename: string): Promise<void> {
  const rail = page.locator('[data-slot="chat-right-rail"]').first()
  const chatPanel = page.locator('section.moldy-panel').first()
  const handle = page.getByRole('separator', {
    name: /파일 패널 크기 조절|Resize files panel/,
  })

  await expect(artifactViewerPanel(page, filename)).toBeVisible()
  const initialRailWidth = await locatorWidth(rail)
  const initialChatWidth = await locatorWidth(chatPanel)

  await dragHorizontally(page, handle, -160)
  await expect.poll(() => locatorWidth(rail)).toBeGreaterThan(initialRailWidth + 120)
  await expect.poll(() => locatorWidth(chatPanel)).toBeLessThan(initialChatWidth - 120)
  const stableExpandedWidth = await locatorWidth(rail)

  await dragHorizontally(page, handle, 420)
  await expect.poll(() => locatorWidth(rail)).toBeLessThan(20)

  await page.getByRole('button', { name: /파일 패널|Artifacts/ }).click()
  await expect.poll(() => locatorWidth(rail)).toBeGreaterThan(stableExpandedWidth - 8)
}

test.describe('Document artifact viewers', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let setup: E2ESetup
  let captureDir: string
  let api: APIRequestContext

  test.beforeAll(async () => {
    api = await playwrightRequest.newContext({
      baseURL: API_BASE,
      storageState: { cookies: [], origins: [] },
    })
    setup = await setupDocumentAgent(api)
    captureDir = path.resolve(
      process.cwd(),
      '..',
      'output',
      'e2e-captures',
      `${yyyymmddSeoul()}-document-artifacts`,
    )
    await fs.mkdir(captureDir, { recursive: true })
  })

  test.afterAll(async () => {
    if (setup?.agentId) {
      await deleteOk(api, `${API_BASE}/api/agents/${setup.agentId}`, setup.csrfHeaders)
    }
    await api?.dispose()
  })

  test('generates and previews DOCX, XLSX, PPTX, and HWPX artifacts', async ({ page, errors }) => {
    const cases: ViewerCase[] = [
      {
        marker: 'E2E_DOCX',
        filename: 'moldy-docx-demo.docx',
        extension: 'docx',
        verify: async (viewerPage) => {
          await expect(
            artifactViewerPanel(viewerPage, 'moldy-docx-demo.docx')
              .getByText('Moldy 문서 생성 검증 보고서')
              .first(),
          ).toBeVisible({ timeout: 30_000 })
        },
      },
      {
        marker: 'E2E_XLSX',
        filename: 'moldy-xlsx-demo.xlsx',
        extension: 'xlsx',
        verify: async (viewerPage) => {
          const panel = artifactViewerPanel(viewerPage, 'moldy-xlsx-demo.xlsx')
          await expect(panel.getByText('검증요약').first()).toBeVisible({
            timeout: 30_000,
          })
          await expect(panel.getByText('JS runner').first()).toBeVisible()
        },
      },
      {
        marker: 'E2E_PPTX',
        filename: 'moldy-pptx-demo.pptx',
        extension: 'pptx',
        verify: async (viewerPage) => {
          const canvas = artifactViewerPanel(viewerPage, 'moldy-pptx-demo.pptx').getByTestId(
            'pptx-preview-canvas',
          )
          await expect(canvas).toBeVisible({ timeout: 30_000 })
          await expect
            .poll(() => visibleCanvasPixelCount(canvas), {
              timeout: 30_000,
              intervals: [500, 1000, 2000],
            })
            .toBeGreaterThan(100)
        },
      },
      {
        marker: 'E2E_HWPX',
        filename: 'moldy-patent-demo.hwpx',
        extension: 'hwpx',
        verify: async (viewerPage) => {
          const image = artifactViewerPanel(viewerPage, 'moldy-patent-demo.hwpx').getByRole('img', {
            name: 'moldy-patent-demo.hwpx',
          })
          await expect(image).toBeVisible({ timeout: 30_000 })
          await expect
            .poll(
              () =>
                image.evaluate((element) => {
                  const img = element as HTMLImageElement
                  return { width: img.naturalWidth, height: img.naturalHeight }
                }),
              { timeout: 30_000, intervals: [500, 1000, 2000] },
            )
            .toEqual(expect.objectContaining({ width: expect.any(Number) }))
          const size = await image.evaluate((element) => {
            const img = element as HTMLImageElement
            return img.naturalWidth * img.naturalHeight
          })
          expect(size).toBeGreaterThan(10_000)
        },
      },
    ]

    await page.goto(`/agents/${setup.agentId}/conversations/${setup.conversationId}`)
    await page.waitForLoadState('domcontentloaded')
    await expect(
      page.getByRole('heading', { name: /E2E Document Artifact Agent/ }).first(),
    ).toBeVisible()
    await expect(page.getByText('Document Artifact Viewer E2E').first()).toBeVisible()

    for (const item of cases) {
      await sendMessage(page, `${item.marker} 문서를 생성해서 artifact viewer로 확인해줘.`)
      await approveExecuteInSkill(page)
      const artifact = await waitForArtifactByName(api, setup.conversationId, item.filename)
      expect(artifact.extension).toBe(item.extension)
      await openArtifactViewer(page, item.filename)
      await item.verify(page)
      await screenshot(page, captureDir, `${item.extension}-viewer.png`)
    }

    await verifyRightRailResize(page, cases[cases.length - 1].filename)

    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  })

  test('marks generated artifacts failed when the run is canceled before final answer', async ({
    page,
    errors,
  }) => {
    const conversation = await postJson<ConversationRow>(
      api,
      `${API_BASE}/api/agents/${setup.agentId}/conversations`,
      setup.csrfHeaders,
      { title: 'Canceled Artifact E2E' },
    )

    await page.goto(`/agents/${setup.agentId}/conversations/${conversation.id}`)
    await page.waitForLoadState('domcontentloaded')
    await sendMessage(
      page,
      'E2E_DOCX E2E_ARTIFACT_SLOW_FINAL 문서를 생성한 뒤 최종 답변 중 취소할게.',
    )
    await approveExecuteInSkill(page)

    const artifact = await waitForArtifactByName(api, conversation.id, 'moldy-docx-demo.docx')
    expect(artifact.status).toBe('ready')

    const activeRun = await waitForActiveRun(api, conversation.id)
    const cancelResponse = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        response
          .url()
          .includes(`/api/conversations/${conversation.id}/runs/${activeRun.id}/cancel`),
    )
    await page.locator('[data-moldy-stop-button="true"]').click()
    const cancelResult = await cancelResponse
    expect(cancelResult.ok()).toBeTruthy()
    await waitForRunStatus(api, conversation.id, activeRun.id, 'canceled')

    const finalizedArtifact = await waitForArtifactByName(
      api,
      conversation.id,
      'moldy-docx-demo.docx',
    )
    expect(finalizedArtifact.status).toBe('failed')
    await screenshot(page, captureDir, 'canceled-artifact-failed.png')

    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  })
})
