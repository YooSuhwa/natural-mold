import { describe, expect, it } from 'vitest'
import type { BaseMessage } from '@langchain/core/messages'
import { usageFromMessage } from '../usage-normalization'

// v3 메시지에 usage 소스가 둘(native usage_metadata=토큰만, enriched
// additional_kwargs.metadata.usage=토큰+cost+timing)일 때, 토큰은 native 기준으로
// 두되 cost/timing 은 enriched 에서 보강해야 한다. usageFromMessage 가 native 만
// 보고 일찍 반환하면 스트리밍 timing 이 유실되던 회귀를 가드.
function asMessage(value: Record<string, unknown>): BaseMessage {
  return value as unknown as BaseMessage
}

describe('usageFromMessage — 스트리밍 timing 병합', () => {
  it('native 토큰 + enriched(additional_kwargs)의 timing/cost 를 병합한다', () => {
    const message = asMessage({
      usage_metadata: { input_tokens: 100, output_tokens: 20 },
      additional_kwargs: {
        metadata: {
          usage: {
            prompt_tokens: 100,
            completion_tokens: 20,
            estimated_cost: 0.5,
            ttft_ms: 300,
            generation_ms: 1200,
            tokens_per_second: 25,
          },
        },
      },
    })

    const usage = usageFromMessage(message)
    expect(usage).not.toBeNull()
    expect(usage?.prompt_tokens).toBe(100)
    expect(usage?.completion_tokens).toBe(20)
    expect(usage?.ttft_ms).toBe(300)
    expect(usage?.generation_ms).toBe(1200)
    expect(usage?.tokens_per_second).toBe(25)
    expect(usage?.estimated_cost).toBe(0.5)
  })

  it('enriched 가 없으면 native usage_metadata 만 반환(timing 없음)', () => {
    const usage = usageFromMessage(asMessage({ usage_metadata: { input_tokens: 5, output_tokens: 1 } }))
    expect(usage?.prompt_tokens).toBe(5)
    expect(usage?.ttft_ms).toBeUndefined()
    expect(usage?.tokens_per_second).toBeUndefined()
  })

  it('native cost 가 있으면 유지하고, 없을 때만 enriched cost 로 보강', () => {
    const usage = usageFromMessage(
      asMessage({
        usage_metadata: { input_tokens: 10, output_tokens: 2, estimated_cost: 0.9 },
        additional_kwargs: { metadata: { usage: { prompt_tokens: 10, completion_tokens: 2, estimated_cost: 0.1 } } },
      }),
    )
    expect(usage?.estimated_cost).toBe(0.9)
  })
})
