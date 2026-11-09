/**
 * The values a node may use (workflow-plan 3.2-3.3): the workflow inputs and the outputs of
 * the nodes that run before it on **every** path from the start (dominators), the same rule
 * the server checks before publishing. Each value comes with its type for the picker.
 */
import type { WorkflowDefinition, WorkflowNode } from '@/lib/types/workflow'

import { dominatorsOf, forwardOf, loopContaining } from './loops'

export interface UpstreamValue {
  node: string // node id, or "input"
  path: string // JSON pointer
  type: string
  /** In a loop: the last round's value of a later step (empty in the first round). */
  previous?: boolean
}

/** Dominators on the forward graph (a loop's back-edge never makes a step "before"). */
export function dominators(definition: WorkflowDefinition): Map<string, Set<string>> {
  return dominatorsOf(forwardOf(definition))
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
  // Later steps of the same loop: their previous round, for a rewrite that needs feedback.
  const loop = loopContaining(definition, nodeId)
  if (loop) {
    for (const node of definition.nodes) {
      if (!loop.body.has(node.id) || node.id === nodeId || before.has(node.id)) continue
      for (const output of outputsOf(node)) {
        values.push({ node: node.id, ...output, previous: true })
      }
    }
  }
  return values
}
