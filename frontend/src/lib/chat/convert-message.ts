import type { ThreadMessageLike, useExternalMessageConverter } from '@assistant-ui/react'
import type { Message } from '@/lib/types'

type ConvertedMessage = useExternalMessageConverter.Message

/**
 * Message (backend) → ThreadMessageLike (assistant-ui) 변환 콜백.
 * useExternalMessageConverter에 전달하여 사용한다.
 *
 * - user/assistant → ThreadMessageLike (텍스트 + 도구 호출)
 * - tool → { role: 'tool', toolCallId, result } (자동으로 tool-call에 병합됨)
 */
export const convertMessage: useExternalMessageConverter.Callback<Message> = (
  message,
): ConvertedMessage => {
  // tool 메시지 → 도구 결과 (assistant-ui가 tool-call에 자동 병합)
  if (message.role === 'tool') {
    return {
      role: 'tool' as const,
      toolCallId: message.tool_call_id ?? '',
      result: message.content,
    }
  }

  // user 메시지 → 텍스트만
  if (message.role === 'user') {
    return {
      role: 'user' as const,
      id: message.id,
      content: message.content,
      createdAt: new Date(message.created_at),
    }
  }

  // assistant 메시지 → 텍스트 + tool-call 파트 배열
  type ContentPart =
    | { type: 'text'; text: string }
    | { type: 'tool-call'; toolCallId: string; toolName: string; args: Record<string, unknown> }
  const parts: ContentPart[] = []

  if (message.content) {
    parts.push({ type: 'text', text: message.content })
  }

  if (message.tool_calls) {
    for (const tc of message.tool_calls) {
      parts.push({
        type: 'tool-call',
        toolCallId: tc.id ?? `tc-${tc.name}`,
        toolName: tc.name,
        args: tc.args as Record<string, unknown>,
      })
    }
  }

  return {
    role: 'assistant' as const,
    id: message.id,
    content: parts.length > 0 ? (parts as ThreadMessageLike['content']) : '',
    createdAt: new Date(message.created_at),
  }
}
