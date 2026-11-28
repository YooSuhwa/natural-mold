// Run from delegation_approval.py while the existing two frontends serve the same suspended room.
const fs = require('node:fs')
const path = require('node:path')
const { createRequire } = require('node:module')

let stage = 'configuration'
class CheckFailed extends Error {}

function check(condition, reason) {
  if (!condition) throw new CheckFailed(reason)
}

function localOrigin(address) {
  const url = new URL(address)
  check(
    url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname),
    'expected loopback origin',
  )
  return url.origin
}

function matchesResume(body, f) {
  const slots = body?.resume
  const decisions = slots?.[0]?.payload?.decisions
  return (
    body?.threadId === f.threadId &&
    body.protocolVersion === '1.0' &&
    typeof body.runId === 'string' &&
    body.runId !== f.sourceRunId &&
    slots?.length === 1 &&
    slots[0].interruptId === f.interruptId &&
    slots[0].status === 'resolved' &&
    decisions?.length === 1 &&
    decisions[0].type === 'approve'
  )
}

async function observeStream(page, commandPath) {
  // Chromium may release SSE bytes before response.body(). Tee the actual fetch without consuming it.
  await page.addInitScript((pathname) => {
    const fetch = window.fetch.bind(window)
    window.approvalStreams = new Map()
    window.fetch = async (input, init) => {
      const response = await fetch(input, init)
      const method = init?.method ?? (input instanceof Request ? input.method : 'GET')
      if (method !== 'POST' || !response.ok || new URL(response.url).pathname !== pathname)
        return response
      const id = response.headers.get('X-Run-Id')
      if (!id || !response.headers.get('content-type')?.includes('text/event-stream'))
        return response
      const reader = response.clone().body?.getReader()
      if (!reader) return response
      const record = { text: '', done: false }
      window.approvalStreams.set(id, record)
      void (async () => {
        const decoder = new TextDecoder()
        try {
          for (;;) {
            const { value, done } = await reader.read()
            if (done) break
            record.text += decoder.decode(value, { stream: true })
          }
        } catch {
          // The parent validates all retained frames, including the mandatory terminal, with the SDK.
        } finally {
          record.text += decoder.decode()
          record.done = true
          reader.releaseLock()
        }
      })()
      return response
    }
  }, commandPath)
}

