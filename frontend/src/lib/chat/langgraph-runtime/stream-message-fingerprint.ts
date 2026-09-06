import type { BaseMessage } from '@langchain/core/messages'
import { langChainMessageFingerprint, stableString } from './message-list'
import {
  branchMetadataFromMessage,
  isAssistantMessage,
  isHumanMessage,
  textContentFromMessageContent,
} from './stream-message-content'
import type { ConvertedMessage, SnapshotCloneableMessage } from './stream-message-types'
import { isRecord } from './stream-message-utilities'

export function convertedMessageId(message: ConvertedMessage | undefined): string | null {
  if (!isRecord(message)) return null
  return typeof message.id === 'string' ? message.id : null
}

export function isConvertedUserMessage(message: ConvertedMessage | undefined): boolean {
  return isRecord(message) && message.role === 'user'
}

export function convertedMessageContentRoleFingerprint(
  message: ConvertedMessage | undefined,
): string {
  const role = isRecord(message) && typeof message.role === 'string' ? message.role : null
  return stableString({
    type: role === 'user' ? 'human' : role === 'assistant' ? 'ai' : role,
    content: convertedMessageComparableContent(message),
  })
}

export function convertedMessageText(message: ConvertedMessage | undefined): string | null {
  if (!isRecord(message)) return null
  return textFromUnknownContent(message.content)
}

export function convertedMessageComparableContent(message: ConvertedMessage | undefined): unknown {
  if (!isRecord(message)) return null
  const text = convertedMessageText(message)
  return text !== null ? text : message.content
}

export function textFromUnknownContent(content: unknown): string | null {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return null
  const parts: string[] = []
  for (const part of content) {
    if (typeof part === 'string') {
      parts.push(part)
      continue
    }
    if (!isRecord(part)) return null
    const text = part.text
    if (typeof text !== 'string') return null
    parts.push(text)
  }
  return parts.join('')
}

export function messageContentFingerprint(message: BaseMessage | undefined): string {
  if (!message) return ''
  return stableString({
    type: typeof message._getType === 'function' ? message._getType() : undefined,
    name: message.name,
    content: message.content,
  })
}

export function messageContentRoleFingerprint(message: BaseMessage | undefined): string {
  if (!message) return ''
  return stableString({
    type: typeof message._getType === 'function' ? message._getType() : undefined,
    content: textContentFromMessageContent(message.content),
  })
}

// H4: the converter-cache invalidation key (`conversionEpoch`) must cover the
// SAME fields the converter reads as `useStableConvertedMessages`'s fingerprint
// (which delegates to `langChainMessageFingerprint`). `messageRenderKey` alone
// only fingerprints content/type plus checkpoint/branch metadata, so source
// changes in name/additional_kwargs/response_metadata/tool_call_id/usage_metadata
// would alter converted output yet leave this key (and the cache) stale. We
// union both so neither side can miss a converter-relevant change.
export function messageListFingerprint(messages: readonly BaseMessage[]): string {
  return stableString(
    messages.map(
      (message) => `${messageRenderKey(message)}|${langChainMessageFingerprint(message)}`,
    ),
  )
}

export function messageRenderKey(message: BaseMessage): string {
  const metadata = branchMetadataFromMessage(message)
  const source = message as SnapshotCloneableMessage
  return stableString({
    id: message.id ?? null,
    checkpointId: metadata?.checkpoint_id ?? null,
    branchCheckpointId: metadata?.branchCheckpointId ?? null,
    fingerprint: messageContentFingerprint(message),
    status: source.status,
    tool_calls: source.tool_calls,
    invalid_tool_calls: source.invalid_tool_calls,
  })
}

export function baseMessageIdentity(message: BaseMessage): string | null {
  const id = typeof message.id === 'string' && message.id.length > 0 ? message.id : null
  if (!id) return null
  if (isHumanMessage(message)) return `human:${id}`
  if (isAssistantMessage(message)) return `ai:${id}`
  return null
}

export function lastAssistantMessageIndex(messages: readonly BaseMessage[]): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (isAssistantMessage(messages[index])) return index
  }
  return -1
}

export function lastHumanMessageIndex(messages: readonly BaseMessage[]): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (isHumanMessage(messages[index])) return index
  }
  return -1
}

export function reloadPromptMessageKey(
  messages: readonly BaseMessage[],
  targetIndex: number,
): string | null {
  for (let index = targetIndex - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (isHumanMessage(message)) return reloadMessageKey(message)
  }
  return null
}

export function reloadMessageKey(message: BaseMessage): string {
  return stableString({
    id: message.id ?? null,
    fingerprint: messageContentFingerprint(message),
  })
}
