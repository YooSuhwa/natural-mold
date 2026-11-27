'use client'

import { useEffect, useSyncExternalStore, type ReactNode } from 'react'
import { AuiConfig, AssistantRuntimeProvider, type AssistantRuntime } from '@assistant-ui/react'
import type { AnyStream } from '@langchain/react'
import { AssistantThread, type AssistantThreadProps } from '@/components/chat/assistant-thread'
import { FailedMessageRetryProvider } from '@/components/chat/failed-message-retry'
import { ScopedSubagentRuntimeProvider } from '@/components/chat/delegated-child-tasks'
import { HiTLContext, type HiTLContextValue } from '@/lib/chat/hitl-context'
import { ALL_DATA_UI } from '@/lib/chat/data-ui'
import { ALL_TOOLKIT, createMoldyChatTools } from '@/lib/chat/tool-ui-registry'
import type { PartialModelOutput } from '@/lib/chat/langgraph-runtime/partial-model-output'
import { ServerMessageQueueProvider } from '@/lib/chat/message-queue/server-message-queue-context'
import type {
  ConversationRunInput,
  ServerMessageQueueController,
} from '@/lib/chat/message-queue/server-message-queue-contract'
import { useFailedInputRetryAction } from '@/lib/chat/commands/failed-input-retry'

const EMPTY_SUBSCRIBE = (): (() => void) => () => {}
const EMPTY_QUEUE_SNAPSHOT = (): null => null

export type ThreadRenderProps = Pick<
  AssistantThreadProps,
  | 'agentImageUrl'
  | 'agentName'
  | 'readOnlyConversation'
  | 'composerHint'
  | 'enableRunSteer'
  | 'conversationId'
  | 'contextWindow'
  | 'dictationAvailability'
  | 'emptyContent'
  | 'modelName'
  | 'showContextGauge'
  | 'user'
  | 'onDictationStart'
  | 'commandActions'
  | 'linkedSkills'
  | 'resourceContextResetKey'
  | 'compact'
  | 'threadHeader'
> & { readonly retryLastFailedRunId?: string }

interface RuntimeFrameProps {
  readonly activities?: AssistantThreadProps['activities']
  readonly deepAgentsState?: AssistantThreadProps['deepAgentsState']
  readonly hitlValue: HiTLContextValue
  readonly runtime: AssistantRuntime
  readonly failedRunId?: string
  readonly partialOutput?: PartialModelOutput | null
  readonly messageQueue?: ServerMessageQueueController
  readonly resetContextOnQueueAcceptance?: boolean
  readonly retryFailedInput?: (input: ConversationRunInput) => Promise<void>
  readonly dataUI?: AssistantThreadProps['dataUI']
  readonly wrapThread?: (children: ReactNode) => ReactNode
  readonly subagentStream?: AnyStream | null
  readonly threadProps: ThreadRenderProps
}

export function RuntimeFrame({
  activities,
  deepAgentsState,
  failedRunId: terminalFailedRunId,
  partialOutput,
  hitlValue,
  runtime,
  messageQueue,
  resetContextOnQueueAcceptance = true,
  retryFailedInput,
  subagentStream,
  dataUI = ALL_DATA_UI,
  wrapThread,
  threadProps,
}: RuntimeFrameProps) {
  const failedRunId = threadProps.readOnlyConversation ? undefined : terminalFailedRunId
  const queueSnapshot = useSyncExternalStore(
    messageQueue?.subscribe ?? EMPTY_SUBSCRIBE,
    messageQueue?.getSnapshot ?? EMPTY_QUEUE_SNAPSHOT,
    () => null,
  )
  useEffect(() => {
    if (!messageQueue || !failedRunId) return
    void messageQueue.refresh()
  }, [failedRunId, messageQueue])
  const operation = queueSnapshot?.lastOperation
  const queueAcceptanceKey =
    operation?.kind === 'queued' || operation?.kind === 'applied' ? operation.inputId : null
  const retryLastFailedInput = useFailedInputRetryAction(
    queueSnapshot?.items ?? [],
    partialOutput === undefined ? failedRunId : undefined,
    retryFailedInput,
  )
  const { retryLastFailedRunId: _retryLastFailedRunId, ...assistantThreadProps } = threadProps
  void _retryLastFailedRunId
  const config = AuiConfig({
    tools: createMoldyChatTools(ALL_TOOLKIT, threadProps.conversationId),
  })

  const content = (
    <FailedMessageRetryProvider
      value={{
        failedRunId,
        partialOutput,
        retryAction: retryLastFailedInput,
      }}
    >
      <AssistantThread
        {...assistantThreadProps}
        commandActions={{
          ...assistantThreadProps.commandActions,
          retryLastFailedInput,
        }}
        retryLastFailedInput={retryLastFailedInput}
        resourceContextResetKey={
          (resetContextOnQueueAcceptance ? queueAcceptanceKey : null) ??
          threadProps.resourceContextResetKey
        }
        activities={activities}
        dataUI={dataUI}
        deepAgentsState={deepAgentsState}
        showTokenBar
        showMessageTimestamp
        enableAttachments
        enableMessageQueue={Boolean(messageQueue)}
      />
    </FailedMessageRetryProvider>
  )
  const thread = (
    <HiTLContext.Provider value={hitlValue}>
      {wrapThread ? (
        wrapThread(content)
      ) : (
        <ScopedSubagentRuntimeProvider
          conversationId={threadProps.conversationId}
          stream={subagentStream}
        >
          {content}
        </ScopedSubagentRuntimeProvider>
      )}
    </HiTLContext.Provider>
  )

  return (
    <AssistantRuntimeProvider runtime={runtime} config={config}>
      {messageQueue ? (
        <ServerMessageQueueProvider controller={messageQueue}>{thread}</ServerMessageQueueProvider>
      ) : (
        thread
      )}
    </AssistantRuntimeProvider>
  )
}
