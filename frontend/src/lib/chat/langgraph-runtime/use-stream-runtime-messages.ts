'use client'

import { useLayoutEffect, useMemo } from 'react'
import { useExternalMessageConverter } from '@assistant-ui/react'
import type { BaseMessage } from '@langchain/core/messages'
import type { UseStreamReturn } from '@langchain/react'
import { useLangGraphArtifactEffects } from './artifact-events'
import { useLangGraphCompactionEffects } from './compaction-events'
import { useLangGraphDataUIEffects } from './data-ui-events'
import { useLangGraphMemoryEffects } from './memory-events'
import { useLangGraphMemoryRecallEffects } from './memory-recall-events'
import { useLangGraphSkillBuilderEffects } from './skill-builder-events'
import { useLangGraphSubagentNamesEffects } from './subagent-names-events'
import { useLangGraphUsageEffects } from './usage-events'
import { convertMoldyLangChainMessage } from './langchain-message-conversion'
import { useStableConvertedMessages } from './message-list'
import { attachMcpAppsMetadata } from '@/lib/chat/mcp-apps/metadata'
import {
  applyPendingEditConvertedBranchMetadata,
  suppressPendingEditConvertedDuplicate,
  suppressPendingEditConvertedStaleTail,
} from './stream-edit-converted'
import { appendTerminalRunNotice } from './stream-branch-metadata'
import type { PendingEditRenderState, PendingReloadRenderState } from './stream-edit-reload-types'
import {
  messageListFingerprint,
  useStickyConversationMessages,
  useStickyConvertedMessages,
  visibleMessagesWithIds,
} from './stream-message-projection'
import type { ThreadRunNotice } from './stream-thread-state-projection'

interface UseStreamRuntimeMessagesOptions<StateType extends { messages?: readonly BaseMessage[] }> {
  readonly conversationId: string
  readonly stream: UseStreamReturn<StateType>
  readonly messagesWithInterrupts: readonly BaseMessage[]
  readonly interruptCount: number
  readonly claimedQueueRunInFlight: boolean
  readonly threadRunNotice: ThreadRunNotice | null
  readonly terminalNoticeText: string
  readonly hydratedMessagesPresent: boolean
  readonly pendingEdit: PendingEditRenderState | null
  readonly pendingReload: PendingReloadRenderState | null
  readonly settling: boolean
  readonly recordLatestVisibleMessages: (messages: readonly BaseMessage[]) => void
}

export function useStreamRuntimeMessages<StateType extends { messages?: readonly BaseMessage[] }>({
  conversationId,
  stream,
  messagesWithInterrupts,
  interruptCount,
  claimedQueueRunInFlight,
  threadRunNotice,
  terminalNoticeText,
  hydratedMessagesPresent,
  pendingEdit,
  pendingReload,
  settling,
  recordLatestVisibleMessages,
}: UseStreamRuntimeMessagesOptions<StateType>) {
  const artifacts = useLangGraphArtifactEffects({
    stream,
    conversationId,
    messages: messagesWithInterrupts,
  })
  const usage = useLangGraphUsageEffects({
    conversationId,
    stream,
    messages: artifacts,
    stateMessages: stream.values?.messages ?? [],
  })
  const compacted = useLangGraphCompactionEffects({ stream, messages: usage })
  const terminal = useMemo(
    () => appendTerminalRunNotice(compacted, threadRunNotice, terminalNoticeText),
    [compacted, terminalNoticeText, threadRunNotice],
  )
  const stickyBase = useStickyConversationMessages(
    conversationId,
    terminal,
    hydratedMessagesPresent || pendingEdit !== null || pendingReload !== null,
  )
  const withDataUI = useLangGraphDataUIEffects({ stream, conversationId, messages: stickyBase })
  useLayoutEffect(
    () => recordLatestVisibleMessages(stickyBase),
    [recordLatestVisibleMessages, stickyBase],
  )
  const fingerprint = useMemo(() => messageListFingerprint(withDataUI), [withDataUI])
  const conversionCallback = useMemo<typeof convertMoldyLangChainMessage>(() => {
    const epoch = fingerprint
    return (message, metadata) => {
      void epoch
      return convertMoldyLangChainMessage(message, metadata)
    }
  }, [fingerprint])
  useLangGraphMemoryEffects({ stream })
  useLangGraphSubagentNamesEffects({ stream, conversationId })
  useLangGraphMemoryRecallEffects({ stream, conversationId })
  useLangGraphSkillBuilderEffects({ stream, conversationId })
  const isRunning =
    (stream.isLoading || claimedQueueRunInFlight) &&
    interruptCount === 0 &&
    threadRunNotice?.status !== 'stale' &&
    threadRunNotice?.status !== 'failed'
  const conversionMessages = useMemo(() => [...withDataUI], [withDataUI])
  const converted = useExternalMessageConverter({
    callback: conversionCallback,
    messages: conversionMessages,
    isRunning,
  })
  const withMcpApps = useMemo(() => attachMcpAppsMetadata(converted), [converted])
  const stable = useStableConvertedMessages(withMcpApps, withDataUI, isRunning)
  const branchMetadata = useMemo(
    () => applyPendingEditConvertedBranchMetadata(stable, pendingEdit),
    [pendingEdit, stable],
  )
  const withoutStale = useMemo(
    () => suppressPendingEditConvertedStaleTail(branchMetadata, pendingEdit),
    [branchMetadata, pendingEdit],
  )
  const withoutDuplicate = useMemo(
    () => suppressPendingEditConvertedDuplicate(withoutStale, pendingEdit),
    [pendingEdit, withoutStale],
  )
  const stickyConverted = useStickyConvertedMessages(
    conversationId,
    withoutDuplicate,
    settling,
    pendingEdit !== null || pendingReload !== null,
  )
  const messages = useMemo(
    () => suppressPendingEditConvertedStaleTail(stickyConverted, pendingEdit),
    [pendingEdit, stickyConverted],
  )
  const checkpointVisibleMessages = useMemo(
    () => visibleMessagesWithIds(messages, stickyBase),
    [messages, stickyBase],
  )
  return {
    messages,
    checkpointVisibleMessages,
    langChainMessages: stickyBase,
    runtimeIsRunning: isRunning || pendingEdit !== null || pendingReload !== null,
  }
}
