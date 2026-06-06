import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { HiTLContext } from '@/lib/chat/hitl-context'
import { ApprovalCard } from '../approval-card'

vi.mock('@assistant-ui/react', () => ({
  makeAssistantToolUI: (config: unknown) => config,
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

type ToolUiRender = {
  render: (props: {
    args: {
      approval_id?: string
      tool_name?: string
      tool_args?: Record<string, unknown>
      hitl_action_index?: number
      hitl_total_actions?: number
      hitl_interrupt_id?: string | null
      allowed_decisions?: string[]
    }
    result?: unknown
    status: { type: string }
    addResult?: (result: unknown) => void
  }) => ReactNode
}

describe('ApprovalCard', () => {
  it('resumes approval even when the runtime cannot accept tool results', async () => {
    const onResumeDecisions = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    const unsupportedAddResult = vi.fn(() => {
      throw new Error('Runtime does not support tool results.')
    })
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'toolu-1',
          tool_name: 'write_file',
          tool_args: {
            file_path: '/runtime/today_diary.md',
            content: '오늘 하루 기록',
          },
        },
        status: { type: 'requires-action' },
        addResult: unsupportedAddResult,
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('approve'))

    await waitFor(() => {
      expect(onResumeDecisions).toHaveBeenCalledWith([{ type: 'approve' }], 'approved')
    })
    expect(unsupportedAddResult).toHaveBeenCalledTimes(1)
  })
})
