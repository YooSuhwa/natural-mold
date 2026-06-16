import { skillBuilderApi } from '@/lib/api/skill-builder'
import { streamSkillBuilderMessage } from '@/lib/sse/stream-skill-builder-message'
import { StrictMode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SkillBuilderDialog } from '../skill-builder-dialog'
import { render, screen, userEvent, waitFor } from '../../../../tests/test-utils'
import type { SkillBuilderSession } from '@/lib/types/skill-builder'

type MockSession = {
  readonly data: { readonly is_super_user: boolean } | null
  readonly isPending: boolean
}

const mockUseSession = vi.hoisted(() => vi.fn<() => MockSession>())

vi.mock('@/lib/api/skill-builder', () => ({
  skillBuilderApi: {
    start: vi.fn(),
    get: vi.fn(),
    confirm: vi.fn(),
    runEvaluation: vi.fn(),
  },
}))

vi.mock('@/lib/sse/stream-skill-builder-message', () => ({
  streamSkillBuilderMessage: vi.fn(),
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: mockUseSession,
}))

const session: SkillBuilderSession = {
  id: 'session-1',
  user_id: 'user-1',
  user_request: '회의록 스킬',
  mode: 'create',
  status: 'review',
  current_phase: 1,
  draft_package: null,
  validation_result: null,
  compatibility_result: null,
  changelog_draft: null,
  eval_result: null,
  trigger_eval_result: null,
  finalized_skill_id: null,
  error_message: null,
  created_at: '2026-06-15T00:00:00.000Z',
  updated_at: '2026-06-15T00:00:00.000Z',
}

describe('SkillBuilderDialog stream lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseSession.mockReturnValue({ data: { is_super_user: false }, isPending: false })
    vi.mocked(skillBuilderApi.start).mockResolvedValue(session)
    vi.mocked(skillBuilderApi.get).mockResolvedValue(session)
  })

  it('aborts the builder stream when the dialog unmounts', async () => {
    const captured: { signal: AbortSignal | null } = { signal: null }
    vi.mocked(streamSkillBuilderMessage).mockImplementation(
      async function* stream(_sessionId, _payload, signal) {
        captured.signal = signal ?? null
        await new Promise<void>((resolve) => {
          signal?.addEventListener('abort', () => resolve(), { once: true })
        })
      },
    )
    const { unmount } = render(<SkillBuilderDialog open mode="create" onOpenChange={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('요청'), '회의록 스킬을 만들어줘')
    await userEvent.click(screen.getByRole('button', { name: '대화 시작' }))
    await waitFor(() => expect(captured.signal).not.toBeNull())

    unmount()

    expect(captured.signal?.aborted).toBe(true)
    expect(skillBuilderApi.get).not.toHaveBeenCalled()
  })

  it('enables confirmation after a completed stream under StrictMode', async () => {
    vi.mocked(streamSkillBuilderMessage).mockImplementation(async function* stream() {
      yield { event: 'message_end', data: { session_id: 'session-1' } }
    })

    render(
      <StrictMode>
        <SkillBuilderDialog open mode="create" onOpenChange={vi.fn()} />
      </StrictMode>,
    )

    await userEvent.type(screen.getByLabelText('요청'), '회의록 스킬을 만들어줘')
    await userEvent.click(screen.getByRole('button', { name: '대화 시작' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '스킬로 저장' })).toBeEnabled()
    })
  })
})
