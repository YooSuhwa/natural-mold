'use client'

import { Suspense } from 'react'
import { MessagePrimitive, useAuiState } from '@assistant-ui/react'
import { AlertTriangleIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { UserAvatar } from '@/components/auth/UserAvatar'
import { UserMessageAttachments } from '@/components/chat/message-attachments'
import { StreamingMessageLoadingIndicator } from '@/components/chat/assistant-message-loading'
import { TokenUsagePopover } from '@/components/chat/token-usage-popover'
import { BranchPicker } from '@/components/chat/assistant-branch-picker'
import {
  BuilderAssistantMessage,
  BuilderAssistantMessageParts,
  BuilderMessageFallback,
  BuilderUserEditComposer,
  BuilderUserMessage,
} from '@/components/chat/assistant-builder-overrides'
import {
  CopyButton,
  EditButton,
  FeedbackButtons,
  MessageMetaRow,
  MessageTimestamp,
  RegenerateButton,
  RetryButton,
  UserMessageEditor,
} from '@/components/chat/assistant-message-actions'
import {
  AssistantArtifactCards,
  AssistantCompactionMarker,
} from '@/components/chat/assistant-message-artifacts'
import { AssistantMessageParts } from '@/components/chat/assistant-message-parts'
import { useAssistantThreadDynamicContext } from '@/components/chat/assistant-thread-context'
import type { TerminalNoticeStatus } from '@/lib/chat/langgraph-runtime/terminal-notice'

function UserMessage() {
  const { isBuilder, showMessageTimestamp, user } = useAssistantThreadDynamicContext()
  const messageId = useAuiState((s) => s.message?.id)
  const metaRow = (
    <MessageMetaRow>
      <BranchPicker />
      <EditButton />
      <CopyButton />
      {showMessageTimestamp && <MessageTimestamp />}
    </MessageMetaRow>
  )
  if (isBuilder) {
    return (
      <Suspense fallback={<BuilderMessageFallback />}>
        <BuilderUserMessage metaRow={metaRow} />
      </Suspense>
    )
  }
  return (
    <div
      className="group relative flex justify-end gap-3"
      data-moldy-message-id={messageId}
      data-moldy-message-role="user"
    >
      <div className="flex w-full max-w-[80%] flex-col items-end">
        <div className="moldy-chat-bubble-user px-4 py-2.5 text-sm leading-relaxed">
          <MessagePrimitive.Content />
        </div>
        <UserMessageAttachments />
        {metaRow}
      </div>
      <UserAvatar user={user} size="sm" />
    </div>
  )
}

function UserEditComposer() {
  const { isBuilder, user } = useAssistantThreadDynamicContext()
  if (isBuilder) {
    return (
      <Suspense fallback={<BuilderMessageFallback />}>
        <BuilderUserEditComposer />
      </Suspense>
    )
  }
  return (
    <div className="flex justify-end gap-3">
      <div className="flex w-full max-w-[80%] flex-col items-end">
        <UserMessageEditor />
      </div>
      <UserAvatar user={user} size="sm" />
    </div>
  )
}

function AssistantMessage() {
  const {
    activities,
    agentImagePublicAsset,
    agentImageUrl,
    agentName,
    builderAgentSubtitle,
    deepAgentsState,
    isBuilder,
    showMessageTimestamp,
  } = useAssistantThreadDynamicContext()
  const messageId = useAuiState((s) => s.message?.id)
  const tChat = useTranslations('chat')
  const terminalNotice = useAuiState(
    (s) =>
      (s.message?.metadata as { custom?: { terminalNotice?: TerminalNoticeStatus } } | undefined)
        ?.custom?.terminalNotice,
  )
  const isFailedNotice = terminalNotice === 'failed'
  const metaRow = isFailedNotice ? null : (
    <MessageMetaRow>
      <BranchPicker />
      <CopyButton />
      <RegenerateButton />
      <FeedbackButtons />
      <TokenUsagePopover />
      {showMessageTimestamp && <MessageTimestamp />}
    </MessageMetaRow>
  )
  if (isBuilder) {
    return (
      <Suspense fallback={<BuilderMessageFallback />}>
        <BuilderAssistantMessage metaRow={metaRow} agentSubtitle={builderAgentSubtitle}>
          <StreamingMessageLoadingIndicator
            activities={activities}
            deepAgentsState={deepAgentsState}
          />
          <BuilderAssistantMessageParts />
        </BuilderAssistantMessage>
      </Suspense>
    )
  }
  return (
    <div
      className="group relative flex gap-3"
      data-moldy-message-id={messageId}
      data-moldy-message-role="assistant"
    >
      <AgentAvatar
        imageUrl={agentImageUrl ?? null}
        name={agentName ?? tChat('defaultAgentName')}
        size="sm"
        publicAsset={agentImagePublicAsset}
      />
      <div className="min-w-0 flex-1">
        <StreamingMessageLoadingIndicator
          activities={activities}
          deepAgentsState={deepAgentsState}
        />
        {isFailedNotice ? (
          <div className="moldy-status-surface moldy-status-danger flex items-start gap-2 rounded-lg px-3 py-2.5 leading-normal">
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 flex-1 space-y-2">
              <AssistantMessageParts />
              <RetryButton />
            </div>
          </div>
        ) : (
          <>
            <AssistantMessageParts />
            <AssistantArtifactCards />
            <AssistantCompactionMarker />
          </>
        )}
        {metaRow}
      </div>
    </div>
  )
}

export const ASSISTANT_THREAD_MESSAGE_COMPONENTS = {
  UserMessage,
  UserEditComposer,
  AssistantMessage,
} as const
