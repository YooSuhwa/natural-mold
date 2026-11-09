/**
 * Top-to-bottom layout for a workflow (workflow-plan 3.7, Q7: positions are never stored).
 *
 * 1차 graphs are small DAGs (≤ 50 nodes, no loops), so a layered layout is enough and needs
 * no layout library: each node's row is its longest distance from a root, and nodes in a
 * row are ordered by the average column of their parents (branches stay under the node
 * they came from), then spread around the centre.
 */
import type { EdgeWhen, WorkflowDefinition } from '@/lib/types/workflow'

import { forwardOf } from './loops'

export interface Placed {
  id: string
  x: number
  y: number
  row: number
}

export const NODE_WIDTH = 240
export const NODE_HEIGHT = 84
const COLUMN_GAP = 56
export const ROW_GAP = 72

/**
 * Where an exit leaves its card: a two-way node has its "yes/approved" handle on the left
 * and "no/rejected" on the right, so its children are anchored half a column to that side.
 * Without it both children share the parent's column and keep definition order (a "no"
 * child listed first lands on the left, its line crossing the "yes" one), and a lone "yes"
 * child sits right under the node, so a "no" line that skips down to a later row runs
 * behind that child's card.
 */
const EXIT_OFFSET: Partial<Record<EdgeWhen | 'next', number>> = {
  next: 0,
  true: -0.5,
  approved: -0.5,
  answered: -0.5,
  false: 0.5,
  rejected: 0.5,
  timeout: 0.5,
  // A loop's "limit" exit (workflow-loop-plan): further right, past the "no" child.
  limit: 1,
}

/** A switch's exits (its cases, then ``default``) spread under it like its handles. */
function exitOffset(definition: WorkflowDefinition, from: string, when: EdgeWhen | null): number {
  const fixed = EXIT_OFFSET[when ?? 'next']
  if (fixed !== undefined) return fixed
  const node = definition.nodes.find((item) => item.id === from)
  if (node?.type !== 'switch') return 0
  const exits = [...node.cases.map((item) => item.name), 'default']
  const index = Math.max(0, exits.indexOf(when ?? 'default'))
  return index - (exits.length - 1) / 2
}

export function layout(full: WorkflowDefinition): Map<string, Placed> {
  // A loop's back-edge does not shape the layout: it is drawn beside the loop instead.
  const definition = forwardOf(full)
  const ids = definition.nodes.map((node) => node.id)
  const known = new Set(ids)
  const edges = definition.edges.filter((edge) => known.has(edge.from) && known.has(edge.to))
  const parents = new Map<string, string[]>(ids.map((id) => [id, []]))
  const exits = new Map<string, number[]>(ids.map((id) => [id, []]))
  const children = new Map<string, string[]>(ids.map((id) => [id, []]))
  for (const edge of edges) {
    parents.get(edge.to)?.push(edge.from)
    exits.get(edge.to)?.push(exitOffset(definition, edge.from, edge.when ?? null))
    children.get(edge.from)?.push(edge.to)
  }

  // Longest-path rows in topological order; nodes on a cycle (a draft may have one) keep
  // the row they had when the cycle was reached.
  const row = new Map<string, number>()
  const indegree = new Map(ids.map((id) => [id, parents.get(id)?.length ?? 0]))
  const queue = ids.filter((id) => indegree.get(id) === 0)
  for (const id of queue) row.set(id, 0)
  while (queue.length > 0) {
    const current = queue.shift() as string
    for (const child of children.get(current) ?? []) {
      row.set(child, Math.max(row.get(child) ?? 0, (row.get(current) ?? 0) + 1))
      indegree.set(child, (indegree.get(child) ?? 0) - 1)
      if (indegree.get(child) === 0) queue.push(child)
    }
  }
  for (const id of ids) if (!row.has(id)) row.set(id, 0)

  const rows = new Map<number, string[]>()
  for (const id of ids) {
    const r = row.get(id) ?? 0
    rows.set(r, [...(rows.get(r) ?? []), id])
  }
  const column = new Map<string, number>()
  const placed = new Map<string, Placed>()
  for (const r of [...rows.keys()].sort((a, b) => a - b)) {
    const members = rows.get(r) ?? []
    const keyed = members.map((id, index) => {
      const offsets = exits.get(id) ?? []
      const cols = (parents.get(id) ?? [])
        .map((parent, at) => {
          const col = column.get(parent)
          return col === undefined ? undefined : col + (offsets[at] ?? 0)
        })
        .filter((value): value is number => value !== undefined)
      const anchor = cols.length ? cols.reduce((a, b) => a + b, 0) / cols.length : index
      return { id, anchor, index }
    })
    keyed.sort((a, b) => a.anchor - b.anchor || a.index - b.index)
    // Spread the row around its parents' centre, one column apart.
    const centre = keyed.reduce((sum, item) => sum + item.anchor, 0) / keyed.length
    const offset = (keyed.length - 1) / 2
    keyed.forEach((item, position) => {
      const col = r === 0 ? position - offset : centre + position - offset
      column.set(item.id, col)
      placed.set(item.id, {
        id: item.id,
        row: r,
        x: col * (NODE_WIDTH + COLUMN_GAP),
        y: r * (NODE_HEIGHT + ROW_GAP),
      })
    })
  }
  return placed
}

/** Nodes in reading order (row, then left to right): the narrow-screen list view. */
export function readingOrder(definition: WorkflowDefinition): string[] {
  const placed = layout(definition)
  return [...placed.values()].sort((a, b) => a.row - b.row || a.x - b.x).map((item) => item.id)
}
