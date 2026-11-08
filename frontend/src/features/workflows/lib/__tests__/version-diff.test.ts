import { describe, expect, it } from 'vitest'

import type { WorkflowDefinition } from '@/lib/types/workflow'

import { diffDefinitions } from '../version-diff'

const v1: WorkflowDefinition = {
  schema_version: 'moldy.workflow.v1',
  inputs: [{ name: 'inquiry', type: 'string', required: true }],
  nodes: [
    { id: 'start', type: 'start' },
    { id: 'gone', type: 'fail', reason: 'old' },
    { id: 'done', type: 'output', fields: { a: [{ text: 'x' }] } },
  ],
  edges: [
    { from: 'start', to: 'gone', when: null },
    { from: 'gone', to: 'done', when: null },
  ],
}

describe('diffDefinitions', () => {
  it('marks added, removed and changed nodes and connections', () => {
    const v2: WorkflowDefinition = {
      ...v1,
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'new', type: 'fail', reason: 'new' },
        { id: 'done', type: 'output', fields: { a: [{ text: 'y' }] } },
      ],
      edges: [
        { from: 'start', to: 'new', when: null },
        { from: 'new', to: 'done', when: null },
      ],
    }
    const diff = diffDefinitions(v1, v2)
    expect(Object.fromEntries(diff.nodes)).toEqual({
      start: 'same',
      new: 'added',
      done: 'changed',
      gone: 'removed',
    })
    expect(Object.fromEntries(diff.edges)).toEqual({
      'start:next:new': 'added',
      'new:next:done': 'added',
      'start:next:gone': 'removed',
      'gone:next:done': 'removed',
    })
    expect(diff.counts).toEqual({ added: 1, removed: 1, changed: 1 })
    // The canvas draws the removed node and connections too.
    expect(diff.merged.nodes.map((node) => node.id)).toEqual(['start', 'new', 'done', 'gone'])
    expect(diff.merged.edges).toHaveLength(4)
  })

  it('ignores key order and shows changed inputs on the start node', () => {
    const reordered: WorkflowDefinition = {
      ...v1,
      nodes: v1.nodes.map((node) =>
        node.type === 'output' ? { fields: node.fields, type: node.type, id: node.id } : node,
      ),
    }
    expect(diffDefinitions(v1, reordered).counts).toEqual({ added: 0, removed: 0, changed: 0 })
    const moreInputs = {
      ...v1,
      inputs: [...v1.inputs, { name: 'email', type: 'string' as const, required: false }],
    }
    const diff = diffDefinitions(v1, moreInputs)
    expect(diff.inputsChanged).toBe(true)
    expect(diff.nodes.get('start')).toBe('changed')
  })
})
