import type { RuntimePolicyV1 } from '@/lib/types/runtime-policy'

export function hasPositiveContextWindow(
  contextWindow: number | null | undefined,
): contextWindow is number {
  return typeof contextWindow === 'number' && Number.isInteger(contextWindow) && contextWindow > 0
}

export function isRuntimePolicyCompatibleWithContextWindow(
  policy: RuntimePolicyV1 | null,
  contextWindow: number | null | undefined,
): boolean {
  return policy?.summarization.mode !== 'preset' || hasPositiveContextWindow(contextWindow)
}
