import { describe, expect, it } from 'vitest'

import type { WorkflowDefinition } from '@/lib/types/workflow'

import { exitsOf, freeExits, loopCandidates, nextCandidates, setNext } from '../editor-commands'
import { layout } from '../layout'
import { backEdges, loopsOf, loopTargets } from '../loops'
import { segmentsToText, textToSegments } from '../template-text'
import { upstreamValues } from '../upstream-values'

/** start → draft → review → passed? (true → done); ``stop`` unconnected. */
function draftReview(): WorkflowDefinition {
  return {
    schema_version: 'moldy.workflow.v1',
    inputs: [{ name: 'topic', type: 'string', required: true }],
    nodes: [
      { id: 'start', type: 'start' },
      {
        id: 'draft',
        type: 'agent',
        agent_id: 'a',
        prompt: [{ ref: { node: 'input', path: '/topic' } }],
        output_schema: { type: 'object', properties: { text: { type: 'string' } } },
      },
      {
        id: 'review',
        type: 'agent',
        agent_id: 'a',
        prompt: [{ ref: { node: 'draft', path: '/text' } }],
        output_schema: {
          type: 'object',
          properties: { passed: { type: 'boolean' }, feedback: { type: 'string' } },
        },
      },
      {
        id: 'passed',
        type: 'decision_bool',
        condition: { all: [{ source: 'review', path: '/passed', op: 'eq', value: true }] },
      },
      { id: 'done', type: 'output', fields: { text: [{ ref: { node: 'draft', path: '/text' } }] } },
      { id: 'stop', type: 'fail' },
    ],
    edges: [
      { from: 'start', to: 'draft', when: null },
      { from: 'draft', to: 'review', when: null },
      { from: 'review', to: 'passed', when: null },
      { from: 'passed', to: 'done', when: 'true' },
    ],
  }
}

const looped = () => setNext(draftReview(), 'passed', 'false', 'draft')

describe('loops in the editor', () => {
  it("offers a decision's yes/no exit the steps before it, never the start or other exits", () => {
    const ids = (when: 'true' | 'false' | 'limit' | null) =>
      loopCandidates(draftReview(), 'passed', when).map((node) => node.id)
    expect(ids('false')).toEqual(['draft', 'review'])
    expect(ids('limit')).toEqual([])
    expect(loopCandidates(draftReview(), 'review', null)).toEqual([])
    expect(loopTargets(draftReview(), 'passed')).toEqual(['draft', 'review'])
  })

  it('going back makes a loop with the default round limit and a limit exit', () => {
    const definition = looped()
    expect(backEdges(definition)).toEqual([{ from: 'passed', to: 'draft', when: 'false' }])
    const decision = definition.nodes.find((node) => node.id === 'passed')
    expect(decision).toMatchObject({ repeat: { max_rounds: 3 } })
    expect(exitsOf(definition, 'passed')).toEqual(['true', 'false', 'limit'])
    expect(freeExits(definition, 'passed')).toEqual(['limit'])
    const [loop] = loopsOf(definition)
    expect([...loop.body].sort()).toEqual(['draft', 'passed', 'review'])
  })

  it('pointing the exit forward again drops the limit and the limit exit', () => {
    const withLimit = setNext(looped(), 'passed', 'limit', 'stop')
    expect(withLimit.edges).toContainEqual({ from: 'passed', to: 'stop', when: 'limit' })
    const plain = setNext(withLimit, 'passed', 'false', 'stop')
    expect(plain.nodes.find((node) => node.id === 'passed')).toMatchObject({ repeat: null })
    expect(plain.edges.some((edge) => edge.when === 'limit')).toBe(false)
    expect(exitsOf(plain, 'passed')).toEqual(['true', 'false'])
  })

  it('steps inside a loop may still point forward inside it', () => {
    // The back-edge must not make every step of the loop "lead back".
    const ids = nextCandidates(looped(), 'draft').map((node) => node.id)
    expect(ids).toContain('review')
    expect(ids).not.toContain('draft')
  })

  it('lays the loop out top to bottom, ignoring the back-edge', () => {
    const rows = layout(looped())
    expect(['start', 'draft', 'review', 'passed', 'done'].map((id) => rows.get(id)?.row)).toEqual([
      0, 1, 2, 3, 4,
    ])
  })

  it('offers later steps of the loop as previous-round values, only inside the loop', () => {
    const forDraft = upstreamValues(looped(), 'draft')
    expect(forDraft.filter((v) => v.previous).map((v) => `${v.node}${v.path}`)).toEqual([
      'review/passed',
      'review/feedback',
      'passed/value',
      'passed/confidence',
    ])
    expect(upstreamValues(looped(), 'done').some((v) => v.previous)).toBe(false)
  })

  it('keeps previous-round references through the text box', () => {
    const segments = [
      { text: '의견: ' },
      { ref: { node: 'review', path: '/feedback', previous: true } },
      { ref: { node: 'input', path: '/topic' } },
    ]
    const text = segmentsToText(segments)
    expect(text).toBe('의견: {{prev:review.feedback}}{{input.topic}}')
    expect(textToSegments(text)).toEqual(segments)
  })
})
