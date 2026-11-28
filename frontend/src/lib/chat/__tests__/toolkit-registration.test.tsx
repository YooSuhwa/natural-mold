import { describe, expect, it } from 'vitest'
import { Provider, createStore } from 'jotai'
import {
  AssistantRuntimeProvider,
  AuiConfig,
  MessagePrimitive,
  ThreadPrimitive,
  Tools,
  useExternalStoreRuntime,
  type ThreadMessageLike,
  type Toolkit,
} from '@assistant-ui/react'
import { render, screen, waitFor } from '../../../../tests/test-utils'
import { AssistantMessageParts } from '@/components/chat/assistant-message-parts'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import { ALL_TOOLKIT, BUILDER_TOOLKIT, SETTINGS_TEST_TOOLKIT } from '@/lib/chat/tool-ui-registry'

interface SourceMessage {
  readonly id: string
  readonly toolName: string
  readonly result?: string
}

function convertMessage(message: SourceMessage): ThreadMessageLike {
  return {
    id: message.id,
    role: 'assistant',
    content: [
      {
        type: 'tool-call',
        toolCallId: `call-${message.id}`,
        toolName: message.toolName,
        args: message.toolName === 'write_todos' ? { todos: [] } : { input: 'value' },
        result: message.result ?? 'done',
      },
    ],
  }
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root>
      <AssistantMessageParts />
    </MessagePrimitive.Root>
  )
}

function Harness({
  toolName,
  toolkit,
  result,
}: {
  readonly toolName: string
  readonly toolkit: Toolkit
  readonly result?: string
}) {
  const runtime = useExternalStoreRuntime<SourceMessage>({
    messages: [{ id: 'message-1', toolName, result }],
    isRunning: false,
    onNew: async () => {},
    convertMessage,
  })
  const config = AuiConfig({ tools: Tools({ toolkit }) })

  return (
    <AssistantRuntimeProvider runtime={runtime} config={config}>
      <ThreadPrimitive.Root>
        <ThreadPrimitive.Viewport>
          <ThreadPrimitive.Messages components={{ AssistantMessage, UserMessage: () => null }} />
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  )
}

describe('assistant-ui toolkit registration', () => {
  it('renders a registered Moldy tool through the real Tools resource', async () => {
    render(<Harness toolName="write_todos" toolkit={ALL_TOOLKIT} />)

    await waitFor(() => expect(screen.getByText('Plan')).toBeInTheDocument())
    expect(screen.queryByText('write_todos')).not.toBeInTheDocument()
  })

  it('renders the responding research provider and every original source from a saved tool result', async () => {
    const results = Array.from({ length: 6 }, (_, i) => ({
      title: i === 0 ? '' : `Article ${i + 1}`,
      url: `https://publisher.example/papers/${i + 1}?lang=ko#summary`,
      provider: 'searxng',
      published_date: '2026-11-24T00:00:00+00:00',
    }))
    render(
      <Harness
        toolName="research_search"
        toolkit={ALL_TOOLKIT}
        result={JSON.stringify({ query: 'AI', provider: 'searxng', results })}
      />,
    )
    expect(await screen.findByText('SearXNG · 6건')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: results[0].url })).toHaveAttribute(
      'href',
      results[0].url,
    )
    expect(screen.getByRole('link', { name: 'Article 6' })).toHaveAttribute('href', results[5].url)
    expect(screen.getAllByText(/발행일.*2026/)).toHaveLength(6)
  })

  it('restores a failed research call even when its old tool message has no error flag', async () => {
    const error = 'Research search is temporarily unavailable. Retry later.'
    render(<Harness toolName="research_search" toolkit={ALL_TOOLKIT} result={error} />)
    expect(await screen.findByText(error)).toBeInTheDocument()
    expect(screen.queryByText('1건')).not.toBeInTheDocument()
  })

  it('keeps the responding provider for a successful search with no sources', async () => {
    const result = JSON.stringify({ provider: 'tavily', results: [] })
    render(<Harness toolName="research_search" toolkit={ALL_TOOLKIT} result={result} />)
    expect(await screen.findByText('Tavily · 0건')).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('renders an unknown tool through the Moldy grouped-parts fallback', async () => {
    const store = createStore()
    const view = render(
      <Provider store={store}>
        <Harness toolName="unknown_backend_tool" toolkit={ALL_TOOLKIT} />
      </Provider>,
    )

    await waitFor(() => expect(screen.getByText('unknown_backend_tool')).toBeInTheDocument())
    view.getByRole('button', { name: 'Expand to panel' }).click()
    expect(store.get(chatRightRailAtom)).toMatchObject({
      mode: 'tool-result',
      toolResult: {
        toolCallId: 'call-message-1',
        toolName: 'unknown_backend_tool',
      },
    })
  })

  it('keeps approval renderers in main chat and AssistantPanel policy', () => {
    expect(ALL_TOOLKIT.ask_user?.render).toBeTypeOf('function')
    expect(ALL_TOOLKIT.request_approval?.render).toBeTypeOf('function')
  })

  it('excludes only HITL renderers from the settings test-chat policy', () => {
    expect(SETTINGS_TEST_TOOLKIT.ask_user).toBeUndefined()
    expect(SETTINGS_TEST_TOOLKIT.request_approval).toBeUndefined()
    expect(SETTINGS_TEST_TOOLKIT.ask_clarifying_question?.render).toBeTypeOf('function')
    expect(SETTINGS_TEST_TOOLKIT.write_todos?.render).toBeTypeOf('function')
  })

  it('keeps builder controls and approvals in the builder policy', () => {
    expect(BUILDER_TOOLKIT.ask_user?.render).toBeTypeOf('function')
    expect(BUILDER_TOOLKIT.phase_timeline?.render).toBeTypeOf('function')
    expect(BUILDER_TOOLKIT.recommendation_approval?.render).toBeTypeOf('function')
    expect(BUILDER_TOOLKIT.prompt_approval?.render).toBeTypeOf('function')
    expect(BUILDER_TOOLKIT.image_approval?.render).toBeTypeOf('function')
    expect(BUILDER_TOOLKIT.draft_approval?.render).toBeTypeOf('function')
  })
})
