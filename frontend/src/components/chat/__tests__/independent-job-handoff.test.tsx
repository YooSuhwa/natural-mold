import { webcrypto } from 'node:crypto'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor } from '../../../../tests/test-utils'
import { independentJobsApi } from '@/lib/api/independent-chat-jobs'
import type { IndependentChatJob } from '@/lib/types/independent-chat-job'
import { IndependentJobHandoff } from '../independent-job-handoff'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  sessionStorage.clear()
})

it('hands off only the authorized child on its original run even if a sibling has the same display name', async () => {
  vi.stubGlobal('crypto', webcrypto)
  vi.spyOn(independentJobsApi, 'list').mockResolvedValue({
    enabled: true,
    parent_job: null,
    jobs: [],
    targets: [
      { id: 'first', name: '조사 담당' },
      { id: 'second', name: '조사 담당' },
    ],
  })
  const start = vi
    .spyOn(independentJobsApi, 'start')
    .mockResolvedValue({ id: 'job', parent_conversation_id: 'chat' } as IndependentChatJob)
  render(
    <IndependentJobHandoff
      conversationId="chat"
      runId="original"
      childTaskId="task-2"
      childAgentId="second"
    />,
  )
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: '별도 작업으로 계속' }))
  await user.type(screen.getByRole('textbox', { name: '맡길 일' }), '자료 마무리')
  await user.click(screen.getByRole('button', { name: '작업 시작' }))
  await waitFor(() =>
    expect(start).toHaveBeenCalledWith(
      'chat',
      'original',
      expect.objectContaining({ target_id: 'second', source_child_task_id: 'task-2' }),
    ),
  )
})

it('hides the handoff for a source task without a proven registered agent or eligible current target', async () => {
  const list = vi
    .spyOn(independentJobsApi, 'list')
    .mockResolvedValue({ enabled: true, parent_job: null, jobs: [], targets: [] })
  const view = render(
    <IndependentJobHandoff
      conversationId="chat"
      runId="old"
      childTaskId="task"
      childAgentId={null}
    />,
  )
  expect(list).not.toHaveBeenCalled()
  view.rerender(
    <IndependentJobHandoff
      conversationId="chat"
      runId="old"
      childTaskId="task"
      childAgentId="revoked"
    />,
  )
  await waitFor(() => expect(list).toHaveBeenCalled())
  expect(screen.queryByRole('button', { name: '별도 작업으로 계속' })).toBeNull()
})
