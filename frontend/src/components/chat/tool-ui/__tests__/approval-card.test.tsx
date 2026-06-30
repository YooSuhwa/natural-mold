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
      description?: string
      message?: string
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

  it('keeps the approval pending when resume fails', async () => {
    const onResumeDecisions = vi.fn<() => Promise<void>>().mockRejectedValue(new Error('stale'))
    const addResult = vi.fn()
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'toolu-1',
          tool_name: 'write_file',
          tool_args: { file_path: '/runtime/report.md' },
        },
        status: { type: 'requires-action' },
        addResult,
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('approve'))

    expect(await screen.findByText('resumeFailed')).toBeVisible()
    expect(addResult).not.toHaveBeenCalled()
    expect(screen.getByText('approve')).toBeEnabled()
  })

  it('passes the LangGraph interrupt id when registering an approval decision', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'interrupt-approval:0',
          tool_name: 'write_file',
          tool_args: { path: 'report.md' },
          hitl_action_index: 0,
          hitl_total_actions: 1,
          hitl_interrupt_id: 'interrupt-approval',
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('approve'))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        { type: 'approve' },
        'approved',
        'interrupt-approval',
      )
    })
  })

  it('keeps a rejected result visible after LangGraph resume accepts the decision', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'interrupt-approval:0',
          tool_name: 'execute_in_skill',
          tool_args: { command: 'node scripts/create_docx.cjs' },
          hitl_action_index: 0,
          hitl_total_actions: 1,
          hitl_interrupt_id: 'interrupt-approval',
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('reject'))
    fireEvent.click(screen.getByText('rejectConfirm'))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        { type: 'reject', message: undefined },
        'rejected',
        'interrupt-approval',
      )
    })
    expect(await screen.findByText('rejected')).toBeVisible()
  })

  it('redacts sensitive approval descriptions and args before rendering', () => {
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'interrupt-approval:0',
          tool_name: 'execute_in_skill',
          description:
            "Args: {'command': 'node scripts/create_docx.cjs', 'api_key': 'raw-secret-value'}",
          tool_args: {
            command: 'node scripts/create_docx.cjs',
            api_key: 'raw-secret-value',
            usage_metadata: { prompt_tokens: 12 },
          },
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn() }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    expect(document.body.textContent).not.toContain('raw-secret-value')
    expect(document.body.textContent).toContain('<redacted>')

    fireEvent.click(screen.getByText('args'))
    expect(document.body.textContent).not.toContain('raw-secret-value')
    expect(document.body.textContent).toContain('prompt_tokens')

    fireEvent.click(screen.getByText('edit'))
    expect(document.body.textContent).not.toContain('raw-secret-value')
  })

  it('renders tool args as a readable key/value list, not a raw JSON dump', () => {
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'toolu-kv',
          tool_name: 'write_file',
          tool_args: { file_path: 'report.md', overwrite: true },
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn() }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('args'))

    // Each arg name is a label; scalar values render plainly (no JSON quotes).
    expect(screen.getByText('file_path')).toBeInTheDocument()
    expect(screen.getByText('report.md')).toBeInTheDocument()
    expect(screen.getByText('overwrite')).toBeInTheDocument()
    expect(screen.getByText('true')).toBeInTheDocument()
    // Not the old raw `JSON.stringify(args, null, 2)` dump.
    expect(document.body.textContent).not.toContain('"file_path"')
    expect(document.body.textContent).not.toContain('{\n')
  })

  it('restores redacted placeholders from raw args before sending edited approvals', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'interrupt-approval:0',
          tool_name: 'execute_in_skill',
          tool_args: {
            command: 'node scripts/create_docx.cjs',
            api_key: 'raw-secret-value',
          },
          hitl_action_index: 0,
          hitl_total_actions: 1,
          hitl_interrupt_id: 'interrupt-approval',
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('edit'))
    expect(document.body.textContent).not.toContain('raw-secret-value')

    fireEvent.change(screen.getByPlaceholderText('editArgsPlaceholder'), {
      target: {
        value: JSON.stringify({
          command: 'node scripts/updated_docx.cjs',
          api_key: '<redacted>',
        }),
      },
    })
    fireEvent.click(screen.getByText('editAndApprove'))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        {
          type: 'edit',
          edited_action: {
            name: 'execute_in_skill',
            args: {
              command: 'node scripts/updated_docx.cjs',
              api_key: 'raw-secret-value',
            },
          },
        },
        'editApproved',
        'interrupt-approval',
      )
    })
  })
})
