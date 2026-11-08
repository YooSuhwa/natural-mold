/**
 * Comparing two workflow definitions (W3b-2, plan 3.7 "버전 기록 색 diff"): which nodes and
 * connections were added, removed or changed, drawn on one canvas.
 */
import type { WorkflowDefinition, WorkflowEdge } from '@/lib/types/workflow'

export type DiffStatus = 'added' | 'removed' | 'changed' | 'same'

export interface DefinitionDiff {
  /** The target plus what the base had and the target dropped: what the canvas draws. */
  merged: WorkflowDefinition
  nodes: Map<string, DiffStatus>
  edges: Map<string, DiffStatus>
  counts: Record<Exclude<DiffStatus, 'same'>, number>
  /** The run inputs changed (shown on the start node). */
  inputsChanged: boolean
}

export function edgeKey(edge: WorkflowEdge): string {
  return `${edge.from}:${edge.when ?? 'next'}:${edge.to}`
}

/** JSON with sorted keys, so key order never reads as a change. */
function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${stable(item)}`).join(',')}}`
  }
  return JSON.stringify(value ?? null)
}

export function diffDefinitions(
  base: WorkflowDefinition,
  target: WorkflowDefinition,
): DefinitionDiff {
  const before = new Map(base.nodes.map((node) => [node.id, node]))
  const after = new Map(target.nodes.map((node) => [node.id, node]))
  const inputsChanged = stable(base.inputs) !== stable(target.inputs)
  const nodes = new Map<string, DiffStatus>()
  for (const node of target.nodes) {
    const old = before.get(node.id)
    let status: DiffStatus = 'same'
    if (!old) status = 'added'
    else if (stable(old) !== stable(node) || (node.type === 'start' && inputsChanged)) {
      status = 'changed'
    }
    nodes.set(node.id, status)
  }
  const removedNodes = base.nodes.filter((node) => !after.has(node.id))
  for (const node of removedNodes) nodes.set(node.id, 'removed')

  const oldEdges = new Set(base.edges.map(edgeKey))
  const newEdges = new Set(target.edges.map(edgeKey))
  const edges = new Map<string, DiffStatus>()
  for (const edge of target.edges) {
    edges.set(edgeKey(edge), oldEdges.has(edgeKey(edge)) ? 'same' : 'added')
  }
  const removedEdges = base.edges.filter((edge) => !newEdges.has(edgeKey(edge)))
  for (const edge of removedEdges) edges.set(edgeKey(edge), 'removed')

  const counts = { added: 0, removed: 0, changed: 0 }
  for (const status of nodes.values()) if (status !== 'same') counts[status] += 1
  return {
    merged: {
      ...target,
      nodes: [...target.nodes, ...removedNodes],
      edges: [...target.edges, ...removedEdges],
    },
    nodes,
    edges,
    counts,
    inputsChanged,
  }
}
