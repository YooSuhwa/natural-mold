import type { ModelListKind } from '@/lib/types/model'

export const modelQueryKeys = {
  all: ['models'] as const,
  // The default picker list (visible chat models) keeps its historic key.
  list: (includeHidden = false, kind: ModelListKind = 'chat') =>
    kind === 'chat'
      ? includeHidden
        ? (['models', 'all'] as const)
        : (['models'] as const)
      : (['models', 'kind', kind, includeHidden] as const),
  detail: (modelId: string | null | undefined) => ['models', modelId] as const,
}
