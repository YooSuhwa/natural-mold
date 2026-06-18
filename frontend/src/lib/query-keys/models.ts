export const modelQueryKeys = {
  all: ['models'] as const,
  list: (includeHidden = false) =>
    includeHidden ? (['models', 'all'] as const) : (['models'] as const),
  detail: (modelId: string | null | undefined) => ['models', modelId] as const,
}
