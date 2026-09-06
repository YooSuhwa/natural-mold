import { AIMessage, HumanMessage } from '@langchain/core/messages'
import { describe, expect, it } from 'vitest'
import {
  assistantMessageHasToolCalls,
  canReuseCachedReadyAssistantMessage,
  hasReadyAssistantAfterLastHumanMessage,
  hasReadyAssistantMessage,
  humanMessageCount,
  isEmptyAssistantMessage,
  isEmptyMessageContent,
  isEmptyTextContentPart,
  lastAssistantMessage,
  mergeCachedReadyAssistantMessages,
  messageListIsDegraded,
  messageListIsOrderedSubsequence,
  messageListsSharedPrefixLength,
  messageListsSharePrefix,
  messagesShareStableIdentity,
  postRunHydrationIsReady,
  suppressRunningEmptyAssistantPlaceholder,
} from '../stream-message-comparison'

describe('stream message comparison', () => {
  it('finds the last ready assistant response after the latest human message', () => {
    const firstHuman = new HumanMessage({ id: 'human-1', content: 'first' })
    const firstAssistant = new AIMessage({ id: 'assistant-1', content: 'first reply' })
    const lastHuman = new HumanMessage({ id: 'human-2', content: 'second' })
    const emptyAssistant = new AIMessage({ id: 'assistant-2', content: '' })
    const readyAssistant = new AIMessage({ id: 'assistant-3', content: 'second reply' })

    const messages = [firstHuman, firstAssistant, lastHuman, emptyAssistant, readyAssistant]

    expect(lastAssistantMessage(messages)).toBe(readyAssistant)
    expect(hasReadyAssistantMessage(messages)).toBe(true)
    expect(hasReadyAssistantAfterLastHumanMessage(messages)).toBe(true)
    expect(humanMessageCount(messages)).toBe(2)
  })

  it('does not treat an earlier assistant reply as ready after a later human message', () => {
    const messages = [
      new HumanMessage({ id: 'human-1', content: 'first' }),
      new AIMessage({ id: 'assistant-1', content: 'first reply' }),
      new HumanMessage({ id: 'human-2', content: 'second' }),
      new AIMessage({ id: 'assistant-2', content: '' }),
    ]

    expect(hasReadyAssistantAfterLastHumanMessage(messages)).toBe(false)
    expect(hasReadyAssistantMessage([new HumanMessage({ content: 'only human' })])).toBe(false)
    expect(lastAssistantMessage([new HumanMessage({ content: 'only human' })])).toBeNull()
  })

  it('measures common prefixes by message content', () => {
    const sharedHuman = new HumanMessage({ id: 'human-1', content: 'question' })
    const sharedAssistant = new AIMessage({ id: 'assistant-1', content: 'answer' })
    const left = [sharedHuman, sharedAssistant, new AIMessage({ content: 'left tail' })]
    const right = [sharedHuman, sharedAssistant, new AIMessage({ content: 'right tail' })]

    expect(messageListsSharedPrefixLength(left, right)).toBe(2)
    expect(messageListsSharePrefix(left, right, 2)).toBe(true)
    expect(messageListsSharePrefix(left, right, 3)).toBe(false)
  })

  it('matches ordered subsequences by stable identity before falling back to content', () => {
    const human = new HumanMessage({ id: 'human-1', content: 'question' })
    const changedAssistant = new AIMessage({ id: 'assistant-1', content: 'new answer' })
    const cached = [
      new HumanMessage({ id: 'human-1', content: 'question' }),
      new AIMessage({ id: 'assistant-1', content: 'old answer' }),
      new AIMessage({ id: 'assistant-2', content: 'another answer' }),
    ]

    expect(messagesShareStableIdentity(changedAssistant, cached[1])).toBe(true)
    expect(messagesShareStableIdentity(human, undefined)).toBe(false)
    expect(
      messagesShareStableIdentity(
        new AIMessage({ content: 'same without id' }),
        new AIMessage({ content: 'same without id' }),
      ),
    ).toBe(true)
    expect(messageListIsOrderedSubsequence([human, changedAssistant], cached)).toBe(true)
    expect(
      messageListIsOrderedSubsequence(
        [new AIMessage({ id: 'missing', content: 'absent' })],
        cached,
      ),
    ).toBe(false)
  })

  it('detects shorter or blank-terminal degraded message lists', () => {
    const human = new HumanMessage({ id: 'human-1', content: 'question' })
    const readyAssistant = new AIMessage({ id: 'assistant-1', content: 'answer' })
    const cached = [human, readyAssistant, new AIMessage({ id: 'assistant-2', content: 'later' })]

    expect(messageListIsDegraded([human, readyAssistant], cached)).toBe(true)
    expect(
      messageListIsDegraded([new AIMessage({ id: 'assistant-2', content: 'later' })], cached),
    ).toBe(true)
    expect(
      messageListIsDegraded(
        [human, new AIMessage({ id: 'assistant-1', content: '' })],
        [human, readyAssistant],
      ),
    ).toBe(true)
    expect(messageListIsDegraded([human], [human])).toBe(false)
  })

  it('accepts hydrated state only when it contains a non-degraded ready response', () => {
    const human = new HumanMessage({ id: 'human-1', content: 'question' })
    const readyAssistant = new AIMessage({ id: 'assistant-1', content: 'answer' })

    expect(postRunHydrationIsReady([human, readyAssistant], [])).toBe(true)
    expect(postRunHydrationIsReady([human], [human, readyAssistant])).toBe(false)
    expect(
      postRunHydrationIsReady(
        [human, new AIMessage({ id: 'assistant-1', content: '' })],
        [human, readyAssistant],
      ),
    ).toBe(false)
    expect(
      postRunHydrationIsReady(
        [new AIMessage({ id: 'assistant-1', content: 'answer' }), readyAssistant],
        [human, readyAssistant],
      ),
    ).toBe(false)
  })

  it('reuses only cached non-empty assistant responses for empty candidate placeholders', () => {
    const human = new HumanMessage({ id: 'human-1', content: 'question' })
    const placeholder = new AIMessage({ id: 'assistant-1', content: '' })
    const cachedAssistant = new AIMessage({ id: 'assistant-1', content: 'answer' })
    const candidate = [human, placeholder]

    const merged = mergeCachedReadyAssistantMessages(candidate, [human, cachedAssistant])

    expect(canReuseCachedReadyAssistantMessage(placeholder, cachedAssistant)).toBe(true)
    expect(canReuseCachedReadyAssistantMessage(human, cachedAssistant)).toBe(false)
    expect(merged).toEqual([human, cachedAssistant])
    expect(merged[1]).toBe(cachedAssistant)
    expect(mergeCachedReadyAssistantMessages([], [cachedAssistant])).toEqual([])
  })

  it('keeps assistant messages with valid tool calls out of the empty placeholder state', () => {
    const directToolCall = new AIMessage({
      content: '',
      tool_calls: [{ id: 'call-1', name: 'search', args: { query: 'moldy' } }],
    })
    const additionalToolCall = new AIMessage({
      content: '',
      additional_kwargs: {
        tool_calls: [
          {
            id: 'call-2',
            type: 'function',
            function: { name: 'search', arguments: '{"query":"moldy"}' },
          },
        ],
      },
    })

    expect(assistantMessageHasToolCalls(directToolCall)).toBe(true)
    expect(assistantMessageHasToolCalls(additionalToolCall)).toBe(true)
    expect(isEmptyAssistantMessage(directToolCall)).toBe(false)
    expect(isEmptyAssistantMessage(new AIMessage({ content: '' }))).toBe(true)
    expect(isEmptyAssistantMessage(new HumanMessage({ content: '' }))).toBe(false)
  })

  it('suppresses only a running empty assistant placeholder', () => {
    const human = new HumanMessage({ id: 'human-1', content: 'question' })
    const placeholder = new AIMessage({ id: 'assistant-1', content: '' })
    const messages = [human, placeholder]

    expect(suppressRunningEmptyAssistantPlaceholder(messages, true)).toEqual([human])
    expect(suppressRunningEmptyAssistantPlaceholder(messages, false)).toBe(messages)
    expect(
      suppressRunningEmptyAssistantPlaceholder([human, new AIMessage({ content: 'answer' })], true),
    ).toHaveLength(2)
  })

  it('recognizes empty string and text-part content without accepting non-text parts', () => {
    expect(isEmptyMessageContent('')).toBe(true)
    expect(isEmptyMessageContent([])).toBe(true)
    expect(isEmptyMessageContent([{ type: 'text', text: '' }])).toBe(true)
    expect(isEmptyMessageContent([{ type: 'image', url: 'https://example.test/image.png' }])).toBe(
      false,
    )
    expect(isEmptyTextContentPart({ type: 'text' })).toBe(true)
    expect(isEmptyTextContentPart({ type: 'text', text: 'answer' })).toBe(false)
    expect(isEmptyTextContentPart({ type: 'image', text: '' })).toBe(false)
    expect(isEmptyTextContentPart('')).toBe(false)
  })
})
