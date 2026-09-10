import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { HiTLContext } from '@/lib/chat/hitl-context'
import { UserInputUI } from '../user-input-ui'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

type ToolUiRender = {
  render: (props: {
    args: {
      mode?: 'question_flow' | 'option_list'
      title?: string
      question?: string
      options?: string[]
      questions?: Array<{
        id?: string
        label?: string
        question?: string
        type: 'single_select' | 'multi_select' | 'text'
        options?: Array<{ id?: string; label: string }>
      }>
      hitl_action_index?: number
      hitl_total_actions?: number
      hitl_interrupt_id?: string | null
    }
    result?: unknown
    status: { type: string }
  }) => ReactNode
}

const renderUserInput = UserInputUI as unknown as ToolUiRender['render']

describe('UserInputUI', () => {
  it('shows the actual question in option-list mode', () => {
    function UserInputUnderTest() {
      return renderUserInput({
        args: {
          mode: 'option_list',
          title: '출력 형식',
          question: '결과를 어떤 형식으로 받고 싶으세요?',
          options: ['HTML', 'Markdown'],
        },
        status: { type: 'requires-action' },
      })
    }

    render(<UserInputUnderTest />)

    expect(screen.getByText('결과를 어떤 형식으로 받고 싶으세요?')).toBeInTheDocument()
  })

  it('uses a neutral card surface while input is pending', () => {
    function UserInputUnderTest() {
      return renderUserInput({
        args: {
          mode: 'option_list',
          question: 'Continue?',
          options: ['Yes', 'No'],
        },
        status: { type: 'requires-action' },
      })
    }

    render(<UserInputUnderTest />)

    const card = screen.getByText('inputRequired').closest('.moldy-chat-card')
    expect(card).toHaveClass('border-border', 'bg-card', 'text-foreground')
    expect(card).not.toHaveClass('moldy-status-surface')
  })

  it('does not repeat the generic input-required title inside the card body', () => {
    function UserInputUnderTest() {
      return renderUserInput({
        args: {
          mode: 'option_list',
          title: 'inputRequired',
          question: 'Continue?',
          options: ['Yes', 'No'],
        },
        status: { type: 'requires-action' },
      })
    }

    render(<UserInputUnderTest />)

    expect(screen.getAllByText('inputRequired')).toHaveLength(1)
  })

  it('does not enable an empty fallback question card', () => {
    function UserInputUnderTest() {
      return renderUserInput({
        args: {},
        status: { type: 'requires-action' },
      })
    }

    render(<UserInputUnderTest />)

    expect(screen.getByRole('button', { name: 'confirm' })).toBeDisabled()
  })

  it('passes the LangGraph interrupt id when registering a user response', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    function UserInputUnderTest() {
      return renderUserInput({
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
    fireEvent.click(screen.getByRole('button', { name: 'confirm' }))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        { type: 'respond', message: 'Yes' },
        'Yes',
        'interrupt-ask-user',
      )
    })
  })

  it('serializes a custom option-list answer as the submitted response', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    function UserInputUnderTest() {
      return renderUserInput({
        args: {
          mode: 'option_list',
          question: 'Output format?',
          options: ['HTML', 'Markdown'],
          hitl_action_index: 0,
          hitl_total_actions: 1,
          hitl_interrupt_id: 'interrupt-custom-answer',
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <UserInputUnderTest />
      </HiTLContext.Provider>,
    )

    const customOption = screen.getByRole('option', { name: /customAnswer/ })
    expect(customOption).toHaveAttribute('aria-selected', 'false')
    fireEvent.click(customOption)
    expect(customOption).toHaveAttribute('aria-selected', 'true')
    fireEvent.change(screen.getByPlaceholderText('customAnswerPlaceholder'), {
      target: { value: 'HTML and Markdown' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'confirmSelection' }))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        {
          type: 'respond',
          message: JSON.stringify({
            mode: 'option_list',
            selection: ['HTML and Markdown'],
            labels: ['HTML and Markdown'],
          }),
        },
        'HTML and Markdown',
        'interrupt-custom-answer',
      )
    })
  })

  it('shows one question-flow step at a time and includes a custom answer', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    function UserInputUnderTest() {
      return renderUserInput({
        args: {
          mode: 'question_flow',
          questions: [
            {
              id: 'format',
              label: 'Format',
              question: 'Choose a format',
              type: 'single_select',
              options: [{ id: 'html', label: 'HTML' }],
            },
            {
              id: 'priority',
              label: 'Priority',
              question: 'Choose a priority',
              type: 'single_select',
              options: [{ id: 'quality', label: 'Quality' }],
            },
          ],
          hitl_action_index: 0,
          hitl_total_actions: 1,
          hitl_interrupt_id: 'interrupt-question-flow',
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <UserInputUnderTest />
      </HiTLContext.Provider>,
    )

    expect(screen.getByText('Format')).toBeVisible()
    expect(screen.queryByText('Priority')).toBeNull()
    fireEvent.click(screen.getByRole('option', { name: /customAnswer/ }))
    fireEvent.change(screen.getByPlaceholderText('customAnswerPlaceholder'), {
      target: { value: 'Both formats' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'next' }))
    expect(screen.getByText('Priority')).toBeVisible()
    expect(screen.queryByText('Format')).toBeNull()
    fireEvent.click(screen.getByRole('option', { name: /Quality/ }))
    fireEvent.click(screen.getByRole('button', { name: 'complete' }))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        expect.objectContaining({ type: 'respond' }),
        'Format: Both formats | Priority: Quality',
        'interrupt-question-flow',
      )
    })
  })

  it('returns a batched user response to idle when the shared resume is rejected', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockRejectedValue(new Error('stale'))
    function UserInputUnderTest() {
      return renderUserInput({
        args: {
          question: 'Continue?',
          options: ['Yes'],
          hitl_action_index: 0,
          hitl_total_actions: 2,
          hitl_interrupt_id: 'interrupt-mixed',
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <UserInputUnderTest />
      </HiTLContext.Provider>,
    )

    const response = screen.getByRole('button', { name: 'Yes' })
    fireEvent.click(response)
    fireEvent.click(screen.getByRole('button', { name: 'confirm' }))

    await waitFor(() => expect(response).toBeEnabled())
    expect(screen.queryByText('completed')).toBeNull()
    expect(screen.getByRole('alert')).toHaveTextContent('resumeFailed')
  })
})
