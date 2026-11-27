import { createHash, randomUUID } from 'node:crypto'
import type { APIRequestContext } from '@playwright/test'

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

const filename = 'e2e-runtime-policy-artifact.md'
const expected = Buffer.from('E2E runtime artifact final content.\n')
const interval = { timeout: 60_000, intervals: [250, 750, 1500] }

function object(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw new Error('Expected an API object')
  return value
}

async function inbox(request: APIRequestContext, path: string) {
  return records(object(await apiGetJson(request, API_BASE + path)).items, path)
}

async function workNotices(request: APIRequestContext, goalId: string, approvalId: string) {
  const rows = await inbox(request, '/api/notifications?limit=50')
  const approval = rows.filter(
    (row) =>
      row.kind === 'approval.requested' && isRecord(row.target) && row.target.id === approvalId,
  )
  const completed: { id: string; destination: Record<string, unknown> }[] = []
  for (const row of rows) {
    if (row.kind !== 'chat_goal.completed' || !isRecord(row.target) || !row.target.accessible)
      continue
    const id = stringField(row, 'id', 'notice')
    const destination = object(
      await apiGetJson(request, `${API_BASE}/api/notifications/${id}/destination`),
    )
    if (destination.goal_id === goalId) completed.push({ id, destination })
  }
  return { approval, completed }
}

test('the approval inbox resumes a goal and delivers one separately verified completion', async ({
  request,
}, testInfo) => {
  test.skip(
    process.env.E2E_CHAT_GOAL_NOTIFICATIONS !== '1',
    'Requires the goal and notification enabled scripted lane',
  )
  test.setTimeout(170_000)
  const headers = await loginApi(request)
  const model = records(await apiGetJson(request, `${API_BASE}/api/models`), 'models').find(
    isSeededScriptedModel,
  )
  if (!model) throw new Error('Missing seeded scripted model')
  const agent = object(
    await apiPostJson(request, `${API_BASE}/api/agents`, headers, {
      name: `E2E approved goal ${randomUUID()}`,
      model_id: model.id,
      system_prompt: 'Write the requested conversation file.',
      runtime_policy: {
        version: 1,
        filesystem: { mode: 'artifact_write' },
        todo: { enabled: true },
        summarization: { mode: 'auto' },
        tool_approval: {
          tools: {
            'builtin:write_file': { mode: 'always' },
          },
        },
      },
    }),
  )
  const agentId = stringField(agent, 'id', 'agent')
  try {
    const conversation = object(
      await apiPostJson(request, `${API_BASE}/api/agents/${agentId}/conversations`, headers, {
        title: 'Goal waiting for one approval',
      }),
    )
    const conversationId = stringField(conversation, 'id', 'conversation')
    const base = `${API_BASE}/api/conversations/${conversationId}`
    const created = object(
      await apiJson(
        await request.put(`${base}/goal`, {
          headers,
          data: {
            expected_revision: 0,
            title: 'Approve the write, then verify its final contents',
            criteria: [{ kind: 'nonempty_file', logical_path: filename }],
          },
        }),
        'create goal',
      ),
    )
    const goalId = stringField(object(created.goal), 'id', 'goal')
    const started = object(
      await apiPostJson(request, `${base}/langgraph/threads/${conversationId}/commands`, headers, {
        id: randomUUID(),
        method: 'run.start',
        params: {
          assistant_id: agentId,
          client_request_id: randomUUID(),
          input: { messages: [{ role: 'user', content: 'E2E_APPROVED_GOAL_FILE' }] },
        },
      }),
    )
    const runId = stringField(object(started.result), 'run_id', 'first run')
    await waitForRunStatus(request, conversationId, runId, 'interrupted')
    const pendingApprovals = async () =>
      (await inbox(request, '/api/approvals?status=pending')).filter(
        (row) => row.conversation_id === conversationId,
      )
    await expect.poll(async () => (await pendingApprovals()).length, interval).toBe(1)
    const [pending] = await pendingApprovals()
    const approvalId = stringField(pending, 'id', 'approval')
    expect(pending).toMatchObject({ kind: 'tool', source: 'chat', status: 'pending' })
    expect(records(pending.actions, 'approval actions').map((action) => action.name)).toEqual([
      'write_file',
    ])
    const goalState = async () => object(object(await apiGetJson(request, `${base}/goal`)).goal)
    await expect
      .poll(goalState, interval)
      .toMatchObject({ status: 'blocked', blocked_reason: 'approval_pending' })
    await expect
      .poll(async () => (await workNotices(request, goalId, approvalId)).approval.length, interval)
      .toBe(1)
    const before = await workNotices(request, goalId, approvalId)
    expect(before.completed).toHaveLength(0)
    const approvalNoticeId = stringField(before.approval[0], 'id', 'approval notice')
    expect(before.approval[0].read_at).toBeNull()
    const markRead = await request.post(`${API_BASE}/api/notifications/${approvalNoticeId}/read`, {
      headers,
    })
    expect(markRead.status()).toBe(204)
    const [marked] = (await workNotices(request, goalId, approvalId)).approval
    const readAt = stringField(marked, 'read_at', 'acknowledged notice')
    expect(records(await apiGetJson(request, `${base}/artifacts`), 'artifacts')).toHaveLength(0)
    const decisionPath = `${API_BASE}/api/approvals/${approvalId}/decision`
    const decision = { decisions: [{ type: 'approve' }] }
    const approved = object(await apiPostJson(request, decisionPath, headers, decision))
    expect(approved).toMatchObject({ id: approvalId, status: 'approved', channel: 'inbox' })
    expect((await request.post(decisionPath, { headers, data: decision })).status()).toBe(409)
    await expect.poll(goalState, interval).toMatchObject({ status: 'completed' })
    await expect
      .poll(async () => (await workNotices(request, goalId, approvalId)).completed.length, interval)
      .toBe(1)
    const { approval, completed } = await workNotices(request, goalId, approvalId)
    expect(approval.map((item) => [item.id, item.read_at])).toEqual([[approvalNoticeId, readAt]])
    expect(await pendingApprovals()).toHaveLength(0)
    const [{ id: completionId, destination }] = completed
    const resumeId = stringField(destination, 'run_id', 'completion')
    const resumed = object(await apiGetJson(request, `${base}/runs/${resumeId}`))
    expect(resumed).toMatchObject({ parent_run_id: runId, status: 'completed', source: 'resume' })
    const checks = records(object((await goalState()).last_evidence).checks, 'verified result')
    const digest = createHash('sha256').update(expected).digest('hex')
    expect(checks).toHaveLength(1)
    expect(checks[0]).toMatchObject({
      passed: true,
      sha256: digest,
      version_id: destination.version_id,
    })
    const bytes = await request.get(`${API_BASE}/api/notifications/${completionId}/result`)
    expect(bytes.status()).toBe(200)
    expect(await bytes.body()).toEqual(expected)
    await testInfo.attach('approval-and-result', {
      contentType: 'application/json',
      body: JSON.stringify({
        goalId,
        conversationId,
        runId,
        resumeId,
        approvalId,
        approvalNotice: approvalNoticeId,
        readAt,
        completionId,
        versionId: destination.version_id,
        sha256: digest,
      }),
    })
  } finally {
    await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, headers)
  }
})
