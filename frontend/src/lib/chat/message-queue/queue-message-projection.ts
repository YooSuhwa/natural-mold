import type { AppendMessage, QueueItemState } from '@assistant-ui/react'
import { z } from 'zod'

import type { ConversationRunInput, JsonValue } from '@/lib/api/conversation-run-inputs'
import { withResourceContextReferences } from '@/lib/chat/context/resource-context-payload'

function jsonRecord(value: JsonValue | undefined): Readonly<Record<string, JsonValue>> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value : null
}

function latestUserInputMessage(
  input: Readonly<Record<string, JsonValue>>,
): Readonly<Record<string, JsonValue>> | null {
  const messages = input.messages
  if (!Array.isArray(messages)) return null
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const current = jsonRecord(messages[index])
    if (!current) continue
    if (current.role === 'user' || current.type === 'human') return current
  }
  return null
}

function textFromContent(content: unknown): string {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .map((part) => {
      if (typeof part === 'string') return part
      if (typeof part !== 'object' || part === null || !('text' in part)) return ''
      return typeof part.text === 'string' ? part.text : ''
    })
    .filter(Boolean)
    .join('\n')
}

export function queueInputText(input: ConversationRunInput): string {
  const messages = input.input_payload.messages
  if (!Array.isArray(messages)) return ''
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const current = messages[index]
    if (typeof current !== 'object' || current === null) continue
    const role = 'role' in current ? current.role : 'type' in current ? current.type : undefined
    if (role !== 'user' && role !== 'human') continue
    return textFromContent('content' in current ? current.content : undefined)
  }
  return ''
}

export function queueItemFromInput(input: ConversationRunInput): QueueItemState {
  const text = queueInputText(input)
  return {
    id: input.id,
    prompt: text,
    parts: text ? [{ type: 'text', text }] : [],
  }
}

export function queueItemText(item: QueueItemState): string {
  return item.parts
    .filter(
      (part): part is Extract<(typeof item.parts)[number], { type: 'text' }> =>
        part.type === 'text',
    )
    .map((part) => part.text)
    .join('\n')
}

export function editableInputFromMessage(
  message: AppendMessage,
  currentInput: Readonly<Record<string, JsonValue>>,
  resourceContext: ConversationRunInput['resource_context'] = [],
): Readonly<Record<string, JsonValue>> {
  const content: JsonValue[] = []
  for (const part of message.content) {
    if (part.type === 'text') {
      content.push({ type: 'text', text: part.text })
      continue
    }
    if (part.type === 'file') {
      content.push({
        type: 'file',
        data: part.data,
        mime_type: part.mimeType,
        ...(part.filename ? { filename: part.filename } : {}),
      })
      continue
    }
    if (part.type === 'image') {
      content.push({
        type: 'image_url',
        image_url: { url: part.image },
        ...(part.filename ? { filename: part.filename } : {}),
      })
    }
  }
  const metadata = z.json().safeParse(message.metadata.custom)
  const previousMessage = latestUserInputMessage(currentInput)
  const previousMetadata = jsonRecord(previousMessage?.metadata)
  const submittedMetadata = metadata.success ? jsonRecord(metadata.data) : null
  const editedInput = {
    ...currentInput,
    messages: [
      {
        ...previousMessage,
        role: message.role,
        content,
        metadata: {
          ...previousMetadata,
          ...submittedMetadata,
        },
      },
    ],
  }
  if (resourceContext.length === 0) return editedInput
  const contextualInput = withResourceContextReferences(editedInput, resourceContext)
  if (contextualInput.kind === 'invalid') {
    throw new Error('Authoritative resource context is malformed')
  }
  return contextualInput.input
}

export function queuedInputFromMessage(
  message: AppendMessage,
): Readonly<Record<string, JsonValue>> {
  const attachments = message.attachments ?? []
  return {
    ...editableInputFromMessage(message, {}),
    ...(attachments.length > 0
      ? {
          attachments: attachments.map((attachment) => ({ id: attachment.id })),
        }
      : {}),
  }
}
