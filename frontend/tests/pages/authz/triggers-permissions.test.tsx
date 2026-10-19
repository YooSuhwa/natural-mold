/**
 * H-05 / plan 13.9 row 3: the agent's schedule tab lists the caller's own triggers
 * (server) and offers creation only with `can_run`; BLOCKED stop reasons show as text.
 */
import { render, screen } from '../../test-utils'
import { TriggersTab } from '@/app/agents/[agentId]/settings/_components/triggers-tab'
import { AgentAccessProvider, agentAccessOf } from '@/components/agent/agent-access-context'
import { ScheduleRunList } from '@/features/schedules/components/schedule-run-list'
import { stopReasonOf } from '@/features/schedules/components/trigger-stop-reason'
import type { AgentTrigger, TriggerRun } from '@/lib/types'
import { mockTriggerList } from '../../mocks/fixtures'

const triggers = vi.hoisted(() => ({ data: [] as AgentTrigger[] }))
vi.mock('@/lib/hooks/use-triggers', () => ({
  useTriggers: () => ({ data: triggers.data }),
  useCreateTrigger: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateTrigger: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-conversations', () => ({ useConversations: () => ({ data: [] }) }))
vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))

const LEVELS: [string, string[] | undefined, boolean][] = [
  ['owner (legacy body)', undefined, true],
  [
    'owner',
    ['can_run', 'can_view_config', 'can_edit', 'can_share', 'can_deploy', 'can_delete'],
    true,
  ],
  ['editor', ['can_run', 'can_view_config', 'can_clone', 'can_edit'], true],
  ['viewer', ['can_run', 'can_view_config', 'can_clone'], true],
  // Runners never reach the settings route (gate); a config view without run cannot create.
  ['config without run', ['can_view_config'], false],
]

describe.each(LEVELS)('schedule tab as %s', (_label, permissions, canCreate) => {
  it(canCreate ? 'offers creation' : 'hides creation', () => {
    triggers.data = []
    render(
      <AgentAccessProvider access={agentAccessOf({ my_permissions: permissions })}>
        <TriggersTab agentId="agent-1" onRequestDelete={vi.fn()} />
      </AgentAccessProvider>,
    )
    expect(!!screen.queryByTestId('trigger-add-button')).toBe(canCreate)
  })
})

describe('stop reasons (F-08 reason_code)', () => {
  it('reads reason codes and BLOCKED last_error, ignores free text', () => {
    expect(stopReasonOf('owner_left')).toBe('owner_left')
    expect(stopReasonOf(null, 'permission_revoked: AGENT_NOT_FOUND')).toBe('permission_revoked')
    expect(stopReasonOf(null, 'timeout: model did not answer')).toBeNull()
    expect(stopReasonOf(null, null)).toBeNull()
  })

  it('shows the trigger stop reason in the schedule tab', () => {
    triggers.data = [{ ...mockTriggerList[0], last_error: 'permission_revoked: AGENT_NOT_FOUND' }]
    render(<TriggersTab agentId="agent-1" onRequestDelete={vi.fn()} />)
    expect(screen.getByText('정지 사유: 실행 권한이 회수되었어요')).toBeInTheDocument()
  })

  it('shows a run reason in the schedule center run list', () => {
    const run = {
      id: 'run-1',
      trigger_id: 't-1',
      agent_id: 'agent-1',
      user_id: 'u-1',
      conversation_id: null,
      status: 'failed',
      source: 'scheduled',
      input_message: 'hi',
      error_message: 'owner_left',
      reason_code: 'owner_left',
      output_preview: null,
      duration_ms: null,
      thread_id: null,
      checkpoint_id: null,
      trace_id: null,
      started_at: '2026-10-17T00:00:00Z',
      finished_at: null,
      created_at: '2026-10-17T00:00:00Z',
    } as TriggerRun & { reason_code: string }
    render(
      <ScheduleRunList
        runs={[run]}
        loading={false}
        labels={{ runNow: 'now', scheduled: 'sched', openConversation: 'open', empty: 'none' }}
        formatDate={(v) => v ?? ''}
      />,
    )
    expect(screen.getByText('정지 사유: 예약을 만든 사람이 조직을 떠났어요')).toBeInTheDocument()
  })
})
