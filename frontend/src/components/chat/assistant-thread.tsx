'use client'

import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type UIEvent,
} from 'react'
import {
  AuiIf,
  ThreadPrimitive,
  useThreadViewport,
  type AssistantDataUI,
} from '@assistant-ui/react'
import { ArrowDownIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { ChatSearchOverlay } from '@/components/chat/chat-search-overlay'
import { MissionControlBar } from '@/components/chat/mission-control-bar'
import { SubagentTeamStrip } from '@/components/chat/subagent-team-strip'
import { MemoryRecallChip } from '@/components/chat/memory-recall-chip'
import { ReconnectIndicator } from '@/components/chat/reconnect-indicator'
import { ChatConversationContext } from '@/components/chat/conversation-context'
import {
  AssistantThreadDynamicContext,
  type AssistantThreadDynamicContextValue,
} from '@/components/chat/assistant-thread-context'
import { ASSISTANT_THREAD_MESSAGE_COMPONENTS } from '@/components/chat/assistant-thread-message-renderers'
import { ThreadComposer } from '@/components/chat/assistant-thread-composer'
import type { DictationAvailability } from '@/components/chat/use-browser-dictation'
import {
  BuilderComposer,
  BuilderComposerFallback,
} from '@/components/chat/assistant-builder-overrides'
import { isThreadViewportAtBottom } from '@/components/chat/scroll-bottom'
import { cn } from '@/lib/utils'
import type { User } from '@/lib/types/user'
import type { DeepAgentsStateSnapshot } from '@/lib/chat/langgraph-runtime/deepagents-state'
import type { RunActivity } from '@/lib/chat/langgraph-runtime/activity-model'
import type { ChatCommandActions } from '@/lib/chat/commands/chat-command-types'
import type { SkillBrief } from '@/lib/types'
import 'katex/dist/katex.min.css'
import { useSideChat } from '@/components/chat/side-chat/side-chat-context'
import './markdown-styles.css'

export { GenericToolFallback } from '@/components/chat/tool-ui/generic-tool-ui'
export {
  groupAssistantParts,
  renderGroupedAssistantPart,
} from '@/components/chat/assistant-message-parts'

export interface AssistantThreadProps {
  threadHeader?: ReactNode
  agentImageUrl?: string | null
  agentImagePublicAsset?: boolean
  agentName?: string
  user?: User | null
  modelName?: string
  showTokenBar?: boolean
  showContextGauge?: boolean
  contextWindow?: number | null
  compact?: boolean
  showMessageTimestamp?: boolean
  emptyContent?: ReactNode
  dataUI?: readonly AssistantDataUI[]
  activities?: readonly RunActivity[]
  deepAgentsState?: DeepAgentsStateSnapshot
  enableAttachments?: boolean
  enableMessageQueue?: boolean
  conversationId?: string
  variant?: 'default' | 'builder'
  builderModelLabel?: string
  builderAgentSubtitle?: string
  composerHint?: ReactNode
  dictationAvailability?: DictationAvailability
  onDictationStart?: () => void
  commandActions?: ChatCommandActions
  linkedSkills?: readonly SkillBrief[]
  retryLastFailedInput?: ChatCommandActions['retryLastFailedInput']
  resourceContextResetKey?: string | number | null
}

export function AssistantThread({
  threadHeader,
  agentImageUrl,
  agentImagePublicAsset = false,
  agentName,
  user,
  modelName,
  showTokenBar = false,
  showContextGauge = false,
  contextWindow,
  compact = false,
  showMessageTimestamp = false,
  emptyContent,
  dataUI,
  activities = [],
  deepAgentsState,
  enableAttachments = false,
  enableMessageQueue = false,
  conversationId,
  variant = 'default',
  builderModelLabel,
  builderAgentSubtitle,
  composerHint,
  dictationAvailability,
  onDictationStart,
  commandActions,
  linkedSkills,
  retryLastFailedInput,
  resourceContextResetKey,
}: AssistantThreadProps) {
  const sideChat = useSideChat()
  const tPage = useTranslations('chat.page')
  const isBuilder = variant === 'builder'
  const [isViewportAtBottom, setIsViewportAtBottom] = useState(true)
  const handleViewportScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
    const nextIsAtBottom = isThreadViewportAtBottom(event.currentTarget)
    setIsViewportAtBottom((current) => (current === nextIsAtBottom ? current : nextIsAtBottom))
  }, [])
  const dynamicContextValue = useMemo<AssistantThreadDynamicContextValue>(
    () => ({
      activities,
      agentImagePublicAsset,
      agentImageUrl,
      agentName,
      builderAgentSubtitle,
      deepAgentsState,
      isBuilder,
      showMessageTimestamp,
      user,
    }),
    [
      activities,
      agentImagePublicAsset,
      agentImageUrl,
      agentName,
      builderAgentSubtitle,
      deepAgentsState,
      isBuilder,
      showMessageTimestamp,
      user,
    ],
  )
  const [searchQuery, setSearchQuery] = useState<string | null>(null)
  const viewportRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleGlobalKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'f') {
        if (viewportRef.current?.offsetParent == null) return
        event.preventDefault()
        setSearchQuery('')
      }
    }
    window.addEventListener('keydown', handleGlobalKeyDown)
    return () => window.removeEventListener('keydown', handleGlobalKeyDown)
  }, [])

  return (
    <AssistantThreadDynamicContext.Provider value={dynamicContextValue}>
      <ChatConversationContext.Provider value={conversationId ?? null}>
        <ThreadPrimitive.Root
          className="flex h-full min-h-0 flex-col"
          data-chat-selection-thread={sideChat && !isBuilder ? conversationId : undefined}
          data-chat-selection-title={
            sideChat && conversationId === sideChat.mainId ? sideChat.mainTitle : agentName
          }
        >
          {threadHeader}
          {!isBuilder && deepAgentsState && deepAgentsState.todos.length > 0 ? (
            <MissionControlBar todos={deepAgentsState.todos} />
          ) : null}
          {!isBuilder ? <SubagentTeamStrip /> : null}
          {!isBuilder ? <MemoryRecallChip /> : null}
          <ThreadPrimitive.Viewport
            ref={viewportRef}
            className="min-h-0 flex-1 overflow-y-auto"
            onScroll={handleViewportScroll}
          >
            {searchQuery !== null ? (
              <ChatSearchOverlay
                initialQuery={searchQuery}
                onClose={() => setSearchQuery(null)}
                searchRootRef={viewportRef}
              />
            ) : null}
            <AuiIf condition={(state) => state.thread.isEmpty}>
              {emptyContent ?? (
                <div className="flex h-full items-center justify-center py-8 text-center text-muted-foreground">
                  <p className="text-sm">{tPage('emptyState')}</p>
                </div>
              )}
            </AuiIf>
            <div
              className={cn(
                'mx-auto w-full px-4 py-4',
                isBuilder ? 'max-w-4xl space-y-6' : 'max-w-3xl space-y-4',
              )}
            >
              <ThreadPrimitive.Messages>
                {({ message }) => {
                  const Component = message.composer.isEditing
                    ? ASSISTANT_THREAD_MESSAGE_COMPONENTS.UserEditComposer
                    : message.role === 'user'
                      ? ASSISTANT_THREAD_MESSAGE_COMPONENTS.UserMessage
                      : ASSISTANT_THREAD_MESSAGE_COMPONENTS.AssistantMessage
                  return <Component />
                }}
              </ThreadPrimitive.Messages>
            </div>
            <ThreadPrimitive.ViewportFooter className="pointer-events-none sticky bottom-0 z-10 flex justify-center pb-2">
              <ScrollToBottomButton isAtBottom={isViewportAtBottom} />
            </ThreadPrimitive.ViewportFooter>
          </ThreadPrimitive.Viewport>
          {dataUI?.map((DataComponent, index) => (
            <DataComponent key={`data-${index}`} />
          ))}
          <ReconnectIndicator />
          {isBuilder ? (
            <Suspense fallback={<BuilderComposerFallback />}>
              <BuilderComposer modelLabel={builderModelLabel} />
            </Suspense>
          ) : (
            <div className="mx-auto w-full max-w-3xl px-4 pb-4">
              {composerHint}
              <ThreadComposer
                modelName={modelName}
                showTokenBar={showTokenBar}
                showContextGauge={showContextGauge}
                contextWindow={contextWindow}
                compact={compact}
                enableAttachments={enableAttachments}
                enableMessageQueue={enableMessageQueue}
                focusKey={conversationId}
                dictationAvailability={dictationAvailability}
                onDictationStart={onDictationStart}
                commandActions={{
                  ...commandActions,
                  openTranscriptSearch: (argument) => setSearchQuery(argument),
                }}
                linkedSkills={linkedSkills}
                retryLastFailedInput={retryLastFailedInput}
                resourceContextResetKey={resourceContextResetKey}
              />
            </div>
          )}
        </ThreadPrimitive.Root>
      </ChatConversationContext.Provider>
    </AssistantThreadDynamicContext.Provider>
  )
}

function ScrollToBottomButton({ isAtBottom }: { readonly isAtBottom: boolean }) {
  const t = useTranslations('chat.input')
  const scrollToBottom = useThreadViewport((viewport) => viewport.scrollToBottom)
  return (
    <button
      type="button"
      aria-label={t('scrollToBottom')}
      aria-hidden={isAtBottom}
      disabled={isAtBottom}
      tabIndex={isAtBottom ? -1 : 0}
      className={cn(
        'moldy-floating-icon-button flex size-8 items-center justify-center text-muted-foreground',
        isAtBottom ? 'pointer-events-none opacity-0' : 'pointer-events-auto opacity-100',
      )}
      onClick={() => scrollToBottom()}
    >
      <ArrowDownIcon className="size-4" />
    </button>
  )
}
