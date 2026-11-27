import {
  AssistantRuntimeProvider,
  fromThreadMessageLike,
  useExternalStoreRuntime,
  type ThreadMessage,
} from '@assistant-ui/react'
import { Provider as JotaiProvider, useAtomValue } from 'jotai'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import messagesKo from '../../../../../messages/ko.json'
import { render, screen, userEvent, within } from '../../../../../tests/test-utils'
import { ChatConversationContext } from '@/components/chat/conversation-context'
import { SubagentPanelContent } from '@/components/chat/right-rail/subagent-panel-content'
import { SubagentCard } from '@/components/chat/subagent-card'
import type { AgUiActivitySnapshot } from '@/lib/ag-ui/chat-history-types'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import { AgUiSubagentRuntimeProvider } from '../subagent-runtime'

const probes = vi.hoisted(() => ({ messages: vi.fn(), toolCalls: vi.fn(), onNew: vi.fn() }))

vi.mock('@langchain/react', async (original) => ({
  ...(await original<typeof import('@langchain/react')>()),
  useMessages: probes.messages,
  useToolCalls: probes.toolCalls,
}))

vi.mock('@/lib/hooks/use-child-tasks', async (original) => ({
  ...(await original<typeof import('@/lib/hooks/use-child-tasks')>()),
  useChildTasks: () => ({ allowLegacyDetails: true, tasks: [], isPending: false }),
}))

const conversationId = 'chat-one'
const sameCallId = 'tool-id-reused-by-the-model'
const complete = { type: 'complete', reason: 'stop' } as const

function parentMessage(label: string): ThreadMessage {
  const subagent = fromThreadMessageLike(
    {
      role: 'assistant',
      metadata: {
        custom: {
          agui: { name: 'Researcher', description: `Brief ${label}`, result: `Done ${label}` },
        },
      },
      content: [
        { type: 'text', text: `Evidence ${label}` },
        {
          type: 'data',
          name: 'agui-activity/moldy.ui_data',
          data: { type: 'demo_note', props: { text: `Note ${label}` } },
        },
        {
          type: 'data',
          name: 'agui-activity/moldy.ui_data',
          data: { type: 'unregistered-card', props: { text: `Unknown ${label}` } },
        },
        {
          type: 'tool-call',
          toolName: 'search',
          toolCallId: 'inner-call',
          args: {},
          result: `Source ${label}`,
        },
      ],
    },
    `child-${label}`,
    complete,
  )
  return fromThreadMessageLike(
    {
      role: 'assistant',
      content: [
        {
          type: 'tool-call',
          toolName: 'task',
          toolCallId: sameCallId,
          args: {},
          messages: [subagent],
        },
      ],
    },
    `parent-${label}`,
    complete,
  )
}

const first = parentMessage('first')
const second = parentMessage('second')
const both = [first, second]
const secondOnly = [second]

function SelectedPanel() {
  const selection = useAtomValue(chatRightRailAtom)
  return selection.mode === 'subagent' && <SubagentPanelContent payload={selection.subagent} />
}

function Harness({
  messages = both,
  activities,
  allow = true,
  showPanel = true,
}: {
  messages?: ThreadMessage[]
  activities?: readonly AgUiActivitySnapshot[]
  allow?: boolean
  showPanel?: boolean
}) {
  const runtime = useExternalStoreRuntime({ messages, onNew: probes.onNew })
  return (
    <JotaiProvider>
      <ChatConversationContext.Provider value={conversationId}>
        <AssistantRuntimeProvider runtime={runtime}>
          <AgUiSubagentRuntimeProvider
            conversationId={conversationId}
            allowLegacyDetails={allow}
            activities={activities}
          >
            {messages.map((message) => (
              <section key={message.id} aria-label={message.id}>
                <SubagentCard
                  parentMessageId={message.id}
                  toolCallId={sameCallId}
                  fallback={{
                    agentName: 'Unverified agent',
                    input: 'Unverified input',
                    status: 'loading',
                  }}
                />
              </section>
            ))}
          </AgUiSubagentRuntimeProvider>
        </AssistantRuntimeProvider>
        <aside aria-label="Selected child">{showPanel && <SelectedPanel />}</aside>
      </ChatConversationContext.Provider>
    </JotaiProvider>
  )
}

const panel = () => within(screen.getByRole('complementary', { name: 'Selected child' }))
const card = (parentId: string) => within(screen.getByRole('region', { name: parentId }))

async function openPanelFor(parentId: string) {
  await userEvent.click(
    card(parentId).getByRole('button', { name: messagesKo.chat.toolUi.subAgent.openDetails }),
  )
}

