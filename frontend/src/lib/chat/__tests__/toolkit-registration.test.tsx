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
import {
  ALL_TOOLKIT,
  BUILDER_TOOLKIT,
  SETTINGS_TEST_TOOLKIT,
} from '@/lib/chat/tool-ui-registry'

interface SourceMessage {
  readonly id: string
  readonly toolName: string
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
        result: 'done',
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

function Harness({ toolName, toolkit }: { readonly toolName: string; readonly toolkit: Toolkit }) {
  const runtime = useExternalStoreRuntime<SourceMessage>({
    messages: [{ id: 'message-1', toolName }],
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
