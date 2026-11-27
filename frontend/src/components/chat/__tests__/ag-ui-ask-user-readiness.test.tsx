import { act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HiTLContext, type HiTLContextValue } from '@/lib/chat/hitl-context'
import { render, screen, userEvent } from '../../../../tests/test-utils'
import { UserInputUI } from '../tool-ui/user-input-ui'

type Register = NonNullable<HiTLContextValue['registerDecision']>

function question(
  registerDecision: Register,
  canRespond: boolean,
  {
    mode = 'single',
    interruptId = 'confirmed-interrupt',
    timeout = 300,
    result,
    raw,
    context,
  }: {
    mode?: 'single' | 'option_list'
    interruptId?: string
    timeout?: number
    result?: string
    raw?: boolean
    context?: Partial<HiTLContextValue>
  } = {},
) {
  const args = {
    ...(mode === 'option_list' ? { mode: 'option_list' as const, maxSelections: 1 } : {}),
    question: '어떤 형식으로 받을까요?',
    options: ['HTML', 'PDF'],
    approval_id: 'ask-format',
    timeout_seconds: timeout,
    ...(raw ? {} : { hitl_interrupt_id: interruptId, hitl_action_index: 1, hitl_total_actions: 2 }),
  }
  return (
    <HiTLContext.Provider
      value={{ registerDecision, canRespond, onResumeDecisions: vi.fn(), ...context }}
    >
      <UserInputUI
        type="tool-call"
        toolName="ask_user"
        toolCallId="ask-format"
        argsText={JSON.stringify(args)}
        args={args}
        status={{ type: 'requires-action', reason: 'interrupt' }}
        result={result}
        addResult={vi.fn()}
        respondToApproval={vi.fn()}
        resume={vi.fn()}
      />
    </HiTLContext.Provider>
  )
}

afterEach(() => vi.useRealTimers())

describe('AG-UI ask_user checkpoint readiness', () => {
  it.each(['single', 'option_list'] as const)(
    'registers the first %s answer exactly once against the confirmed interrupt',
    async (mode) => {
      const user = userEvent.setup()
      const acknowledgement = Promise.withResolvers<void>()
      const register = vi.fn<Register>().mockReturnValue(acknowledgement.promise)
      const { rerender } = render(
        question(register, false, { mode, interruptId: 'provisional-interrupt' }),
      )
      const role = mode === 'single' ? 'button' : 'option'
      const submitName = mode === 'single' ? '확인' : /선택 확인/
      const provisional = screen.getByRole(role, { name: /PDF/ })
      expect(provisional).toBeDisabled()
      await user.click(provisional)
      const unavailable = screen.getByRole('button', { name: submitName })
      expect(unavailable).toBeDisabled()
      await user.click(unavailable)
      expect(register).not.toHaveBeenCalled()

      rerender(question(register, true, { mode }))
      await user.click(screen.getByRole(role, { name: /HTML/ }))
      await user.dblClick(screen.getByRole('button', { name: submitName }))
      expect(register).toHaveBeenCalledTimes(1)
      expect(register).toHaveBeenCalledWith(
        1,
        {
          type: 'respond',
          message:
            mode === 'single'
              ? 'HTML'
              : JSON.stringify({ mode: 'option_list', selection: ['HTML'], labels: ['HTML'] }),
        },
        'HTML',
        'confirmed-interrupt',
      )
      await act(async () => acknowledgement.resolve())
      expect(screen.getByText('답변 완료')).toBeVisible()

      rerender(question(register, false, { mode }))
      expect(screen.getByText('HTML')).toBeVisible()
      rerender(question(register, false, { mode, result: 'HTML' }))
      expect(screen.getByText('HTML')).toBeVisible()
      expect(screen.queryByRole('button', { name: submitName })).not.toBeInTheDocument()
      expect(register).toHaveBeenCalledTimes(1)
    },
  )

  it('reserves the response deadline for the time the user can actually respond', async () => {
    vi.useFakeTimers()
    const register = vi.fn<Register>().mockResolvedValue(undefined)
    const { rerender } = render(question(register, false, { timeout: 1 }))
    await act(() => vi.advanceTimersByTimeAsync(2500))
    expect(register).not.toHaveBeenCalled()

    rerender(question(register, true, { timeout: 1 }))
    await act(() => vi.advanceTimersByTimeAsync(1))
    expect(register).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'HTML' })).toBeEnabled()
  })

  it('clears an earlier selection when the interrupt rotates inside the same tool call', async () => {
    const register = vi.fn<Register>().mockResolvedValue(undefined)
    const user = userEvent.setup()
    const { rerender } = render(question(register, true, { interruptId: 'original' }))
    await user.click(screen.getByRole('button', { name: 'PDF' }))
    expect(screen.getByRole('button', { name: '확인' })).toBeEnabled()
    rerender(question(register, true, { interruptId: 'replacement' }))
    expect(screen.getByRole('button', { name: '확인' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'HTML' }))
    await user.click(screen.getByRole('button', { name: '확인' }))
    expect(register.mock.calls).toEqual([
      [1, { type: 'respond', message: 'HTML' }, 'HTML', 'replacement'],
    ])
  })

  it.each(['unbound', 'previous interrupt'] as const)(
    'keeps a %s card from answering another confirmed slot',
    async (binding) => {
      const user = userEvent.setup()
      const register = vi.fn<Register>()
      const resume = vi.fn<HiTLContextValue['onResumeDecisions']>()
      render(
        question(register, true, {
          raw: binding === 'unbound',
          interruptId: 'old',
          context: {
            onResumeDecisions: resume,
            canRespondTo: (id, index) => id === 'current' && index === 1,
          },
        }),
      )
      const choice = screen.getByRole('button', { name: 'HTML' })
      expect(choice).toBeDisabled()
      await user.click(choice)
      await user.click(screen.getByRole('button', { name: '확인' }))
      expect(register).not.toHaveBeenCalled()
      expect(resume).not.toHaveBeenCalled()
    },
  )

  it('preserves the submitted choice while loading and clears it on replacement of the approval batch', async () => {
    const user = userEvent.setup()
    const register = vi.fn<Register>().mockResolvedValue(undefined)
    const { rerender } = render(
      question(register, true, { context: { approvalGeneration: 'checkpoint-1' } }),
    )
    await user.click(screen.getByRole('button', { name: 'PDF' }))
    await user.click(screen.getByRole('button', { name: '확인' }))
    expect(screen.getByText('답변 완료')).toBeVisible()
    rerender(question(register, false, { context: { approvalGeneration: null } }))
    expect(screen.getByText('PDF')).toBeVisible()

    rerender(question(register, true, { context: { approvalGeneration: 'checkpoint-2' } }))
    expect(screen.queryByText('답변 완료')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '확인' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'HTML' }))
    await user.click(screen.getByRole('button', { name: '확인' }))
    expect(register.mock.calls.map(([, decision]) => decision)).toEqual([
      { type: 'respond', message: 'PDF' },
      { type: 'respond', message: 'HTML' },
    ])
  })
})
