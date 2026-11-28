import { webcrypto } from 'node:crypto'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor, within } from '../../../../tests/test-utils'
import type { IndependentChatJob } from '@/lib/types/independent-chat-job'
import { independentJobsApi } from '@/lib/api/independent-chat-jobs'
import { IndependentJobPanel } from '../independent-job-panel'

const parentId = 'c1c67576-9d6f-4909-9ceb-a1943003369a'
const runId = '4e84f7d1-50be-43de-8c90-76304d4e60e0'
const job: IndependentChatJob = {
  id: 'c2305194-4b16-4c2b-88c4-69cb33198ba1',
  parent_agent_id: '710f2e6b-cb27-4e79-b71f-49deb06471ee',
  parent_conversation_id: parentId,
  parent_run_id: runId,
  child_agent_id: '48cd7b2e-a918-403c-a796-471cd63640d5',
  child_agent_name: '조사 담당',
  child_conversation_id: '7a587681-b123-409b-9359-08004986fa8c',
  child_run_id: 'fd930aa4-d110-4f8a-8fea-9f0116ed8777',
  status: 'waiting_for_approval',
}
const base = {
  enabled: true,
  parent_job: null,
  jobs: [],
  targets: [{ id: job.child_agent_id, name: job.child_agent_name }],
}

describe('IndependentJobPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    sessionStorage.clear()
    vi.stubGlobal('crypto', webcrypto)
  })

  it('keeps the request ID when a start response is lost and the user reloads, then clears it after confirmation', async () => {
    const start = vi
      .spyOn(independentJobsApi, 'start')
      .mockRejectedValueOnce(new TypeError('lost connection'))
      .mockResolvedValueOnce(job)
    const onJob = vi.fn()
    const panel = (
      <IndependentJobPanel conversationId={parentId} runId={runId} list={base} onJob={onJob} />
    )
    const user = userEvent.setup()
    const original = render(panel)
    async function request() {
      await user.click(screen.getByRole('button', { name: '별도 작업으로 계속' }))
      await user.type(screen.getByRole('textbox', { name: '맡길 일' }), '지역별 가격 조사')
      await user.click(screen.getByRole('button', { name: '작업 시작' }))
    }

    await request()
    expect(await screen.findByRole('alert')).toBeVisible()
    expect(Object.keys(sessionStorage).join()).not.toContain('지역별')
    original.unmount()
    render(panel)
    await request()
    await waitFor(() => expect(onJob).toHaveBeenCalledWith(job))
    expect(start).toHaveBeenCalledTimes(2)
    expect(start.mock.calls[1]).toEqual(start.mock.calls[0])
    expect(start.mock.calls[0][2]).toMatchObject({
      request_id: expect.any(String),
      target_id: job.child_agent_id,
      instruction: '지역별 가격 조사',
    })
    expect(
      Object.keys(sessionStorage).filter((key) => key.startsWith('moldy.independent-job:')),
    ).toHaveLength(0)
  })

  it('restores separate result and approval links and cancels only the selected child', async () => {
    const done = {
      ...job,
      id: '43e981ec-cb5e-4126-b5e0-17b88b05a9d4',
      child_conversation_id: '383edbfc-80a6-47de-922e-abd61aa8b64d',
      status: 'completed' as const,
    }
    const cancel = vi
      .spyOn(independentJobsApi, 'cancel')
      .mockResolvedValue({ ...job, status: 'canceled' })
    render(
      <IndependentJobPanel
        conversationId={parentId}
        runId={runId}
        list={{ ...base, enabled: false, jobs: [done, job] }}
        onJob={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: '별도 작업으로 계속' })).toBeNull()
    expect(screen.getByRole('link', { name: '결과 열기' })).toHaveAttribute(
      'href',
      `/agents/${done.child_agent_id}/conversations/${done.child_conversation_id}`,
    )
    const awaiting = screen.getByRole('link', { name: '응답·승인 요청 열기' })
    expect(awaiting).toHaveAttribute(
      'href',
      `/agents/${job.child_agent_id}/conversations/${job.child_conversation_id}`,
    )
    const row = awaiting.closest('li')
    if (!row) throw new Error('Missing job row')
    await userEvent.setup().click(within(row).getByRole('button', { name: '이 작업 중지' }))
    expect(cancel).toHaveBeenCalledExactlyOnceWith(parentId, job.id)
  })

  it('offers a parent link on the child page and suppresses another delegation', () => {
    render(
      <IndependentJobPanel
        conversationId={job.child_conversation_id}
        runId={job.child_run_id}
        list={{ ...base, parent_job: job }}
        onJob={vi.fn()}
      />,
    )
    expect(screen.getByRole('link', { name: '원래 대화 열기' })).toHaveAttribute(
      'href',
      `/agents/${job.parent_agent_id}/conversations/${parentId}`,
    )
    expect(screen.queryByRole('button', { name: '별도 작업으로 계속' })).toBeNull()
  })
})
