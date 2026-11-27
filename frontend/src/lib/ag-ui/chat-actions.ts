'use client'

import type { AppendMessage, AttachmentAdapter } from '@assistant-ui/react'
import { useCallback, useLayoutEffect, useMemo, useRef } from 'react'

import type { ConversationRunInput } from '@/lib/api/conversation-run-inputs'
import { withResourceContextRunInput } from '@/lib/chat/context/resource-context-payload'
import { shouldRouteComposerToServerQueue } from '@/lib/chat/message-queue/server-message-queue'
import type { ServerMessageQueueOptions } from '@/lib/chat/message-queue/server-message-queue-contract'
import { waitForChatQueueReceipt } from './chat-queue-receipt'
import { useServerMessageQueue } from '@/lib/chat/message-queue/use-server-message-queue'
import { useCancelConversationRun } from '@/lib/hooks/use-conversation-runs'
import type { AgUiChatActions } from './chat-action-context'
import { findChatMessageLink, type ChatMessageIdentity } from './chat-message-identity'
import type { MoldyAgUiAgent } from './moldy-agent'

interface ChatActionOptions {
  agent: Pick<MoldyAgUiAgent, 'readSnapshot' | 'submitQueuedInput' | 'retryFailedInput'>
  conversationId: string
  attachmentAdapter?: AttachmentAdapter
  activeRunId: string | null
  activePhysicalRunId: string | null
  onClaimedRun: (physicalRunId: string) => void
  runtimeIsRunning?: boolean
}

function queuedDraft(message: AppendMessage, requestId: string) {
  if (message.role !== 'user') throw new Error('Queued chat input must be a user message.')
  const context = withResourceContextRunInput({}, message.runConfig?.custom)
  if (context.kind !== 'valid') throw new Error('Invalid resource context.')
  return {
    id: requestId,
    text: message.content
      .filter((part) => part.type === 'text')
      .map((part) => part.text)
      .join('\n'),
    attachmentIds: (message.attachments ?? []).map((attachment) => attachment.id),
    ...(context.refs.length ? { resourceContext: { resources: context.refs } } : {}),
  }
}

