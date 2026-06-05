import { fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import {
  addMemoryToolResultIfSupported,
  memoryReasonLabelKey,
  memoryToolPillStatus,
  memoryResultFromProposal,
  SaveUserMemoryToolUI,
  shouldMemoryToolDefaultExpand,
} from '../memory-tool-ui'

vi.mock('@assistant-ui/react', () => ({
  makeAssistantToolUI: (config: unknown) => config,
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

vi.mock('sonner', () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}))

vi.mock('@/lib/hooks/use-memory', () => ({
  useApproveMemoryProposal: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useEditAndApproveMemoryProposal: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useMemoryProposal: () => ({ data: null }),
  useRejectMemoryProposal: () => ({ isPending: false, mutateAsync: vi.fn() }),
}))

type ToolUiRender = {
  render: (props: {
    args: { content?: string; reason?: string | null; scope?: 'user' | 'agent' }
    result?: unknown
    status: { type: string }
    addResult?: (result: unknown) => void
  }) => ReactNode
}

describe('memory tool UI result bridge', () => {
  it('does not throw when the current assistant runtime cannot accept tool results', () => {
    const unsupportedAddResult = vi.fn(() => {
      throw new Error('Runtime does not support tool results.')
    })

    expect(() =>
      addMemoryToolResultIfSupported(unsupportedAddResult, {
        memory_event: 'memory_saved',
        content: 'User prefers concise answers.',
      }),
    ).not.toThrow()
    expect(unsupportedAddResult).toHaveBeenCalledTimes(1)
  })

  it('maps persisted proposal status back to the card event state', () => {
    const baseProposal = {
      id: 'proposal-1',
      user_id: 'user-1',
      agent_id: null,
      conversation_id: 'conversation-1',
      source_run_id: null,
      scope: 'user',
      content: 'User prefers concise answers.',
      reason: null,
      created_at: '2026-06-04T00:00:00',
      resolved_at: null,
    } as const

    expect(memoryResultFromProposal({ ...baseProposal, status: 'pending' })).toMatchObject({
      memory_event: 'memory_proposed',
    })
    expect(memoryResultFromProposal({ ...baseProposal, status: 'approved' })).toMatchObject({
      memory_event: 'memory_saved',
    })
    expect(memoryResultFromProposal({ ...baseProposal, status: 'rejected' })).toMatchObject({
      memory_event: 'memory_rejected',
    })
  })

  it('uses a rejection-specific reason label for rejected memory events', () => {
    expect(memoryReasonLabelKey('memory_rejected')).toBe('rejectedReason')
    expect(memoryReasonLabelKey('memory_saved')).toBe('reason')
    expect(memoryReasonLabelKey('memory_proposed')).toBe('reason')
    expect(memoryReasonLabelKey(undefined)).toBe('reason')
  })

  it('keeps completed memory result cards collapsed by default', () => {
    expect(shouldMemoryToolDefaultExpand('memory_saved', 'complete')).toBe(false)
    expect(shouldMemoryToolDefaultExpand('memory_rejected', 'complete')).toBe(false)
    expect(shouldMemoryToolDefaultExpand(undefined, 'running')).toBe(false)
  })

  it('opens actionable memory proposals by default', () => {
    expect(shouldMemoryToolDefaultExpand('memory_proposed', 'complete')).toBe(true)
  })

  it('maps memory events to compact tool pill status tones', () => {
    expect(memoryToolPillStatus('memory_saved', 'complete')).toBe('success')
    expect(memoryToolPillStatus('memory_rejected', 'complete')).toBe('error')
    expect(memoryToolPillStatus('memory_proposed', 'complete')).toBe('loading')
    expect(memoryToolPillStatus(undefined, 'running')).toBe('loading')
  })

  it('syncs the editable draft when a streaming proposal result resolves content', () => {
    const toolUi = SaveUserMemoryToolUI as unknown as ToolUiRender
    const proposalResult = JSON.stringify({
      memory_event: 'memory_proposed',
      id: 'proposal-1',
      scope: 'user',
      content: 'User prefers concise Korean answers.',
      reason: 'User said this preference.',
    })

    const view = render(
      toolUi.render({
        args: { content: '' },
        result: undefined,
        status: { type: 'running' },
      }),
    )

    view.rerender(
      toolUi.render({
        args: { content: '' },
        result: proposalResult,
        status: { type: 'complete' },
      }),
    )

    expect(screen.getAllByText('User prefers concise Korean answers.').length).toBeGreaterThan(0)
    expect(screen.getByTestId('memory-proposal-approve')).toBeEnabled()

    fireEvent.click(screen.getByTestId('memory-proposal-edit'))

    expect(screen.getByLabelText('contentLabel')).toHaveValue(
      'User prefers concise Korean answers.',
    )
  })
})
