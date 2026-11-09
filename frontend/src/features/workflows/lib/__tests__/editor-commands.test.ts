import { describe, expect, it } from 'vitest'

import type { WorkflowDefinition } from '@/lib/types/workflow'

import {
  appendAfter,
  apply,
  connect,
  freeExits,
  insertOnEdge,
  newNode,
  nextCandidates,
  nextId,
  redo,
  removeNode,
  setNext,
  startHistory,
  undo,
} from '../editor-commands'
import { layout, NODE_WIDTH, readingOrder } from '../layout'

const base = (): WorkflowDefinition => ({
  schema_version: 'moldy.workflow.v1',
  inputs: [],
  nodes: [
    { id: 'start', type: 'start' },
    { id: 'done', type: 'output', fields: {} },
  ],
  edges: [{ from: 'start', to: 'done', when: null }],
})

describe('editor commands', () => {
  it('inserts a node on an edge and keeps the branch', () => {
    const decision = newNode('decision_bool', 'decision_bool_1')
    const next = insertOnEdge(base(), { from: 'start', to: 'done', when: null }, decision)
    expect(next.edges).toEqual([
      { from: 'start', to: 'decision_bool_1', when: null },
      { from: 'decision_bool_1', to: 'done', when: 'true' },
    ])
    expect(freeExits(next, 'decision_bool_1')).toEqual(['false'])
  })

  it('appends after a free exit and gives each kind the first free id', () => {
    let definition = base()
    definition = insertOnEdge(definition, definition.edges[0], newNode('approval', 'approval_1'))
    expect(nextId(definition, 'approval')).toBe('approval_2')
    definition = appendAfter(definition, 'approval_1', 'rejected', newNode('fail', 'fail_1'))
    expect(freeExits(definition, 'approval_1')).toEqual([])
    expect(freeExits(definition, 'fail_1')).toEqual([])
  })

  it('bridges a removed step in a chain and never removes the start', () => {
    const agent = newNode('agent', 'agent_1', { agentId: 'a-1' })
    const chained = insertOnEdge(base(), base().edges[0], agent)
    expect(removeNode(chained, 'agent_1').edges).toEqual([
      { from: 'start', to: 'done', when: null },
    ])
    expect(removeNode(chained, 'start')).toBe(chained)
  })

  it('connect replaces the same exit and ignores self loops', () => {
    const definition = { ...base(), nodes: [...base().nodes, newNode('fail', 'fail_1')] }
    const moved = connect(definition, { from: 'start', to: 'fail_1', when: null })
    expect(moved.edges.filter((edge) => edge.from === 'start')).toEqual([
      { from: 'start', to: 'fail_1', when: null },
    ])
    expect(connect(definition, { from: 'done', to: 'done' })).toBe(definition)
  })

  it('undo and redo walk the history and a new edit drops the redo stack', () => {
    const first = base()
    const second = insertOnEdge(first, first.edges[0], newNode('transform', 'transform_1'))
    let history = apply(startHistory(first), second)
    history = undo(history)
    expect(history.present).toBe(first)
    history = redo(history)
    expect(history.present).toBe(second)
    history = apply(undo(history), base())
    expect(history.future).toEqual([])
    expect(undo(startHistory(first)).present).toBe(first)
  })
})

