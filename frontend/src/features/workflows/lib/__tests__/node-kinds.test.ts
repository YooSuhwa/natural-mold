import { describe, expect, it } from 'vitest'

import type { WorkflowDefinition, WorkflowNode } from '@/lib/types/workflow'

import { appendAfter, freeExits, insertOnEdge, newNode } from '../editor-commands'
import { layout } from '../layout'
import { nodeTitle } from '../node-title'
import { outputsOf, upstreamValues } from '../upstream-values'

const AGENT = '00000000-0000-0000-0000-00000000a001'
const t = (key: string, values?: Record<string, string | number>) =>
  values ? `${key}(${Object.values(values).join(',')})` : key

const base = (): WorkflowDefinition => ({
  schema_version: 'moldy.workflow.v1',
  inputs: [{ name: 'topic', type: 'string', required: true }],
  nodes: [
    { id: 'start', type: 'start' },
    { id: 'done', type: 'output', fields: {} },
  ],
  edges: [{ from: 'start', to: 'done', when: null }],
})

describe('the remaining node kinds (workflow-nodes-plan)', () => {
  it('gives a switch one exit per case plus default, and an ask answered/timeout', () => {
    let definition = insertOnEdge(base(), base().edges[0], newNode('switch', 'route'))
    const route = definition.nodes.find((node) => node.id === 'route') as Extract<
      WorkflowNode,
      { type: 'switch' }
    >
    definition = {
      ...definition,
      nodes: definition.nodes.map((node) =>
        node.id === 'route'
          ? { ...route, cases: [...route.cases, { name: 'news', value: '뉴스' }] }
          : node,
      ),
    }
    // insertOnEdge wired the first exit (case_1) to done; the rest are free.
    expect(freeExits(definition, 'route')).toEqual(['news', 'default'])
    definition = appendAfter(definition, 'route', 'news', newNode('ask', 'ask'))
    expect(freeExits(definition, 'ask')).toEqual(['answered', 'timeout'])
    // A switch's children spread under it like its handles: case_1 left of news.
    const placed = layout(definition)
    expect((placed.get('done')?.x ?? 0) < (placed.get('ask')?.x ?? 0)).toBe(true)
  })

  it('starts agent kinds on the given agent and for_each on the current item', () => {
    const each = newNode('for_each', 'each', { agentId: AGENT })
    expect(each).toMatchObject({
      agent_id: AGENT,
      prompt: [{ ref: { node: 'item', path: '' } }],
      max_items: 20,
    })
    const parallel = newNode('parallel', 'views', { agentId: AGENT })
    expect(parallel.type === 'parallel' && parallel.branches.map((b) => b.agent_id)).toEqual([
      AGENT,
      AGENT,
    ])
    expect(newNode('tool', 'now')).toMatchObject({ tool: 'current_datetime', args: {} })
  })

  it('offers the right values downstream and the item inside a for_each', () => {
    const lister: WorkflowNode = {
      id: 'lister',
      type: 'agent',
      agent_id: AGENT,
      prompt: [],
      output_schema: {
        type: 'object',
        properties: {
          rows: {
            type: 'array',
            items: { type: 'object', properties: { title: { type: 'string' } } },
          },
        },
      },
    }
    const each: WorkflowNode = {
      ...(newNode('for_each', 'each', { agentId: AGENT }) as Extract<
        WorkflowNode,
        { type: 'for_each' }
      >),
      source: 'lister',
      path: '/rows',
    }
    const definition: WorkflowDefinition = {
      ...base(),
      nodes: [base().nodes[0], lister, each, base().nodes[1]],
      edges: [
        { from: 'start', to: 'lister' },
        { from: 'lister', to: 'each' },
        { from: 'each', to: 'done' },
      ],
    }
    const inside = upstreamValues(definition, 'each')
    expect(inside.filter((value) => value.node === 'item')).toEqual([
      { node: 'item', path: '', type: 'object' },
      { node: 'item', path: '/title', type: 'string' },
    ])
    const after = upstreamValues(definition, 'done').filter((value) => value.node === 'each')
    expect(after.map((value) => value.path)).toEqual(['/items', '/count'])
    expect(upstreamValues(definition, 'done').some((value) => value.node === 'item')).toBe(false)
    expect(
      outputsOf({ id: 'd', type: 'decision_bool', evaluator: 'llm', question: [] }).map(
        (o) => o.path,
      ),
    ).toEqual(['/value', '/confidence', '/reason'])
  })

  it('summarizes the new kinds on their cards', () => {
    const definition: WorkflowDefinition = {
      ...base(),
      nodes: [
        ...base().nodes,
        {
          id: 'route',
          type: 'switch',
          source: 'input',
          path: '/topic',
          cases: [
            { name: 'news', value: '뉴스' },
            { name: 'sports', value: '스포츠' },
          ],
        },
        { id: 'now', type: 'tool', tool: 'current_datetime' },
        {
          id: 'views',
          type: 'parallel',
          branches: [
            { name: 'pro', agent_id: AGENT, prompt: [], output_schema: {} },
            { name: 'con', agent_id: 'missing', prompt: [], output_schema: {} },
          ],
        },
      ],
    }
    const names = new Map([[AGENT, '조사원']])
    expect(nodeTitle(definition, 'route', names, t).detail).toBe('input.topic → news, sports')
    expect(nodeTitle(definition, 'now', names, t).detail).toBe('settings.toolName.current_datetime')
    expect(nodeTitle(definition, 'views', names, t).detail).toBe('pro: 조사원, con: ?')
  })
})
