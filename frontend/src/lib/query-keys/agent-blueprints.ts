export const agentBlueprintQueryKeys = {
  all: ['agent-blueprints'] as const,
  detail: (blueprintId: string | null | undefined) => ['agent-blueprints', blueprintId] as const,
}