describe('layout', () => {
  it('stacks a chain top to bottom and keeps branches under their node', () => {
    let definition = base()
    definition = insertOnEdge(definition, definition.edges[0], newNode('decision_bool', 'd'))
    definition = appendAfter(definition, 'd', 'false', newNode('fail', 'f'))
    const placed = layout(definition)
    expect(placed.get('start')?.row).toBe(0)
    expect(placed.get('d')?.row).toBe(1)
    expect(placed.get('done')?.row).toBe(2)
    expect(placed.get('f')?.row).toBe(2)
    expect(placed.get('start')?.x).toBe(placed.get('d')?.x)
    expect(readingOrder(definition).slice(0, 2)).toEqual(['start', 'd'])
  })

  it('puts a "no" branch on the right of a "yes" branch, whatever the definition order', () => {
    const definition: WorkflowDefinition = {
      ...base(),
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'd', type: 'decision_bool', condition: { all: [] } },
        { id: 'no', type: 'fail', reason: 'no' },
        { id: 'yes', type: 'output', fields: {} },
      ],
      edges: [
        { from: 'start', to: 'd', when: null },
        { from: 'd', to: 'no', when: 'false' },
        { from: 'd', to: 'yes', when: 'true' },
      ],
    }
    const placed = layout(definition)
    expect(placed.get('yes')?.x).toBeLessThan(placed.get('no')?.x ?? 0)
    // Still centred under the decision.
    const centre = ((placed.get('yes')?.x ?? 0) + (placed.get('no')?.x ?? 0)) / 2
    expect(centre).toBe(placed.get('d')?.x)
  })

  it('moves a lone "yes" child aside so a "no" line skipping down clears its card', () => {
    const definition: WorkflowDefinition = {
      ...base(),
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'd', type: 'decision_bool', condition: { all: [] } },
        { id: 'review', type: 'approval', title: [], body: [] },
        { id: 'done', type: 'output', fields: {} },
      ],
      edges: [
        { from: 'start', to: 'd', when: null },
        { from: 'd', to: 'review', when: 'true' },
        { from: 'd', to: 'done', when: 'false' },
        { from: 'review', to: 'done', when: 'approved' },
      ],
    }
    const placed = layout(definition)
    const d = placed.get('d')?.x ?? 0
    const review = placed.get('review')?.x ?? 0
    // Positions are card corners. The "no" handle sits at three quarters of the card and
    // its line drops straight down past the "yes" card, with room to spare.
    const noLine = d + (NODE_WIDTH * 3) / 4
    expect(noLine - (review + NODE_WIDTH)).toBeGreaterThanOrEqual(48)
  })

  it('survives a cycle in a draft', () => {
    const definition = connect(base(), { from: 'done', to: 'start', when: null })
    expect(layout(definition).size).toBe(2)
  })
})

describe('choosing the next step', () => {
  // start → decide (true → done, false → review) ; review → done ; spare unconnected
  const graph = (): WorkflowDefinition => ({
    schema_version: 'moldy.workflow.v1',
    inputs: [],
    nodes: [
      { id: 'start', type: 'start' },
      { id: 'decide', type: 'decision_bool', condition: { all: [] } },
      { id: 'review', type: 'approval', title: [], timeout_minutes: 60 },
      { id: 'done', type: 'output', fields: {} },
      { id: 'spare', type: 'fail' },
    ],
    edges: [
      { from: 'start', to: 'decide', when: null },
      { from: 'decide', to: 'done', when: 'true' },
      { from: 'decide', to: 'review', when: 'false' },
      { from: 'review', to: 'done', when: 'approved' },
    ],
  })

  it('offers every node but the start, itself and anything that leads back to it', () => {
    const ids = (nodeId: string) => nextCandidates(graph(), nodeId).map((node) => node.id)
    expect(ids('review')).toEqual(['done', 'spare'])
    expect(ids('decide')).toEqual(['review', 'done', 'spare'])
    // done is reached from start through decide, so start may not be offered anywhere.
    expect(ids('start')).toEqual(['decide', 'review', 'done', 'spare'])
  })

  it('moves one exit to another node and keeps the other exits', () => {
    const moved = setNext(graph(), 'decide', 'false', 'spare')
    expect(moved.edges).toContainEqual({ from: 'decide', to: 'spare', when: 'false' })
    expect(moved.edges).not.toContainEqual({ from: 'decide', to: 'review', when: 'false' })
    expect(moved.edges).toContainEqual({ from: 'decide', to: 'done', when: 'true' })
  })

  it('disconnects an exit, and refuses a target that would make a loop', () => {
    const cut = setNext(graph(), 'decide', 'true', null)
    expect(cut.edges.some((edge) => edge.from === 'decide' && edge.when === 'true')).toBe(false)
    const definition = graph()
    expect(setNext(definition, 'review', 'rejected', 'decide')).toBe(definition)
    expect(setNext(definition, 'review', 'rejected', 'start')).toBe(definition)
  })
})
