import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { HiTLContext } from '@/lib/chat/hitl-context'
import { UserInputUI } from '../user-input-ui'

vi.mock('@assistant-ui/react', () => ({
  makeAssistantToolUI: (config: unknown) => config,
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

type ToolUiRender = {
  render: (props: {
    args: {
      question?: string
      options?: string[]
      hitl_action_index?: number
      hitl_total_actions?: number
      hitl_interrupt_id?: string | null
    }
    result?: unknown
    status: { type: string }
  }) => ReactNode
}

describe('UserInputUI', () => {
  it('passes the LangGraph interrupt id when registering a user response', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    const toolUi = UserInputUI as unknown as ToolUiRender
    function UserInputUnderTest() {
      return toolUi.render({
        args: {
          question: 'Continue?',
          options: ['Yes'],
          hitl_action_index: 0,
          hitl_total_actions: 1,
          hitl_interrupt_id: 'interrupt-ask-user',
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <UserInputUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('Yes'))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        { type: 'respond', message: 'Yes' },
        'Yes',
        'interrupt-ask-user',
      )
    })
  })
})
