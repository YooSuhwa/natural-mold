import { AIMessage } from '@langchain/core/messages'
import { describe, expect, it } from 'vitest'
import { convertMoldyLangChainMessage } from '../langchain-message-conversion'

type ConvertedWithCustom = {
  readonly metadata?: {
    readonly custom?: Record<string, unknown>
  }
}

function converterMetadata(): Parameters<typeof convertMoldyLangChainMessage>[1] {
  return undefined as unknown as Parameters<typeof convertMoldyLangChainMessage>[1]
}

function customMetadata(value: unknown): Record<string, unknown> {
  return ((value as ConvertedWithCustom).metadata?.custom ?? {}) as Record<string, unknown>
}

describe('convertMoldyLangChainMessage', () => {
  it('preserves LangGraph usage metadata in assistant-ui custom metadata', () => {
    const message = new AIMessage({
      id: 'assistant-usage-1',
      content: 'done',
      usage_metadata: {
        input_tokens: 120,
        output_tokens: 45,
        total_tokens: 165,
      },
    })

    const converted = convertMoldyLangChainMessage(message, converterMetadata())

    expect(customMetadata(converted).usage).toEqual({
      prompt_tokens: 120,
      completion_tokens: 45,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
    })
  })

  it('keeps existing custom metadata when adding streamed usage', () => {
    const message = new AIMessage({
      id: 'assistant-usage-2',
      content: 'done',
      additional_kwargs: {
        metadata: {
          existing: true,
          usage: {
            prompt_tokens: 12,
            completion_tokens: 5,
            cache_creation_tokens: 2,
            cache_read_tokens: 3,
          },
        },
      },
    })

    const converted = convertMoldyLangChainMessage(message, converterMetadata())

    expect(customMetadata(converted)).toMatchObject({
      existing: true,
      usage: {
        prompt_tokens: 12,
        completion_tokens: 5,
        cache_creation_tokens: 2,
        cache_read_tokens: 3,
      },
    })
  })
})
