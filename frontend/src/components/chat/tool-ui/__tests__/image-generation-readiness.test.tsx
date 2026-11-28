import { act, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent } from '../../../../../tests/test-utils'
import { HiTLContext, type HiTLContextValue } from '@/lib/chat/hitl-context'
import { ImageApprovalToolUI, ImageChoiceToolUI } from '../image-generation-ui'

function card(
  kind: 'choice' | 'approval',
  hitl: HiTLContextValue,
  { annotated = true, complete = false }: { annotated?: boolean; complete?: boolean } = {},
) {
  const args = {
    phase: 6,
    auto_prompt: 'Original',
    prompt: 'Original',
    image_url: '/images/preview.png',
    ...(annotated && { hitl_interrupt_id: 'image-review', hitl_action_index: 1 }),
  }
  const Component = kind === 'choice' ? ImageChoiceToolUI : ImageApprovalToolUI
  return (
    <HiTLContext.Provider value={hitl}>
      <Component
        type="tool-call"
        toolName={kind}
        toolCallId="image-card"
        argsText={JSON.stringify(args)}
        args={args}
        status={complete ? { type: 'complete' } : { type: 'requires-action', reason: 'interrupt' }}
        addResult={vi.fn()}
        respondToApproval={vi.fn()}
        resume={vi.fn()}
      />
    </HiTLContext.Provider>
  )
}

describe('image decision readiness', () => {
  it.each(['choice', 'approval'] as const)('admits only the confirmed %s action', async (kind) => {
    const user = userEvent.setup()
    const hitl = {
      canRespond: true,
      onResumeDecisions: vi.fn(),
      registerDecision: vi.fn(),
      approvalGeneration: 'checkpoint-1',
      canRespondTo: (id?: string | null, index?: number) => id === 'image-review' && index === 1,
    }
    const { rerender } = render(card(kind, { ...hitl, canRespond: false }))
    const label = kind === 'choice' ? '생성하기' : '확정'
    const button = () => screen.getByRole('button', { name: label })
    expect(screen.getByRole('textbox')).toBeDisabled()
    for (const entry of screen.getAllByRole('button')) expect(entry).toBeDisabled()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Blocked edit' } })
    fireEvent.click(button())
    rerender(card(kind, hitl, { annotated: false }))
    expect(button()).toBeDisabled()
    rerender(card(kind, { ...hitl, canRespondTo: () => false }))
    expect(button()).toBeDisabled()
    expect(hitl.registerDecision).not.toHaveBeenCalled()

    rerender(card(kind, hitl))
    expect(screen.getByRole('textbox')).toHaveValue('Original')
    await user.type(screen.getByRole('textbox'), ' edited')
    await user.click(button())
    expect(hitl.registerDecision.mock.calls).toEqual([
      [
        1,
        {
          type: 'respond',
          message: JSON.stringify({
            choice: kind === 'choice' ? 'generate' : 'confirm',
            prompt: 'Original edited',
          }),
        },
        label,
        'image-review',
      ],
    ])
    expect(hitl.onResumeDecisions).not.toHaveBeenCalled()
    rerender(card(kind, { ...hitl, canRespond: false, approvalGeneration: null }))
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    rerender(card(kind, { ...hitl, approvalGeneration: 'checkpoint-2' }))
    expect(button()).toBeEnabled()
    expect(screen.getByRole('textbox')).toHaveValue('Original')
  })

  it('leaves a replacement approval available if an older acknowledgement fails', async () => {
    const old = Promise.withResolvers<void>()
    const hitl = {
      canRespond: true,
      approvalGeneration: 'first',
      onResumeDecisions: vi.fn(),
      registerDecision: vi.fn().mockReturnValueOnce(old.promise),
    }
    const { rerender } = render(card('approval', hitl))
    fireEvent.click(screen.getByRole('button', { name: '확정' }))
    rerender(card('approval', { ...hitl, approvalGeneration: 'second' }))
    await act(async () => old.reject(new Error('Old checkpoint')))
    expect(screen.getByRole('button', { name: '확정' })).toBeEnabled()
  })

  it('rechecks a slot that lost permission after the buttons were rendered', () => {
    const canRespondTo = vi.fn().mockReturnValue(true)
    const hitl = {
      canRespond: true,
      canRespondTo,
      registerDecision: vi.fn(),
      onResumeDecisions: vi.fn(),
    }
    render(card('choice', hitl))
    const submit = screen.getByRole('button', { name: '생성하기' })
    expect(submit).toBeEnabled()
    canRespondTo.mockReturnValue(false)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Unconfirmed' } })
    expect(screen.getByRole('textbox')).toHaveValue('Original')
    fireEvent.click(submit)
    expect(hitl.registerDecision).not.toHaveBeenCalled()
    expect(hitl.onResumeDecisions).not.toHaveBeenCalled()
  })

  it('preserves the legacy unannotated response and displays a completed preview', async () => {
    const hitl = { onResumeDecisions: vi.fn(), registerDecision: vi.fn() }
    const { rerender } = render(card('approval', hitl, { annotated: false }))
    await userEvent.setup().click(screen.getByRole('button', { name: '재생성' }))
    expect(hitl.onResumeDecisions).toHaveBeenCalledWith(
      [{ type: 'respond', message: JSON.stringify({ choice: 'regenerate', prompt: 'Original' }) }],
      '재생성',
    )
    expect(hitl.registerDecision).not.toHaveBeenCalled()
    rerender(card('approval', { ...hitl, canRespond: false }, { complete: true }))
    expect(screen.getByRole('img')).toBeVisible()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
