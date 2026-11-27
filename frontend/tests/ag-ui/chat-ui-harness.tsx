/** Synthetic transport, real AG-UI runtime, Moldy renderers and scoped child panel. */
import { useSyncExternalStore, type ReactNode } from 'react'
import { type HttpAgent } from '@ag-ui/client'
import {
  AuiConfig,
  AssistantRuntimeProvider,
  MessagePrimitive,
  ThreadPrimitive,
} from '@assistant-ui/react'
import { useAgUiRuntime } from '@assistant-ui/react-ag-ui'

import { AssistantMessageParts } from '@/components/chat/assistant-message-parts'
import { AssistantCompactionMarker } from '@/components/chat/assistant-message-artifacts'
import { ChatConversationContext } from '@/components/chat/conversation-context'
import { AgUiSubagentRuntimeProvider } from '@/lib/chat/langgraph-runtime/subagent-runtime'
import { ALL_TOOLKIT, createMoldyChatTools } from '@/lib/chat/tool-ui-registry'
import { MOLDY_AG_UI_DATA_UI } from '@/lib/ag-ui/activity-data-ui'

import { AgUiActivityProvider } from '@/lib/ag-ui/activity-context'
import type { createAgUiChatSession } from '@/lib/ag-ui/chat-history'
import { conversationId } from './chat-ui-fixtures'
export { conversationId } from './chat-ui-fixtures'

function AssistantParts() {
  return (
    <MessagePrimitive.Root>
      <AssistantMessageParts />
      <AssistantCompactionMarker />
    </MessagePrimitive.Root>
  )
}

interface AgUiTestChatProps {
  readonly session: ReturnType<typeof createAgUiChatSession>
  readonly agent: HttpAgent
  readonly children?: ReactNode
  readonly messagesVisible?: boolean
  readonly restore?: boolean
}

export function AgUiTestChat({
  agent,
  session,
  children,
  messagesVisible = true,
  restore = false,
}: AgUiTestChatProps) {
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot)
  const runtime = useAgUiRuntime({
    agent,
    adapters: restore ? { history: session.history } : undefined,
  })
  const config = AuiConfig({ tools: createMoldyChatTools(ALL_TOOLKIT, conversationId) })
  return (
    <AssistantRuntimeProvider runtime={runtime} config={config}>
      <ChatConversationContext.Provider value={conversationId}>
        <AgUiActivityProvider
          conversationId={conversationId}
          activities={snapshot.activities}
          links={snapshot.messageLinks}
        >
          <AgUiSubagentRuntimeProvider conversationId={conversationId}>
            <ThreadPrimitive.Root>
              {messagesVisible && (
                <ThreadPrimitive.Messages
                  components={{ AssistantMessage: AssistantParts, UserMessage: () => null }}
                />
              )}
              {MOLDY_AG_UI_DATA_UI.map((DataUI, index) => (
                <DataUI key={index} />
              ))}
              <button
                type="button"
                onClick={() => {
                  void runtime.thread.append({
                    role: 'user',
                    content: [{ type: 'text', text: 'Summarize the records' }],
                  })
                }}
              >
                Send fixture
              </button>
            </ThreadPrimitive.Root>
            {children}
          </AgUiSubagentRuntimeProvider>
        </AgUiActivityProvider>
      </ChatConversationContext.Provider>
    </AssistantRuntimeProvider>
  )
}
