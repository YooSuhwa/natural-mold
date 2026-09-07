import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { AssistantRuntimeProvider, useAuiState, useLocalRuntime } from '@assistant-ui/react'
import { describe, expect, it } from 'vitest'

import { useResourceContextComposer } from '../use-resource-context-composer'

function Wrapper({ children }: { readonly children: ReactNode }) {
  const runtime = useLocalRuntime({ async *run() {} })
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>
}

function useHarness(resetKey: number) {
  return {
    context: useResourceContextComposer(resetKey),
    configured: useAuiState((state) => state.composer.runConfig.custom?.resource_context),
  }
}

describe('useResourceContextComposer', () => {
  it('keeps strict refs in official run config, supports removal, and clears after acceptance', async () => {
    const ref = {
      kind: 'file',
      id: '11111111-1111-4111-8111-111111111111',
      label: 'brief.txt',
    } as const
    const { result, rerender } = renderHook(({ resetKey }) => useHarness(resetKey), {
      wrapper: Wrapper,
      initialProps: { resetKey: 0 },
    })

    act(() => result.current.context.add(ref))
    expect(result.current.context.references).toEqual([ref])
    await waitFor(() => expect(result.current.configured).toEqual([ref]))

    act(() => result.current.context.remove(ref))
    expect(result.current.context.references).toEqual([])
    await waitFor(() => expect(result.current.configured).toBeUndefined())

    act(() => result.current.context.add(ref))
    rerender({ resetKey: 1 })
    expect(result.current.context.references).toEqual([])
  })
})
