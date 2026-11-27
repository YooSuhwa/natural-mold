export interface PartialModelOutput {
  readonly text: string
  readonly tools: readonly {
    readonly name: string
    readonly state: 'started' | 'completed' | 'unknown'
  }[]
  readonly truncated: boolean
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Only display the read-only projection accompanying the selected failed checkpoint. */
export function parsePartialModelOutput(value: unknown): PartialModelOutput | null {
  if (!isRecord(value) || typeof value.text !== 'string' || typeof value.truncated !== 'boolean') {
    return null
  }
  if (!Array.isArray(value.tools)) return null
  const tools: PartialModelOutput['tools'][number][] = []
  for (const tool of value.tools) {
    if (!isRecord(tool) || typeof tool.name !== 'string' || !tool.name) return null
    const state = tool.state
    if (state !== 'started' && state !== 'completed' && state !== 'unknown') return null
    tools.push({ name: tool.name, state })
  }
  return { text: value.text, tools, truncated: value.truncated }
}
