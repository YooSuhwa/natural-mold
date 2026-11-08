import { describe, expect, it } from 'vitest'

import type { WorkflowDefinition } from '@/lib/types/workflow'

import { appendAfter, insertOnEdge, newNode } from '../editor-commands'
import { contractLine, rowsToSchema, schemaToRows } from '../schema-rows'
import { referencedValues, segmentsToText, textToSegments } from '../template-text'
import { upstreamValues } from '../upstream-values'

describe('template text', () => {
  it('round-trips text and references', () => {
    const segments = [
      { text: '분류: ' },
      { ref: { node: 'classify', path: '/category' } },
      { text: ' / ' },
      { ref: { node: 'input', path: '/inquiry' } },
    ]
    const text = segmentsToText(segments)
    expect(text).toBe('분류: {{classify.category}} / {{input.inquiry}}')
    expect(textToSegments(text)).toEqual(segments)
    expect(textToSegments('{{ draft.text }}끝')).toEqual([
      { ref: { node: 'draft', path: '/text' } },
      { text: '끝' },
    ])
    expect(textToSegments('{{1bad}} {{}}')).toEqual([{ text: '{{1bad}} {{}}' }])
    expect(textToSegments('{{classify}}')).toEqual([{ ref: { node: 'classify', path: '' } }])
  })

  it('lists each referenced value once', () => {
    expect(referencedValues(textToSegments('{{a.x}} {{a.x}} {{b}}'))).toEqual([
      { node: 'a', path: '/x' },
      { node: 'b', path: '' },
    ])
  })
})

describe('upstream values', () => {
  const build = (): WorkflowDefinition => {
    let definition: WorkflowDefinition = {
      schema_version: 'moldy.workflow.v1',
      inputs: [{ name: 'inquiry', type: 'string', required: true }],
      nodes: [
        { id: 'start', type: 'start' },
        { id: 'done', type: 'output', fields: {} },
      ],
      edges: [{ from: 'start', to: 'done', when: null }],
    }
    definition = insertOnEdge(
      definition,
      definition.edges[0],
      newNode('agent', 'a', { agentId: 'x' }),
    )
    const toDone = definition.edges.find((edge) => edge.to === 'done')
    if (!toDone) throw new Error('edge')
    definition = insertOnEdge(definition, toDone, newNode('approval', 'ok'))
    return appendAfter(definition, 'ok', 'rejected', newNode('fail', 'f'))
  }

  it('offers inputs and outputs of nodes on every path, never a sibling branch', () => {
    const definition = build()
    const forDone = upstreamValues(definition, 'done').map((v) => `${v.node}${v.path}:${v.type}`)
    expect(forDone).toEqual([
      'input/inquiry:string',
      'a/result:string',
      'ok/decision:string',
      'ok/comment:string',
    ])
    expect(upstreamValues(definition, 'a').map((v) => v.node)).toEqual(['input'])
    expect(upstreamValues(definition, 'f').some((v) => v.node === 'done')).toBe(false)
  })
})

describe('schema rows', () => {
  it('round-trips the table shapes and summarises the contract', () => {
    const schema = {
      type: 'object',
      properties: {
        category: { type: 'string', enum: ['a', 'b'], description: '분류' },
        urgent: { type: 'boolean' },
        tags: { type: 'array', items: { type: 'string' } },
      },
      required: ['category'],
    }
    const rows = schemaToRows(schema)
    expect(rows?.map((row) => [row.name, row.type, row.required])).toEqual([
      ['category', 'enum', true],
      ['urgent', 'boolean', false],
      ['tags', 'string_list', false],
    ])
    expect(rowsToSchema(rows ?? [])).toEqual(schema)
    expect(contractLine(schema)).toBe('category, urgent, tags[]')
    expect(schemaToRows({ type: 'object', properties: { x: { type: 'object' } } })).toBeNull()
  })
})
