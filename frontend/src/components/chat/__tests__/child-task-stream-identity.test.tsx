import type { AnyStream, SubagentDiscoverySnapshot } from '@langchain/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import { Provider as JotaiProvider } from 'jotai'
import { NextIntlClientProvider } from 'next-intl'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import messages from '../../../../messages/en.json'
import { createTestQueryClient } from '../../../../tests/test-utils'
import { ChatConversationContext } from '../conversation-context'
import { DelegatedChildTasks, ScopedSubagentRuntimeProvider } from '../delegated-child-tasks'
import { SubagentCard } from '../subagent-card'
import { SubagentPanelContent } from '../right-rail/subagent-panel-content'
import type { ChildTask, ChildTaskPage } from '@/lib/api/child-tasks'
import { ApiError } from '@/lib/api/errors'

const sdk = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  snapshots: vi.fn(),
  shared: vi.fn(),
  messages: vi.fn(),
  calls: vi.fn(),
}))

vi.mock('@/lib/api/child-tasks', async (original) => ({
  ...(await original<typeof import('@/lib/api/child-tasks')>()),
  listChildTasks: sdk.list,
  getChildTask: sdk.get,
}))

vi.mock('@assistant-ui/react', () => ({ useAuiState: () => false }))

vi.mock('@langchain/react', async (original) => ({
  ...(await original<typeof import('@langchain/react')>()),
  useMessages: sdk.messages,
  useToolCalls: sdk.calls,
}))

vi.mock('@/lib/chat/langgraph-runtime/subagent-runtime', async (original) => ({
  ...(await original<typeof import('@/lib/chat/langgraph-runtime/subagent-runtime')>()),
  useSubagentSnapshots: sdk.snapshots,
  useSharedSubagentRuntime: sdk.shared,
}))

const conversationId = 'conversation-1'
const row: ChildTask = {
  id: 'child-1',
  run_id: 'run-1',
  tool_call_id: 'call-reused',
  role: 'Researcher',
  status: 'running',
  checkpointed: true,
  created_at: '2026-11-27T10:00:00Z',
  updated_at: '2026-11-27T10:00:01Z',
  result: null,
  instructions: [],
}

const snapshot: SubagentDiscoverySnapshot = {
  id: row.tool_call_id,
  name: 'Sensitive stream identity',
  namespace: ['tools:node-1'],
  parentId: null,
  depth: 1,
  status: 'running',
  taskInput: 'Sensitive input',
  output: 'Sensitive interim result',
  error: undefined,
  startedAt: new Date(row.created_at),
  completedAt: null,
}

function renderChat(content: ReactNode) {
  const queryClient = createTestQueryClient()
  render(
    <QueryClientProvider client={queryClient}>
      <JotaiProvider>
        <NextIntlClientProvider locale="en" messages={messages}>
          <ChatConversationContext.Provider value={conversationId}>
            {content}
          </ChatConversationContext.Provider>
        </NextIntlClientProvider>
      </JotaiProvider>
    </QueryClientProvider>,
  )
  return queryClient
}

