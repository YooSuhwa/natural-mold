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
    case 'llm': {
      const out: { path: string; type: string }[] = []
      schemaValues(node.output_schema, '', out)
      return out
    }
    case 'for_each': {
      const out: { path: string; type: string }[] = [
        { path: '/items', type: 'object[]' },
        { path: '/count', type: 'integer' },
      ]
      return out
    }
    case 'parallel': {
      const out: { path: string; type: string }[] = []
      for (const branch of node.branches) schemaValues(branch.output_schema, `/${branch.name}`, out)
      return out
    }
    case 'decision_bool':
      return [
        { path: '/value', type: 'boolean' },
        { path: '/confidence', type: 'number' },
        ...(node.evaluator === 'llm' ? [{ path: '/reason', type: 'string' }] : []),
      ]
    case 'switch':
      return [
        { path: '/value', type: 'any' },
        { path: '/case', type: 'string' },
      ]
    case 'ask':
      return [{ path: '/answer', type: 'string' }]
    case 'notify':
      return [{ path: '/sent', type: 'boolean' }]
    case 'tool':
      return [{ path: '/result', type: 'string' }]
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

/** The item a for_each node's prompt reads: the whole item, or its fields for objects. */
function itemValues(definition: WorkflowDefinition, nodeId: string): UpstreamValue[] {
  const node = definition.nodes.find((item) => item.id === nodeId)
  if (node?.type !== 'for_each') return []
  const source = definition.nodes.find((item) => item.id === node.source)
  let schema: unknown = source && 'output_schema' in source ? source.output_schema : undefined
  for (const part of node.path.split('/').slice(1)) {
    const current = schema as { properties?: Record<string, unknown> } | undefined
    schema = current?.properties?.[part]
  }
  const items = (schema as { items?: { type?: string } } | undefined)?.items
  const values: UpstreamValue[] = [{ node: 'item', path: '', type: items?.type ?? 'any' }]
  const fields: { path: string; type: string }[] = []
  schemaValues(items, '', fields)
  return [...values, ...fields.map((field) => ({ node: 'item', ...field }))]
}

export function upstreamValues(definition: WorkflowDefinition, nodeId: string): UpstreamValue[] {
  const values: UpstreamValue[] = [
    ...definition.inputs.map((item) => ({
      node: 'input',
      path: `/${item.name}`,
      type: item.type,
    })),
    ...itemValues(definition, nodeId),
  ]
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
