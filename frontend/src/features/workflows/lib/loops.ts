/**
 * Loops in the editor (workflow-loop-plan 3.2), the same shape the server checks
 * (`backend/app/workflows/checks.py` `loop_shape`): a **back-edge** is an edge to a node still
 * on the depth-first path from the start; a loop is a back-edge from a decision's yes/no exit
 * to a step that runs before it on every path. Layout, the value picker and the step settings
 * work on the **forward** graph, the definition without its back-edges.
 */
import type { WorkflowDefinition, WorkflowEdge } from '@/lib/types/workflow'

export const DEFAULT_MAX_ROUNDS = 3
export const MAX_ROUNDS = 10

export interface Loop {
  decision: string
  when: 'true' | 'false'
  target: string
  /** The steps from the target to the decision (both included). */
  body: Set<string>
}

function sameEdge(a: WorkflowEdge, b: WorkflowEdge): boolean {
  return a.from === b.from && a.to === b.to && (a.when ?? null) === (b.when ?? null)
}

export function backEdges(definition: WorkflowDefinition): WorkflowEdge[] {
  const start = definition.nodes.find((node) => node.type === 'start')
  if (!start) return []
  const out = new Map<string, WorkflowEdge[]>()
  for (const edge of definition.edges) out.set(edge.from, [...(out.get(edge.from) ?? []), edge])
  const back: WorkflowEdge[] = []
  const onPath = new Set<string>()
  const done = new Set<string>()
  const visit = (node: string) => {
    onPath.add(node)
    for (const edge of out.get(node) ?? []) {
      if (onPath.has(edge.to)) back.push(edge)
      else if (!done.has(edge.to)) visit(edge.to)
    }
    onPath.delete(node)
    done.add(node)
  }
  visit(start.id)
  return back
}

export function isBackEdge(definition: WorkflowDefinition, edge: WorkflowEdge): boolean {
  return backEdges(definition).some((item) => sameEdge(item, edge))
}

/** The definition without its back-edges. */
export function forwardOf(definition: WorkflowDefinition): WorkflowDefinition {
  const back = backEdges(definition)
  if (back.length === 0) return definition
  return {
    ...definition,
    edges: definition.edges.filter((edge) => !back.some((item) => sameEdge(item, edge))),
  }
}

/** For each node reachable from start: the nodes that run before it on every path. */
export function dominatorsOf(forward: WorkflowDefinition): Map<string, Set<string>> {
  const preceding = new Map<string, string[]>(forward.nodes.map((node) => [node.id, []]))
  const following = new Map<string, string[]>(forward.nodes.map((node) => [node.id, []]))
  for (const edge of forward.edges) {
    if (!preceding.has(edge.to) || !following.has(edge.from)) continue
    preceding.get(edge.to)?.push(edge.from)
    following.get(edge.from)?.push(edge.to)
  }
  const start = forward.nodes.find((node) => node.type === 'start')
  const result = new Map<string, Set<string>>()
  if (!start) return result
  const indegree = new Map(
    forward.nodes.map((node) => [node.id, preceding.get(node.id)?.length ?? 0]),
  )
  const queue = forward.nodes.filter((node) => indegree.get(node.id) === 0).map((node) => node.id)
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

function bodyOf(forward: WorkflowDefinition, target: string, decision: string): Set<string> {
  const reaches = new Set([decision])
  const stack = [decision]
  while (stack.length > 0) {
    const current = stack.pop() as string
    for (const edge of forward.edges) {
      if (edge.to === current && !reaches.has(edge.from)) {
        reaches.add(edge.from)
        stack.push(edge.from)
      }
    }
  }
  const reached = new Set([target])
  const queue = [target]
  while (queue.length > 0) {
    const current = queue.shift() as string
    for (const edge of forward.edges) {
      if (edge.from === current && !reached.has(edge.to)) {
        reached.add(edge.to)
        queue.push(edge.to)
      }
    }
  }
  return new Set([...reaches].filter((id) => reached.has(id)))
}

/** The loops of a draft (back-edges from a decision's yes/no exit to a dominating step). */
export function loopsOf(definition: WorkflowDefinition): Loop[] {
  const forward = forwardOf(definition)
  const dominators = dominatorsOf(forward)
  const kinds = new Map(definition.nodes.map((node) => [node.id, node.type]))
  return backEdges(definition).flatMap((edge) =>
    kinds.get(edge.from) === 'decision_bool' &&
    (edge.when === 'true' || edge.when === 'false') &&
    dominators.get(edge.from)?.has(edge.to)
      ? [
          {
            decision: edge.from,
            when: edge.when as 'true' | 'false',
            target: edge.to,
            body: bodyOf(forward, edge.to, edge.from),
          },
        ]
      : [],
  )
}

/** Steps a decision's yes/no exit may lead back to: those before it on every path. */
export function loopTargets(definition: WorkflowDefinition, decisionId: string): string[] {
  const forward = forwardOf(definition)
  const before = dominatorsOf(forward).get(decisionId) ?? new Set<string>()
  return definition.nodes
    .filter((node) => before.has(node.id) && node.type !== 'start')
    .map((node) => node.id)
}

/** The loop a step belongs to, if any. */
export function loopContaining(definition: WorkflowDefinition, nodeId: string): Loop | undefined {
  return loopsOf(definition).find((loop) => loop.body.has(nodeId))
}