async function run() {
  const f = JSON.parse(fs.readFileSync(0, 'utf8'))
  const requireUI = createRequire(
    path.resolve(process.env.MOLDY_BACKEND_SOURCE_ROOT, '../frontend/package.json'),
  )
  const { chromium, expect } = requireUI('@playwright/test')
  const api = localOrigin(process.env.E2E_API_BASE_URL)
  const legacy = localOrigin(process.env.E2E_AGUI_LEGACY_FRONTEND_URL)
  const standard = localOrigin('http://localhost:' + process.env.E2E_FRONTEND_PORT)
  check(standard !== legacy, 'frontends must be distinct')
  const origins = new Set([api, legacy, standard])
  const base = '/api/conversations/' + encodeURIComponent(f.threadId)
  const room =
    '/agents/' + encodeURIComponent(f.agentId) + '/conversations/' + encodeURIComponent(f.threadId)
  const command = base + '/ag-ui/runs'
  let allowDecision = false
  let attempts = 0
  const browser = await chromium.launch()
  try {
    async function login(worker) {
      const ctx = await browser.newContext({
        locale: 'ko-KR',
        viewport: { width: 1440, height: 960 },
        extraHTTPHeaders: { 'x-moldy-e2e-worker': worker },
      })
      await ctx.addInitScript(() => {
        sessionStorage.setItem('moldy.onboarding_dismissed', '1')
        sessionStorage.setItem('moldy.super_user_welcomed', '1')
      })
      await ctx.route(/^https?:\/\//, async (route) => {
        const request = route.request()
        const { origin, pathname } = new URL(request.url())
        if (!origins.has(origin)) return route.abort()
        if (request.method() === 'POST' && pathname.startsWith(base + '/')) {
          const legacyCommand =
            pathname.endsWith('/commands') &&
            ['run.start', 'run.cancel', 'input.respond'].includes(request.postDataJSON()?.method)
          const childCommand = pathname.startsWith(base + '/child-tasks/')
          if (
            pathname === command ||
            legacyCommand ||
            childCommand ||
            pathname.endsWith('/cancel')
          ) {
            attempts++
            if (
              !allowDecision ||
              attempts !== 1 ||
              pathname !== command ||
              !matchesResume(request.postDataJSON(), f)
            )
              return route.abort()
          }
        }
        return route.continue()
      })
      const response = await ctx.request.post(api + '/api/auth/login', {
        data: { email: process.env.E2E_USER_EMAIL, password: process.env.E2E_USER_PASSWORD },
      })
      check(response.ok(), 'session authentication failed')
      return ctx
    }
    const alphaChip = (page) =>
      page
        .locator('button[data-moldy-team-chip][title="alpha"]')
        .filter({ hasText: f.alphaTaskId.slice(0, 8) })
    const betaChip = (page) =>
      page
        .locator('button[data-moldy-team-chip][title="beta"]')
        .filter({ hasText: f.betaTaskId.slice(0, 8) })
    const childPanel = (page, taskId) =>
      page.getByRole('region', { name: new RegExp(taskId.slice(0, 8) + '$') })
    stage = 'legacy-pending'
    const old = await login('a')
    const first = await old.newPage()
    await first.goto(legacy + room, { waitUntil: 'domcontentloaded' })
    stage = 'legacy-sibling-chips'
    await expect(betaChip(first)).toBeVisible({ timeout: 20_000 })
    await alphaChip(first).click({ timeout: 15_000 })
    stage = 'legacy-target-panel'
    await expect(childPanel(first, f.alphaTaskId).getByRole('status')).toContainText(
      /승인 대기 중|Awaiting approval/,
      { timeout: 15_000 },
    )
    await old.close()
    check(attempts === 0, 'observation attempted a command')

    stage = 'standard-pending'
    const fresh = await login('b')
    const page = await fresh.newPage()
    await observeStream(page, command)
    const waitRead = (pathname) =>
      page.waitForResponse(
        (r) => r.ok() && r.request().method() === 'GET' && new URL(r.url()).pathname === pathname,
        { timeout: 25_000 },
      )
    const [snapshot, detail] = await Promise.all([
      waitRead(base + '/ag-ui/snapshot').then((r) => r.json()),
      waitRead(base + '/child-tasks/' + f.alphaTaskId + '/wait').then((r) => r.json()),
      page.goto(standard + room, { waitUntil: 'domcontentloaded' }),
    ])
    const target = snapshot.subagents.find((s) => s.childTaskId === f.alphaTaskId)
    const sibling = snapshot.subagents.find((s) => s.childTaskId === f.betaTaskId)
    const interrupts = snapshot.interrupts
    check(
      target?.status === 'interrupted' &&
        target.interruptIds?.length === 1 &&
        target.interruptIds[0] === f.interruptId,
      'target interrupt did not survive reload',
    )
    check(
      sibling?.status === 'completed' && sibling.interruptIds?.length === 0,
      'sibling inherited an interrupt',
    )
    check(
      interrupts?.length === 1 &&
        interrupts[0].id === f.interruptId &&
        interrupts[0].subagentRunId === target.subagentRunId &&
        (!f.subagentRunId || target.subagentRunId === f.subagentRunId),
      'snapshot lost the approval owner',
    )
    check(
      detail.id === f.alphaTaskId &&
        detail.status === 'awaiting_approval' &&
        detail.tool_call_id === 'delegate-alpha',
      'managed detail does not admit this target',
    )
    const approve = page.getByTestId('approval-approve-button').filter({ visible: true })
    stage = 'standard-visible-approval'
    await expect(approve).toHaveCount(1, { timeout: 15_000 })
    check(attempts === 0, 'new tab attempted a command on attachment')

    stage = 'standard-decision'
    allowDecision = true
    const [response] = await Promise.all([
      page.waitForResponse(
        (r) => r.request().method() === 'POST' && new URL(r.url()).pathname === command,
        { timeout: 25_000 },
      ),
      approve.click(),
    ])
    allowDecision = false
    const request = response.request().postDataJSON()
    const headers = {
      'content-type': await response.headerValue('content-type'),
      'x-run-id': await response.headerValue('x-run-id'),
    }
    check(
      response.status() === 200 &&
        headers['x-run-id'] === request.runId &&
        headers['content-type']?.includes('text/event-stream'),
      'approval stream headers failed',
    )
    stage = 'standard-decision-stream'
    const result = await page.waitForFunction(
      (id) => {
        const record = window.approvalStreams.get(id)
        return record?.done ? record.text : undefined
      },
      request.runId,
      { timeout: 60_000 },
    )
    const sse = await result.jsonValue()

    stage = 'standard-child-results'
    await expect(
      page
        .getByText(f.expectedParentResult ?? 'Both local checks have finished.', { exact: true })
        .filter({ visible: true })
        .first(),
    ).toBeVisible({ timeout: 20_000 })
    await expect(approve).toHaveCount(0)
    for (const [chip, id, text] of [
      [
        alphaChip(page),
        f.alphaTaskId,
        f.expectedAlphaResult ?? 'report alpha, addressed instructions 1',
      ],
      [betaChip(page), f.betaTaskId, 'report beta, addressed instructions 0'],
    ]) {
      stage = id === f.alphaTaskId ? 'standard-target-result' : 'standard-sibling-result'
      await chip.click({ timeout: 15_000 })
      await expect(childPanel(page, id).getByText(text, { exact: true })).toBeVisible({
        timeout: 20_000,
      })
    }
    check(attempts === 1, 'unexpected number of run commands')
    await fresh.close()
    return {
      request,
      status: response.status(),
      headers,
      sse,
      observations: {
        authenticatedContexts: 2,
        closedLegacyPendingTab: true,
        approvalButtons: 1,
        runCommands: attempts,
        targetTaskId: detail.id,
        siblingTaskId: sibling.childTaskId,
        targetSubagentRunId: target.subagentRunId,
        visibleParentResult: true,
        visibleTargetResult: true,
        visibleSiblingResult: true,
      },
    }
  } finally {
    await browser.close()
  }
}
run()
  .then((result) => process.stdout.write(JSON.stringify(result)))
  .catch((error) => {
    const reason = error instanceof CheckFailed ? error.message : 'browser operation failed'
    process.stderr.write(stage + ': ' + reason + '\n')
    process.exitCode = 1
  })
