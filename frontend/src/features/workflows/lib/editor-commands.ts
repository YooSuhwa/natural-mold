/**
 * Workflow editing as commands (workflow-plan 3.7): every change to the draft is a pure
 * function from one definition to the next, so undo/redo, autosave, the issue list and
 * (later) AI edit proposals all work on the same values. Nothing here talks to the server.
 */
import type {
  EdgeWhen,
  WorkflowDefinition,
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeType,
} from '@/lib/types/workflow'

export type AddableType = Exclude<WorkflowNodeType, 'start'>

/** The outgoing branches a node kind has; a plain node has one unnamed exit. */
export function branchesOf(type: WorkflowNodeType): readonly (EdgeWhen | null)[] {
  if (type === 'decision_bool') return ['true', 'false']
  if (type === 'approval') return ['approved', 'rejected']
  if (type === 'output' || type === 'fail') return []
  return [null]
}

const DEFAULT_SCHEMA = {
  type: 'object',
  properties: { result: { type: 'string' } },
  required: ['result'],
}

/** A new node of ``type`` with the smallest settings that still parse on the server. */
export function newNode(
  type: AddableType,
  id: string,
  options: { agentId?: string; label?: string } = {},
): WorkflowNode {
  const label = options.label ?? null
  switch (type) {
    case 'agent':
      return {
        id,
        label,
        type,
        agent_id: options.agentId ?? '',
        prompt: [],
        output_schema: DEFAULT_SCHEMA,
      }
    case 'decision_bool':
      return {
        id,
        label,
        type,
        condition: { all: [{ source: 'input', path: '', op: 'exists' }] },
      }
    case 'approval':
      return { id, label, type, title: [], body: [], timeout_minutes: 1440 }
    case 'transform':
      return { id, label, type, template: [], fields: {} }
    case 'output':
      return { id, label, type, fields: {} }
    case 'fail':
      return { id, label, type, reason: '' }
  }
}

/** ``agent_1``, ``agent_2``…: the first free id for the kind. */
export function nextId(definition: WorkflowDefinition, type: AddableType): string {
  const taken = new Set(definition.nodes.map((node) => node.id))
  let index = 1
  while (taken.has(`${type}_${index}`)) index += 1
  return `${type}_${index}`
}

function sameEdge(a: WorkflowEdge, b: WorkflowEdge): boolean {
  return a.from === b.from && a.to === b.to && (a.when ?? null) === (b.when ?? null)
}

/** Put ``node`` on ``edge``: from → node (same branch) → to (the node's first exit). */
export function insertOnEdge(
  definition: WorkflowDefinition,
  edge: WorkflowEdge,
  node: WorkflowNode,
): WorkflowDefinition {
  const exits = branchesOf(node.type)
  const kept = definition.edges.filter((item) => !sameEdge(item, edge))
  const edges: WorkflowEdge[] = [...kept, { from: edge.from, to: node.id, when: edge.when ?? null }]
  if (exits.length > 0) edges.push({ from: node.id, to: edge.to, when: exits[0] })
  return { ...definition, nodes: [...definition.nodes, node], edges }
}

/** Hang ``node`` off one free exit of ``fromId``. */
export function appendAfter(
  definition: WorkflowDefinition,
  fromId: string,
  when: EdgeWhen | null,
  node: WorkflowNode,
): WorkflowDefinition {
  const edges = [...definition.edges, { from: fromId, to: node.id, when }]
  return { ...definition, nodes: [...definition.nodes, node], edges }
}

/** The exits of a node that nothing is connected to yet. */
export function freeExits(definition: WorkflowDefinition, nodeId: string): (EdgeWhen | null)[] {
  const node = definition.nodes.find((item) => item.id === nodeId)
  if (!node) return []
  const used = new Set(
    definition.edges.filter((edge) => edge.from === nodeId).map((edge) => edge.when ?? null),
  )
  return branchesOf(node.type).filter((when) => !used.has(when))
}

/**
 * Remove a node. A node with one way in and one way out is bridged (its parent now leads
 * to its child), so deleting a step in a chain keeps the chain; anything else just drops
 * its edges. The start node cannot be removed.
 */
export function removeNode(definition: WorkflowDefinition, nodeId: string): WorkflowDefinition {
  const node = definition.nodes.find((item) => item.id === nodeId)
  if (!node || node.type === 'start') return definition
  const incoming = definition.edges.filter((edge) => edge.to === nodeId)
  const outgoing = definition.edges.filter((edge) => edge.from === nodeId)
  const edges = definition.edges.filter((edge) => edge.from !== nodeId && edge.to !== nodeId)
  if (incoming.length === 1 && outgoing.length === 1) {
    const bridge = { from: incoming[0].from, to: outgoing[0].to, when: incoming[0].when ?? null }
    if (!edges.some((edge) => sameEdge(edge, bridge))) edges.push(bridge)
  }
  return { ...definition, nodes: definition.nodes.filter((item) => item.id !== nodeId), edges }
}

export function connect(definition: WorkflowDefinition, edge: WorkflowEdge): WorkflowDefinition {
  if (edge.from === edge.to || definition.edges.some((item) => sameEdge(item, edge))) {
    return definition
  }
  const replaced = definition.edges.filter(
    (item) => !(item.from === edge.from && (item.when ?? null) === (edge.when ?? null)),
  )
  return { ...definition, edges: [...replaced, { ...edge, when: edge.when ?? null }] }
}

export function removeEdge(definition: WorkflowDefinition, edge: WorkflowEdge): WorkflowDefinition {
  return { ...definition, edges: definition.edges.filter((item) => !sameEdge(item, edge)) }
}

export function updateNode(
  definition: WorkflowDefinition,
  nodeId: string,
  change: (node: WorkflowNode) => WorkflowNode,
): WorkflowDefinition {
  return {
    ...definition,
    nodes: definition.nodes.map((node) => (node.id === nodeId ? change(node) : node)),
  }
}

/** Undo/redo over definitions. ``limit`` bounds memory on long sessions. */
export interface History {
  past: WorkflowDefinition[]
  present: WorkflowDefinition
  future: WorkflowDefinition[]
}

const LIMIT = 100

export function startHistory(present: WorkflowDefinition): History {
  return { past: [], present, future: [] }
}

export function apply(history: History, next: WorkflowDefinition): History {
  if (next === history.present) return history
  return { past: [...history.past, history.present].slice(-LIMIT), present: next, future: [] }
}

export function undo(history: History): History {
  const previous = history.past.at(-1)
  if (previous === undefined) return history
  return {
    past: history.past.slice(0, -1),
    present: previous,
    future: [history.present, ...history.future],
  }
}

export function redo(history: History): History {
  const [next, ...rest] = history.future
  if (next === undefined) return history
  return { past: [...history.past, history.present], present: next, future: rest }
}
