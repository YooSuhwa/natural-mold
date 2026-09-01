import { HumanMessage, type BaseMessage } from '@langchain/core/messages'
import { isRecord } from './stream-message-utilities'

export function isHumanMessage(message: BaseMessage): boolean {
  if (HumanMessage.isInstance(message)) return true
  const getType = (message as { _getType?: () => string })._getType
  return typeof getType === 'function' && getType.call(message) === 'human'
}

export function isAssistantMessage(message: BaseMessage | undefined): boolean {
  if (!message) return false
  const getType = (message as { _getType?: () => string })._getType
  return typeof getType === 'function' && getType.call(message) === 'ai'
}

export function messageContentEqualsText(message: BaseMessage | undefined, text: string): boolean {
  return message ? textContentFromMessageContent(message.content) === text : false
}

export function textContentFromMessageContent(content: BaseMessage['content']): string | null {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return null
  const text = content
    .map((part) => {
      if (typeof part === 'string') return part
      if (!isRecord(part)) return ''
      return part.type === 'text' && typeof part.text === 'string' ? part.text : ''
    })
    .join('')
  return text
}

export function branchMetadataFromMessage(
  message: BaseMessage | undefined,
): Record<string, unknown> {
  const metadata = message?.additional_kwargs?.metadata
  return isRecord(metadata) ? metadata : {}
}
