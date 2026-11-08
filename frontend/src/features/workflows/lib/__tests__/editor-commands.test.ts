import { describe, expect, it } from 'vitest'

import type { WorkflowDefinition } from '@/lib/types/workflow'

import {
  appendAfter,
  apply,
  connect,
  freeExits,
  insertOnEdge,
  newNode,
  nextId,
  redo,
  removeNode,
  startHistory,
  undo,
} from '../editor-commands'
import { layout, readingOrder } from '../layout'

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

  it('survives a cycle in a draft', () => {
    const definition = connect(base(), { from: 'done', to: 'start', when: null })
    expect(layout(definition).size).toBe(2)
  })
})
