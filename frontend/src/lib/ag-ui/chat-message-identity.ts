import type { ThreadMessage } from '@assistant-ui/react'
import type { AgUiMessageLink } from './chat-history-types'

export type ChatMessageIdentity = Pick<ThreadMessage, 'id' | 'metadata'>

/** The SDK can assign the reply a local ID before it receives the model's text message ID. */
export function wireChatMessageId(message: ChatMessageIdentity): string {
  const namespace = message.metadata?.custom?.agui
  if (
    namespace &&
    typeof namespace === 'object' &&
    'messageId' in namespace &&
    typeof namespace.messageId === 'string' &&
    namespace.messageId.trim()
  )
    return namespace.messageId
  return message.id
}

export function storedChatMessageId(message: ChatMessageIdentity): string | undefined {
  const publicId = message.metadata?.custom?.publicMessageId
  return typeof publicId === 'string' && publicId.trim() ? publicId : undefined
}

/** Only a unique root transcript row can be the target of a normal message action. */
export function findChatMessageLink(
  message: ChatMessageIdentity,
  links: readonly AgUiMessageLink[],
) {
  const wire = wireChatMessageId(message)
  const stored = storedChatMessageId(message)
  const candidates = links.filter(
    (link) =>
      !link.subagentRunId &&
      (link.messageId === wire || (wire === message.id && link.uiMessageId === message.id)) &&
      (!stored || link.uiMessageId === stored),
  )
  return candidates.length === 1 ? candidates[0] : undefined
}