export function useAgUiChatActions({
  agent,
  conversationId,
  attachmentAdapter,
  activeRunId,
  activePhysicalRunId,
  onClaimedRun,
  runtimeIsRunning = false,
}: ChatActionOptions) {
  const { mutateAsync: cancelRun } = useCancelConversationRun(conversationId)
  const scope = useMemo(
    () => ({
      conversationId,
      agent,
      draftRequests: new WeakMap<AppendMessage, { steer: boolean; id: string }>(),
      stopping: new Set<string>(),
      followed: new Set<string>(),
      retryRequests: new Map<string, string>(),
      retrying: new Set<string>(),
    }),
    [conversationId, agent],
  )
  const mounted = useRef<{ scope: typeof scope; lifetime: AbortController } | null>(null)
  useLayoutEffect(() => {
    const lifetime = new AbortController()
    mounted.current = { scope, lifetime }
    return () => {
      mounted.current = null
      lifetime.abort()
    }
  }, [scope])

  const followClaim = useCallback(
    (physicalId: string) => {
      if (mounted.current?.scope !== scope || scope.followed.has(physicalId)) return
      scope.followed.add(physicalId)
      if (physicalId !== activePhysicalRunId) onClaimedRun(physicalId)
    },
    [scope, onClaimedRun, activePhysicalRunId],
  )
  const submit = useCallback<ServerMessageQueueOptions['submit']>(
    (message, { strategy, requestId }) =>
      agent.submitQueuedInput(queuedDraft(message, requestId), strategy, requestId),
    [agent],
  )
  const { controller: messageQueue, snapshot } = useServerMessageQueue({
    conversationId,
    submit,
    onClaimedRun: followClaim,
  })
  const queueDraft = useCallback<AgUiChatActions['queueDraft']>(
    async (message, steer) => {
      const prior = messageQueue.getSnapshot()
      if (
        mounted.current?.scope !== scope ||
        prior.lastOperation.kind === 'sending' ||
        prior.lastOperation.kind === 'reconciling'
      ) {
        throw new Error('A chat input is still awaiting confirmation.')
      }
      const previous = scope.draftRequests.get(message)
      const id = previous?.steer === steer ? previous.id : crypto.randomUUID()
      scope.draftRequests.set(message, { steer, id })
      const signal = mounted.current.lifetime.signal
      if (steer) await messageQueue.steer(message, id)
      else await messageQueue.enqueue(message, id)
      await waitForChatQueueReceipt(messageQueue, id, signal)
      scope.draftRequests.delete(message)
    },
    [messageQueue, scope],
  )

  const retryFailedInput = useCallback(
    async (input: ConversationRunInput) => {
      if (mounted.current?.scope !== scope || scope.retrying.has(input.id)) return
      if (
        input.conversation_id !== conversationId ||
        !input.run_id ||
        (input.status !== 'failed' && input.status !== 'claimed')
      ) {
        throw new Error('The failed input does not belong to this conversation.')
      }
      // If both the receipt and reconciliation GET were lost, reuse the original command ID.
      const requestId = scope.retryRequests.get(input.id) ?? crypto.randomUUID()
      scope.retryRequests.set(input.id, requestId)
      scope.retrying.add(input.id)
      try {
        let runId: string | null | undefined
        try {
          const accepted = await agent.retryFailedInput(input.id, requestId)
          runId = accepted.runId
        } catch (error) {
          if (mounted.current?.scope !== scope) return
          const reconciled = await messageQueue.reconcileRequest(requestId)
          if (!reconciled) throw error
          runId = reconciled.run_id
        }
        if (mounted.current?.scope !== scope) return
        scope.retryRequests.delete(input.id)
        if (runId) followClaim(runId)
        await messageQueue.refresh()
      } finally {
        scope.retrying.delete(input.id)
      }
    },
    [scope, conversationId, agent, messageQueue, followClaim],
  )

  const resolveMessageId = useCallback(
    async (message: ChatMessageIdentity) => {
      const snapshot = await agent.readSnapshot()
      if (mounted.current?.scope !== scope) return undefined
      return findChatMessageLink(message, snapshot.messageLinks)?.uiMessageId ?? undefined
    },
    [agent, scope],
  )
  const getBranchRunConfig = useCallback<AgUiChatActions['getBranchRunConfig']>(
    async (operation, message, existing) => {
      const snapshot = await agent.readSnapshot()
      const link = findChatMessageLink(message, snapshot.messageLinks)
      if (mounted.current?.scope !== scope || !link?.parentCheckpointId || !snapshot.checkpointId) {
        throw new Error('The message has no active branch checkpoint.')
      }
      return {
        ...existing,
        custom: {
          ...existing?.custom,
          moldy: {
            operation,
            checkpointId: link.parentCheckpointId,
            expectedHead: snapshot.checkpointId,
          },
        },
      }
    },
    [agent, scope],
  )
  const stopActiveRun = useCallback(async () => {
    if (mounted.current?.scope !== scope || (!activeRunId && !activePhysicalRunId)) return
    const displayed = activePhysicalRunId ?? activeRunId
    if (!displayed || scope.stopping.has(displayed)) return
    scope.stopping.add(displayed)
    try {
      let physicalId = activePhysicalRunId
      if (!physicalId) {
        const selected = await agent.readSnapshot()
        if (selected.activeRunId !== activeRunId) return
        physicalId = selected.physicalRunId
      }
      if (mounted.current?.scope !== scope || !physicalId) return
      await cancelRun(physicalId)
      if (mounted.current?.scope === scope) await messageQueue.refresh()
    } finally {
      scope.stopping.delete(displayed)
    }
  }, [scope, activeRunId, activePhysicalRunId, agent, cancelRun, messageQueue])

  const shouldQueue = shouldRouteComposerToServerQueue(
    snapshot,
    runtimeIsRunning || Boolean(activeRunId || activePhysicalRunId),
  )
  const actions = useMemo<AgUiChatActions>(
    () => ({
      attachmentAdapter,
      queueDraft,
      getBranchRunConfig,
      resolveMessageId,
      stopActiveRun,
      shouldQueue,
      canStopActiveRun: Boolean(activeRunId || activePhysicalRunId),
    }),
    [
      attachmentAdapter,
      queueDraft,
      getBranchRunConfig,
      resolveMessageId,
      stopActiveRun,
      shouldQueue,
      activeRunId,
      activePhysicalRunId,
    ],
  )
  return { actions, messageQueue, retryFailedInput }
}
