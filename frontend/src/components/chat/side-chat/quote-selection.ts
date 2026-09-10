import type { MessageQuote } from './side-chat-context'

export type QuoteSelection = {
  readonly reference: MessageQuote
  readonly rect: DOMRect
  readonly source: HTMLElement
}

export function readQuoteSelection(
  root: HTMLElement,
  selection: Selection | null,
  sideId?: string | null,
): QuoteSelection | null {
  if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return null
  const parent = (node: Node | null) => (node instanceof Element ? node : node?.parentElement)
  const start = parent(selection.anchorNode)?.closest<HTMLElement>('[data-chat-quote-text]')
  const end = parent(selection.focusNode)?.closest<HTMLElement>('[data-chat-quote-text]')
  if (!start || start !== end) return null
  const thread = start.closest<HTMLElement>('[data-chat-selection-thread]')
  if (!root.contains(start) && thread?.dataset.chatSelectionThread !== sideId) return null
  if (start.closest('[data-chat-streaming="true"]')) return null
  const message = start.closest<HTMLElement>('[data-moldy-message-id]')
  const messageId = message?.dataset.moldyMessageId
  const conversationId = thread?.dataset.chatSelectionThread
  const quote = selection.toString().trim()
  if (!messageId || !conversationId || !quote || quote.length > 8000) return null
  return {
    reference: {
      kind: 'conversation',
      id: conversationId,
      message_id: messageId,
      quote,
      ...(message?.dataset.moldyMessageRole === 'user' ||
      message?.dataset.moldyMessageRole === 'assistant'
        ? { message_role: message.dataset.moldyMessageRole }
        : {}),
      label: thread.dataset.chatSelectionTitle,
    },
    rect: selection.getRangeAt(0).getBoundingClientRect(),
    source: start,
  }
}
