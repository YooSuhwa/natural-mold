/**
 * The values a node may use (workflow-plan 3.2-3.3): the workflow inputs and the outputs of
 * the nodes that run before it on **every** path from the start (dominators), the same rule
 * the server checks before publishing. Each value comes with its type for the picker.
 */
import type { WorkflowDefinition, WorkflowNode } from '@/lib/types/workflow'

export interface UpstreamValue {
  node: string // node id, or "input"
  path: string // JSON pointer
  type: string
}

export function dominators(definition: WorkflowDefinition): Map<string, Set<string>> {
  const preceding = new Map<string, string[]>(definition.nodes.map((node) => [node.id, []]))
  const following = new Map<string, string[]>(definition.nodes.map((node) => [node.id, []]))
  for (const edge of definition.edges) {
    if (!preceding.has(edge.to) || !following.has(edge.from)) continue
    preceding.get(edge.to)?.push(edge.from)
    following.get(edge.from)?.push(edge.to)
  }
  const start = definition.nodes.find((node) => node.type === 'start')
  const result = new Map<string, Set<string>>()
  if (!start) return result
  const indegree = new Map(
    definition.nodes.map((node) => [node.id, preceding.get(node.id)?.length ?? 0]),
  )
  const queue = definition.nodes
    .filter((node) => indegree.get(node.id) === 0)
    .map((node) => node.id)
  while (queue.length > 0) {
    const current = queue.shift() as string
    const parents = (preceding.get(current) ?? []).filter((parent) => result.has(parent))
    if (current === start.id) result.set(current, new Set())
    else if (parents.length > 0) {
      const sets = parents.map((parent) => new Set([...(result.get(parent) ?? []), parent]))
      result.set(current, new Set([...sets[0]].filter((id) => sets.every((set) => set.has(id)))))
    }
    for (const child of following.get(current) ?? []) {
      indegree.set(child, (indegree.get(child) ?? 0) - 1)
      if (indegree.get(child) === 0) queue.push(child)
    }
  }
  return result
}

function schemaValues(schema: unknown, prefix: string, out: { path: string; type: string }[]) {
  if (!schema || typeof schema !== 'object') return
  const node = schema as { type?: string; properties?: Record<string, unknown>; items?: unknown }
  if (node.type !== 'object' || !node.properties) return
  for (const [name, child] of Object.entries(node.properties)) {
    const path = `${prefix}/${name}`
    const childType = (child as { type?: string }).type ?? 'string'
    const itemType = (child as { items?: { type?: string } }).items?.type
    out.push({ path, type: childType === 'array' ? `${itemType ?? 'string'}[]` : childType })
    schemaValues(child, path, out)
  }
}

/** What a node produces, as (path, type) pairs a reference may point at. */
export function outputsOf(node: WorkflowNode): { path: string; type: string }[] {
  switch (node.type) {
    case 'agent': {
      const out: { path: string; type: string }[] = []
      schemaValues(node.output_schema, '', out)
      return out
    }
    case 'decision_bool':
      return [
        { path: '/value', type: 'boolean' },
        { path: '/confidence', type: 'number' },
      ]
    case 'approval':
      return [
        { path: '/decision', type: 'string' },
        { path: '/comment', type: 'string' },
      ]
    case 'transform':
      return [
        { path: '/text', type: 'string' },
        ...Object.keys(node.fields ?? {}).map((name) => ({ path: `/fields/${name}`, type: 'any' })),
      ]
    default:
      return []
  }
}

export function upstreamValues(definition: WorkflowDefinition, nodeId: string): UpstreamValue[] {
  const values: UpstreamValue[] = definition.inputs.map((item) => ({
    node: 'input',
    path: `/${item.name}`,
    type: item.type,
  }))
  const before = dominators(definition).get(nodeId)
  if (!before) return values
  for (const node of definition.nodes) {
    if (!before.has(node.id)) continue
    for (const output of outputsOf(node)) values.push({ node: node.id, ...output })
  }
  return values
}
