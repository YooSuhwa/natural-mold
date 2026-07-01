import { AIMessage } from '@langchain/core/messages'
import { describe, expect, it } from 'vitest'
import { convertMoldyLangChainMessage } from '../langchain-message-conversion'
import { TERMINAL_NOTICE_METADATA_KEY } from '../terminal-notice'

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

  it('preserves branch picker metadata from LangGraph state messages', () => {
    const message = new AIMessage({
      id: 'assistant-branch-1',
      content: 'new answer',
      additional_kwargs: {
        metadata: {
          branches: ['assistant-old', 'assistant-branch-1'],
          siblingCheckpointIds: ['ck-old', 'ck-new'],
          activeBranchId: 'assistant-branch-1',
          branchCheckpointId: 'ck-new',
          branchIndex: 1,
          branchTotal: 2,
        },
      },
    })

    const converted = convertMoldyLangChainMessage(message, converterMetadata())

    expect(customMetadata(converted)).toMatchObject({
      branches: ['assistant-old', 'assistant-branch-1'],
      siblingCheckpointIds: ['ck-old', 'ck-new'],
      activeBranchId: 'assistant-branch-1',
      branchCheckpointId: 'ck-new',
      branchIndex: 1,
      branchTotal: 2,
    })
  })

  it('injects a moldy_ui data part for each attached uiData item (path A producer)', () => {
    const message = Object.assign(new AIMessage({ id: 'assistant-ui-data-1', content: 'here' }), {
      uiData: [{ type: 'demo_note', props: { text: 'hello' }, tool_call_id: 'call-1' }],
    })

    const converted = convertMoldyLangChainMessage(message, converterMetadata())
    const content = (converted as { content?: unknown[] }).content ?? []

    expect(content).toContainEqual({
      type: 'data',
      name: 'moldy_ui',
      data: { type: 'demo_note', props: { text: 'hello' } },
    })
    // The original text part is preserved alongside the injected data part.
    expect(content.some((part) => (part as { type?: string }).type === 'text')).toBe(true)
  })

  it('does not change content when no uiData is attached (regression-zero)', () => {
    const message = new AIMessage({ id: 'assistant-no-ui-data', content: 'plain answer' })

    const converted = convertMoldyLangChainMessage(message, converterMetadata())
    const content = (converted as { content?: unknown[] }).content ?? []

    expect(content.some((part) => (part as { type?: string }).type === 'data')).toBe(false)
  })

  it('preserves display-only branch picker metadata from pending reload messages', () => {
    const message = new AIMessage({
      id: 'assistant-branch-display-only',
      content: 'partial answer',
      additional_kwargs: {
        metadata: {
          branches: ['pending-reload-0', 'pending-reload-1'],
          siblingCheckpointIds: ['pending-reload-0', 'pending-reload-1'],
          activeBranchId: 'pending-reload-1',
          branchCheckpointId: 'pending-reload-1',
          branchIndex: 1,
          branchTotal: 2,
          checkpoint_id: 'pending-reload-1',
          moldyBranchPickerDisplayOnly: true,
        },
      },
    })

    const converted = convertMoldyLangChainMessage(message, converterMetadata())

    expect(customMetadata(converted)).toMatchObject({
      branchIndex: 1,
      branchTotal: 2,
      moldyBranchPickerDisplayOnly: true,
      siblingCheckpointIds: ['pending-reload-0', 'pending-reload-1'],
    })
  })

  it('실패 terminal-notice 버블을 custom.terminalNotice로 승격한다 (G2)', () => {
    const message = new AIMessage({
      id: 'moldy-failed-run1',
      content: 'model provider request failed',
      additional_kwargs: {
        metadata: { [TERMINAL_NOTICE_METADATA_KEY]: 'failed' },
      },
    })

    const converted = convertMoldyLangChainMessage(message, converterMetadata())

    expect(customMetadata(converted).terminalNotice).toBe('failed')
  })

  it('일반 어시스턴트 메시지에는 terminalNotice 플래그가 없다', () => {
    const converted = convertMoldyLangChainMessage(
      new AIMessage({ id: 'assistant-plain', content: 'hello' }),
      converterMetadata(),
    )

    expect(customMetadata(converted).terminalNotice).toBeUndefined()
  })
})