describe('AG-UI subagent presentation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    probes.messages.mockReturnValue([])
    probes.toolCalls.mockReturnValue([])
  })

  it('uses the parent message for an inline card and the selected child run for its panel', async () => {
    render(<Harness />)

    for (const [parent, own, other] of [
      [first, 'first', 'second'],
      [second, 'second', 'first'],
    ] as const) {
      const inline = card(parent.id)
      expect(await inline.findByText(`Evidence ${own}`)).toBeVisible()
      expect(inline.queryByText(`Evidence ${other}`)).not.toBeInTheDocument()
      await openPanelFor(parent.id)
      expect(await panel().findByText(`Evidence ${own}`)).toBeVisible()
      expect(panel().getByText(`Source ${own}`)).toBeVisible()
      expect(panel().queryByText(`Source ${other}`)).not.toBeInTheDocument()
    }
    expect(probes.messages).not.toHaveBeenCalled()
    expect(probes.toolCalls).not.toHaveBeenCalled()
    expect(probes.onNew).not.toHaveBeenCalled()
  })

  it('removes projected details and cached selections when the conversation loses access', async () => {
    const view = render(<Harness />)
    await openPanelFor(first.id)
    expect(await panel().findByText('Evidence first')).toBeVisible()

    view.rerender(<Harness allow={false} />)
    for (const value of [
      'Researcher',
      'Brief first',
      'Evidence first',
      'Evidence second',
      'Source first',
      'Done first',
      'Unverified input',
    ]) {
      expect(screen.queryAllByText(value)).toHaveLength(0)
    }
  })

  it('renders an allowlisted activity inside its authorized child and removes it on revocation', async () => {
    const view = render(<Harness />)
    expect(await card(first.id).findByText('Note first')).toBeVisible()
    expect(card(first.id).queryByText('Note second')).not.toBeInTheDocument()
    await openPanelFor(second.id)
    expect(await panel().findByText('Note second')).toBeVisible()
    expect(panel().queryByText('Note first')).not.toBeInTheDocument()
    expect(screen.queryAllByText(/Unknown (first|second)/)).toHaveLength(0)

    view.rerender(<Harness allow={false} />)
    expect(screen.queryAllByText(/Note (first|second)/)).toHaveLength(0)
  })

  it('updates and clears only the selected child’s todo list in both the card and open panel', async () => {
    function todos(child: string, text?: string): AgUiActivitySnapshot {
      return {
        type: 'ACTIVITY_SNAPSHOT',
        activityType: 'moldy.subagent_todos',
        messageId: `todos-${child}`,
        subagentRunId: `child-${child}`,
        content: { todos: text ? [{ id: 'same-todo', content: text, status: 'in_progress' }] : [] },
      }
    }

    const sibling = todos('second', 'Review sources')
    const view = render(<Harness activities={[todos('first', 'Read draft'), sibling]} />)
    await openPanelFor(first.id)
    expect(await card(first.id).findByText('Read draft')).toBeVisible()
    expect(await panel().findByText('Read draft')).toBeVisible()
    expect(card(first.id).queryByText('Review sources')).not.toBeInTheDocument()
    expect(panel().queryByText('Review sources')).not.toBeInTheDocument()

    view.rerender(<Harness activities={[todos('first', 'Revise draft'), sibling]} />)
    expect(await card(first.id).findByText('Revise draft')).toBeVisible()
    expect(panel().getByText('Revise draft')).toBeVisible()
    expect(screen.queryAllByText('Read draft')).toHaveLength(0)

    view.rerender(<Harness activities={[todos('first'), sibling]} />)
    expect(screen.queryAllByText('Revise draft')).toHaveLength(0)
    expect(card(second.id).getByText('Review sources')).toBeVisible()
    expect(panel().queryByText('Review sources')).not.toBeInTheDocument()
  })

  it('withholds a previously opened panel on a different branch that reuses the tool call', async () => {
    const view = render(<Harness />)
    await openPanelFor(first.id)
    expect(await panel().findByText('Brief first')).toBeVisible()

    view.rerender(<Harness messages={secondOnly} showPanel={false} />)
    expect(await card(second.id).findByText('Evidence second')).toBeVisible()
    view.rerender(<Harness messages={secondOnly} />)
    for (const value of [
      'Researcher',
      'Brief first',
      'Evidence first',
      'Evidence second',
      'Source second',
    ]) {
      expect(panel().queryByText(value)).not.toBeInTheDocument()
    }
  })
})
