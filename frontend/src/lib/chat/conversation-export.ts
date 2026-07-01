import type { Message, MessagesEnvelope, ToolCallInfo } from '@/lib/types'

/**
 * 대화 export (G5). 프론트가 이미 로드한 ``envelope.messages``를 markdown/json으로
 * 변환한다(백엔드 export API 불필요). 순수 함수 — 사용자에게 보이는 라벨은 i18n으로
 * 호출부(ExportDialog)에서 주입한다. 날짜는 기계 파싱을 위해 ISO 원본을 유지한다.
 */

export interface ExportLabels {
  readonly roleUser: string
  readonly roleAssistant: string
  readonly roleTool: string
  readonly toolCalls: string
  readonly attachments: string
  readonly exportedAt: string
}

function roleLabel(role: Message['role'], labels: ExportLabels): string {
  if (role === 'user') return labels.roleUser
  if (role === 'assistant') return labels.roleAssistant
  return labels.roleTool
}

function toolCallsToMarkdown(toolCalls: readonly ToolCallInfo[], label: string): string {
  const lines = toolCalls.map((call) => `- \`${call.name}\` ${JSON.stringify(call.args)}`)
  return `**${label}:**\n${lines.join('\n')}`
}

function attachmentsToMarkdown(
  attachments: readonly { readonly filename: string; readonly url: string }[],
  label: string,
): string {
  const lines = attachments.map((attachment) => `- [${attachment.filename}](${attachment.url})`)
  return `**${label}:**\n${lines.join('\n')}`
}

function messageToMarkdown(message: Message, labels: ExportLabels): string {
  const parts: string[] = [`## ${roleLabel(message.role, labels)} · ${message.created_at}`]
  const content = message.content.trim()
  if (content) parts.push(content)
  if (message.tool_calls && message.tool_calls.length > 0) {
    parts.push(toolCallsToMarkdown(message.tool_calls, labels.toolCalls))
  }
  if (message.attachments && message.attachments.length > 0) {
    parts.push(attachmentsToMarkdown(message.attachments, labels.attachments))
  }
  return parts.join('\n\n')
}

export function conversationToMarkdown(
  messages: readonly Message[],
  opts: { readonly title: string; readonly exportedAt: string; readonly labels: ExportLabels },
): string {
  const header = `# ${opts.title}\n\n_${opts.labels.exportedAt}: ${opts.exportedAt}_`
  const blocks = messages.map((message) => messageToMarkdown(message, opts.labels))
  return `${[header, ...blocks].join('\n\n---\n\n')}\n`
}

export function conversationToJson(envelope: MessagesEnvelope): string {
  return `${JSON.stringify(envelope, null, 2)}\n`
}

export function exportFilename(
  conversationId: string,
  ext: 'md' | 'json',
  timestamp: string,
): string {
  return `conversation-${conversationId}-${timestamp}.${ext}`
}

/** 클라이언트 Blob 다운로드 (mcp-servers export 패턴). DOM 부수효과라 유틸 테스트에서 제외. */
export function downloadTextFile(content: string, filename: string, mime: string): void {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
