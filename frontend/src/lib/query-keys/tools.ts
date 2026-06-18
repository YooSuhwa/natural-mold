export type ToolListQueryParams = {
  readonly definition_key?: string
  readonly enabled?: boolean
}

export const toolQueryKeys = {
  all: ['tools'] as const,
  list: (params?: ToolListQueryParams) => ['tools', params ?? {}] as const,
  detail: (toolId: string | null | undefined) => ['tools', toolId] as const,
  types: ['tool-types'] as const,
  typeDetail: (definitionKey: string | null | undefined) => ['tool-types', definitionKey] as const,
}
