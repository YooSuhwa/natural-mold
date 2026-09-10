import { describe, expect, it } from 'vitest'
import { readQuoteSelection } from './quote-selection'
import {
  resourceContextReferencesSchema,
  addResourceContextReference,
} from '@/lib/chat/context/resource-context'

const id = '11111111-1111-4111-8111-111111111111'

function selectedText() {
  const root = document.createElement('div')
  const thread = document.createElement('div')
  thread.dataset.chatSelectionThread = id
  thread.dataset.chatSelectionTitle = '원문 대화'
  const message = document.createElement('div')
  message.dataset.moldyMessageId = 'message-1'
  message.dataset.moldyMessageRole = 'assistant'
  const text = document.createElement('p')
  text.dataset.chatQuoteText = ''
  text.textContent = '선택한 문장'
  const tool = document.createElement('button')
  tool.textContent = '도구'
  message.append(text, tool)
  thread.append(message)
  root.append(thread)
  document.body.replaceChildren(root)
  const paragraph = root.querySelector('p')
  if (!paragraph) throw new Error('Missing fixture paragraph')
  const range = document.createRange()
  range.selectNodeContents(paragraph)
  Object.defineProperty(range, 'getBoundingClientRect', {
    value: () => new DOMRect(20, 40, 120, 20),
  })
  const selection = window.getSelection()
  selection?.removeAllRanges()
  selection?.addRange(range)
  return { root, paragraph, selection }
}

describe('selected message provenance', () => {
  it('captures source conversation, message and excerpt without changing the message', () => {
    const { root, selection } = selectedText()
    const quote = readQuoteSelection(root, selection)
    expect(quote?.reference).toEqual({
      kind: 'conversation',
      id,
      message_id: 'message-1',
      message_role: 'assistant',
      quote: '선택한 문장',
      label: '원문 대화',
    })
    expect(root.textContent).toBe('선택한 문장도구')
  })
  it('excludes unrelated roots, in-progress text, and cross-element selections', () => {
    const { root, paragraph, selection } = selectedText()
    expect(readQuoteSelection(document.createElement('div'), selection)).toBeNull()
    paragraph.dataset.chatStreaming = 'true'
    expect(readQuoteSelection(root, selection)).toBeNull()
    delete paragraph.dataset.chatStreaming
    const range = selection?.getRangeAt(0)
    const button = root.querySelector('button')
    if (!range || !button) throw new Error('Missing fixture selection')
    range.setEndAfter(button)
    expect(readQuoteSelection(root, selection)).toBeNull()
  })
  it('validates quote contracts and keeps different excerpts from the same message', () => {
    const one = { kind: 'conversation', id, message_id: 'message-1', quote: '첫 문장' } as const
    expect(resourceContextReferencesSchema.safeParse([one]).success).toBe(true)
    expect(resourceContextReferencesSchema.safeParse([{ ...one, quote: undefined }]).success).toBe(
      false,
    )
    expect(addResourceContextReference([one], one).kind).toBe('duplicate')
    expect(addResourceContextReference([one], { ...one, quote: '다른 문장' }).kind).toBe('added')
  })
})
