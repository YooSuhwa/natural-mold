import { createHash, randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'

import {
  API_BASE,
  apiDeleteOk,
  apiGetJson,
  apiJson,
  apiPostJson,
  expect,
  isRecord,
  loginApi,
  test,
} from './fixtures'
import { isSeededScriptedModel } from './helpers/scripted-model'
import { records, stringField, waitForRunStatus } from './langgraph-v3-helpers'

const file = 'e2e-runtime-policy-artifact.md'
const finalText = 'E2E runtime artifact final content.\n'

test('a closed chat delivers one goal notification and downloads its verified file version', async ({
  page,
  context,
  request,
}) => {
  test.skip(
    process.env.E2E_CHAT_GOAL_NOTIFICATIONS !== '1',
    'Requires the goal and notification enabled scripted lane',
  )
  test.setTimeout(180_000)

  const headers = await loginApi(request)
  const models = records(await apiGetJson(request, `${API_BASE}/api/models`), 'models')
  const model = models.find(isSeededScriptedModel)
  if (!model) throw new Error('Missing seeded scripted model')
  const agent = await apiPostJson(request, `${API_BASE}/api/agents`, headers, {
    name: `E2E goal notification ${randomUUID()}`,
    model_id: model.id,
    system_prompt: 'Create the requested conversation artifact.',
    runtime_policy: {
      version: 1,
      filesystem: { mode: 'artifact_write' },
      todo: { enabled: true },
      summarization: { mode: 'auto' },
    },
  })
  if (!isRecord(agent)) throw new Error('Missing agent')
  const agentId = stringField(agent, 'id', 'agent')
  try {
    const created = await apiPostJson(
      request,
      `${API_BASE}/api/agents/${agentId}/conversations`,
      headers,
      { title: 'Goal notification test' },
    )
    if (!isRecord(created)) throw new Error('Missing conversation')
    const conversationId = stringField(created, 'id', 'conversation')
    const base = `${API_BASE}/api/conversations/${conversationId}`
    const chatPath = `/agents/${agentId}/conversations/${conversationId}`
    const goal = await apiJson(
      await request.put(`${base}/goal`, {
        headers,
        data: {
          expected_revision: 0,
          title: 'Create the reviewed Markdown file',
          criteria: [{ kind: 'nonempty_file', logical_path: file }],
        },
      }),
      'create goal',
    )
    if (!isRecord(goal) || !isRecord(goal.goal)) throw new Error('Missing goal')
    const goalId = stringField(goal.goal, 'id', 'goal')
    const chatTab = await context.newPage()
    await chatTab.goto(chatPath)
    await chatTab.close()

    const start = await apiPostJson(
      request,
      `${base}/langgraph/threads/${conversationId}/commands`,
      headers,
      {
        id: randomUUID(),
        method: 'run.start',
        params: {
          assistant_id: agentId,
          client_request_id: randomUUID(),
          input: { messages: [{ role: 'user', content: 'E2E_RUNTIME_FILESYSTEM_ARTIFACT_WRITE' }] },
        },
      },
    )
    if (!isRecord(start) || !isRecord(start.result)) throw new Error('Missing accepted run')
    const runId = stringField(start.result, 'run_id', 'accepted run')
    await waitForRunStatus(request, conversationId, runId, 'completed')

    let noticeId: string | undefined
    let versionId: string | undefined
    await expect
      .poll(
        async () => {
          const view = await apiGetJson(request, `${base}/goal`)
          if (!isRecord(view) || !isRecord(view.goal) || view.goal.status !== 'completed') return 0
          const evidence = view.goal.last_evidence
          if (!isRecord(evidence)) return 0
          const check = records(evidence.checks, 'goal checks').find(
            (check) => check.passed && check.logical_path === file,
          )
          if (!check) return 0
          expect(check.sha256).toBe(createHash('sha256').update(finalText).digest('hex'))
          versionId = stringField(check, 'version_id', 'verified file')
          const list = await apiGetJson(request, `${API_BASE}/api/notifications`)
          if (!isRecord(list)) throw new Error('Missing notifications')
          const rows = records(list.items, 'notifications').filter(
            (row) =>
              row.kind === 'chat_goal.completed' && isRecord(row.target) && row.target.accessible,
          )
          const matching = []
          for (const row of rows) {
            const id = stringField(row, 'id', 'notification')
            const destination = await apiGetJson(
              request,
              `${API_BASE}/api/notifications/${id}/destination`,
            )
            if (
              isRecord(destination) &&
              destination.goal_id === goalId &&
              destination.version_id === versionId &&
              destination.run_id === runId
            )
              matching.push(id)
          }
          noticeId = matching[0]
          return matching.length
        },
        { timeout: 65_000, intervals: [300, 750, 1500] },
      )
      .toBe(1)
    if (!noticeId || !versionId) throw new Error('Missing verified notification')

    await page.goto('/agents')
    await page.getByTestId('notifications-bell').click()
    const row = page.locator(`[data-notification-id="${noticeId}"]`)
    await expect(row).toContainText(/목표를 완료했어요|Your goal is complete/)
    const downloadEvent = page.waitForEvent('download')
    await row.click()
    const download = await downloadEvent
    expect(download.suggestedFilename()).toBe(file)
    const path = await download.path()
    if (!path) throw new Error('Missing downloaded file')
    expect(await readFile(path, 'utf8')).toBe(finalText)
    await expect(page).toHaveURL(new RegExp(`${chatPath}#conversation-goal$`))
    await page.reload()
    await page.getByTestId('notifications-bell').click()
    await expect(row).toBeVisible()
    await expect(row).not.toHaveAttribute('data-unread', 'true')
  } finally {
    await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, headers)
  }
})