describe('managed child stream identity', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    sdk.list.mockResolvedValue({
      items: [row],
      enabled: true,
      allow_legacy_details: false,
      next_offset: null,
    })
    sdk.get.mockResolvedValue(row)
    sdk.snapshots.mockReturnValue([snapshot])
    sdk.messages.mockReturnValue([])
    sdk.calls.mockReturnValue([])
  })

  it('keeps a tool-call id reused by another run out of the authorised child detail', async () => {
    // Both runs used this call id. The SDK snapshot alone does not identify
    // which persisted task the user is reading.
    const selectedTask = { ...row, result: 'Authorised child result' }
    const otherTask = { ...row, id: 'child-other-run', run_id: 'run-2' }
    sdk.shared.mockReturnValue({
      conversationId,
      stream: { subagents: new Map([[snapshot.id, snapshot]]) },
      subagentsByToolCallId: new Map([[snapshot.id, snapshot]]),
    })
    sdk.get.mockResolvedValue(selectedTask)
    sdk.list.mockResolvedValue({
      items: [selectedTask, otherTask],
      enabled: true,
      allow_legacy_details: false,
      next_offset: null,
    })

    renderChat(
      <SubagentPanelContent
        payload={{
          conversationId,
          childTaskId: row.id,
          toolCallId: row.tool_call_id,
          agentName: 'Previously cached label',
        }}
      />,
    )

    expect(await screen.findByText('Authorised child result')).toBeVisible()
    expect(screen.queryByText('Sensitive input')).not.toBeInTheDocument()
    expect(screen.queryByText('Sensitive interim result')).not.toBeInTheDocument()
    expect(sdk.messages).not.toHaveBeenCalled()
    expect(sdk.calls).not.toHaveBeenCalled()
  })

  it.each(['managed', 'forbidden'] as const)(
    'removes unbound SDK details from an inline parent tool card when legacy access becomes %s',
    async (access) => {
      let finishLookup!: (page: ChildTaskPage) => void
      sdk.list.mockImplementationOnce(
        () =>
          new Promise<ChildTaskPage>((resolve) => {
            finishLookup = resolve
          }),
      )
      sdk.messages.mockReturnValue([{ id: 'msg-1', content: 'Private SDK message' }])
      sdk.calls.mockReturnValue([{ callId: 'call-2', name: 'private_sdk_tool' }])
      // The real runtime provider consumes the SDK's discovery map. Its detail
      // selectors above only run while that stream is accessible to the card.
      const stream = { subagents: new Map([[snapshot.id, snapshot]]) } as unknown as AnyStream
      const queryClient = renderChat(
        <ScopedSubagentRuntimeProvider conversationId={conversationId} stream={stream}>
          <SubagentCard
            toolCallId={snapshot.id}
            fallback={{
              agentName: 'Parent task',
              input: 'Ordinary parent tool input',
              status: 'loading',
            }}
          />
        </ScopedSubagentRuntimeProvider>,
      )
      expect(screen.getByText('Ordinary parent tool input')).toBeVisible()
      expect(screen.queryByText(snapshot.name)).not.toBeInTheDocument()
      expect(sdk.messages).not.toHaveBeenCalled()

      await act(async () => {
        finishLookup({ items: [], enabled: false, allow_legacy_details: true, next_offset: null })
      })
      expect(await screen.findByText('Private SDK message')).toBeVisible()
      expect(screen.getByText('private_sdk_tool')).toBeVisible()
      expect(screen.getByText(snapshot.name)).toBeVisible()

      if (access === 'forbidden')
        sdk.list.mockRejectedValue(new ApiError(403, 'forbidden', 'Forbidden'))
      else
        sdk.list.mockResolvedValue({
          items: [],
          enabled: true,
          allow_legacy_details: false,
          next_offset: null,
        })
      await act(async () => {
        await queryClient.invalidateQueries()
      })

      expect(await screen.findByText('Ordinary parent tool input')).toBeVisible()
      for (const privateText of [
        snapshot.name,
        'Sensitive input',
        'Sensitive interim result',
        'Private SDK message',
        'private_sdk_tool',
      ]) {
        expect(screen.queryByText(privateText)).not.toBeInTheDocument()
      }
    },
  )

  it('shows an SDK-only chip as read-only after a successful lookup when management is disabled', async () => {
    let finishLookup!: (page: ChildTaskPage) => void
    sdk.list.mockImplementation(
      () =>
        new Promise<ChildTaskPage>((resolve) => {
          finishLookup = resolve
        }),
    )
    renderChat(<DelegatedChildTasks conversationId={conversationId} />)
    expect(
      screen.queryByRole('button', { name: 'Open Sensitive stream identity details' }),
    ).not.toBeInTheDocument()
    await act(async () => {
      finishLookup({ items: [], enabled: false, allow_legacy_details: true, next_offset: null })
    })
    expect(
      await screen.findByRole('button', { name: 'Open Sensitive stream identity details' }),
    ).toBeVisible()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('keeps a previously selected legacy rail private on a fresh client when the disabled-mode page withholds legacy access', async () => {
    sdk.list.mockResolvedValue({
      items: [],
      enabled: false,
      allow_legacy_details: false,
      next_offset: null,
    })
    sdk.shared.mockReturnValue({
      conversationId,
      stream: { subagents: new Map([[snapshot.id, snapshot]]) },
      subagentsByToolCallId: new Map([[snapshot.id, snapshot]]),
    })
    renderChat(
      <>
        <DelegatedChildTasks conversationId={conversationId} />
        <SubagentPanelContent
          payload={{
            conversationId,
            toolCallId: snapshot.id,
            agentName: snapshot.name,
            input: 'Previously cached request',
          }}
        />
      </>,
    )

    expect(await screen.findByText('This task is unavailable.')).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Open Sensitive stream identity details' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByText('Previously cached request')).not.toBeInTheDocument()
    expect(screen.queryByText('Sensitive interim result')).not.toBeInTheDocument()
    expect(sdk.messages).not.toHaveBeenCalled()
  })

  it.each(['denied', 'removed', 'disabled'] as const)(
    'never exposes an old managed task as an SDK-only chip when access is %s',
    async (access) => {
      const queryClient = renderChat(<DelegatedChildTasks conversationId={conversationId} />)
      expect(await screen.findByRole('button', { name: 'Open Researcher details' })).toBeVisible()
      if (access === 'denied')
        sdk.list.mockRejectedValue(new ApiError(404, 'not_found', 'Not found'))
      else
        sdk.list.mockResolvedValue({
          items: [],
          enabled: access !== 'disabled',
          allow_legacy_details: false,
          next_offset: null,
        })

      await act(async () => {
        await queryClient.invalidateQueries()
      })

      await waitFor(() =>
        expect(
          screen.queryByRole('button', { name: 'Open Researcher details' }),
        ).not.toBeInTheDocument(),
      )
      expect(
        screen.queryByRole('button', { name: 'Open Sensitive stream identity details' }),
      ).not.toBeInTheDocument()
      expect(screen.queryByTitle('Sensitive input')).not.toBeInTheDocument()
    },
  )
})
