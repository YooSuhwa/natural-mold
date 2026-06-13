import type { ThreadMessageLike, useExternalMessageConverter } from '@assistant-ui/react'
import type { Message } from '@/lib/types'
import { parseTimestamp } from '@/lib/utils/format-relative-time'

type ConvertedMessage = useExternalMessageConverter.Message

/** P1-A — branch metadata is identical for user/assistant. Returns ``null``
 * when the message has no branching info so callers can skip the metadata
 * write entirely. */
function buildBranchMeta(message: Message): Record<string, unknown> | null {
  if (!message.siblings || message.siblings.length <= 1) return null
  return {
    branches: message.siblings,
    siblingCheckpointIds: message.sibling_checkpoint_ids ?? [],
    activeBranchId: message.id,
    branchCheckpointId: message.branch_checkpoint_id ?? null,
    branchIndex: message.branch_index ?? null,
    branchTotal: message.branch_total ?? null,
  }
}

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

  // user 메시지 → 텍스트 (+ 첨부)
  if (message.role === 'user') {
    const userMsg: ConvertedMessage = {
      role: 'user' as const,
      id: message.id,
      content: message.content,
      createdAt: parseTimestamp(message.created_at),
    }
    if (message.attachments && message.attachments.length > 0) {
      // assistant-ui expects CompleteAttachment shape; we tag content with the
      // upload URL so any UI in the message body can render previews.
      ;(userMsg as unknown as { attachments: unknown }).attachments = message.attachments.map(
        (att) => ({
          id: att.id,
          type: att.mime_type.startsWith('image/') ? 'image' : 'file',
          name: att.filename,
          contentType: att.mime_type,
          status: { type: 'complete' },
          content: [{ type: 'text', text: `[attachment: ${att.filename}](${att.url})` }],
        }),
      )
    }
    // M-CHAT1b — surface branch info via metadata.custom so the inline
    // BranchPicker UI can read it from useAuiState.
    const userBranchMeta = buildBranchMeta(message)
    if (userBranchMeta) {
      ;(userMsg as unknown as { metadata: unknown }).metadata = {
        custom: userBranchMeta,
      }
    }
    return userMsg
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

  const result: ConvertedMessage = {
    role: 'assistant' as const,
    id: message.id,
    content: parts.length > 0 ? (parts as ThreadMessageLike['content']) : '',
    createdAt: new Date(message.created_at),
  }
  // M-CHAT1b — branch info on assistant messages (sibling regenerations).
  // ``message.usage`` (W7)도 같은 metadata.custom 슬롯에 함께 hoist하여 ActionBar
  // 옆 TokenUsagePopover가 ``useAuiState``로 직접 읽는다.
  const assistantBranchMeta = buildBranchMeta(message)

  const isStreamingMessage = message.id.startsWith('stream-')

  const artifacts = message.artifacts?.length ? message.artifacts : null

  if (message.feedback || assistantBranchMeta || message.usage || isStreamingMessage || artifacts) {
    // assistant-ui treats `metadata.submittedFeedback.type` as the active
    // rating — the FeedbackPositive/Negative buttons render highlighted when
    // it matches their type. We co-locate branch info + usage in `metadata.custom`.
    const customMeta: Record<string, unknown> = { ...(assistantBranchMeta ?? {}) }
    if (isStreamingMessage) {
      customMeta.isStreamingMessage = true
    }
    if (message.usage) {
      customMeta.usage = message.usage
    }
    if (artifacts) {
      customMeta.artifacts = artifacts
    }
    const meta: Record<string, unknown> = { custom: customMeta }
    if (message.feedback) {
      meta.submittedFeedback = {
        type: message.feedback.rating === 'up' ? 'positive' : 'negative',
      }
    }
    ;(result as unknown as { metadata: unknown }).metadata = meta
  }
  return result
}
