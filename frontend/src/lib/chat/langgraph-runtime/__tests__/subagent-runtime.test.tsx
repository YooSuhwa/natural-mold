import type { SubagentDiscoverySnapshot } from '@langchain/react'
import { describe, expect, it } from 'vitest'
import {
  getSubagentInlinePolicy,
  summarizeSubagentProgress,
} from '@/lib/chat/langgraph-runtime/subagent-runtime'

function snapshot(
  id: string,
  status: SubagentDiscoverySnapshot['status'],
): SubagentDiscoverySnapshot {
  return {
    id,
    name: `agent-${id}`,
    namespace: [`tools:${id}`],
    parentId: null,
    depth: 0,
    status,
    taskInput: `input-${id}`,
    output: undefined,
    error: undefined,
    startedAt: new Date('2026-06-13T00:00:00Z'),
    completedAt: status === 'running' ? null : new Date('2026-06-13T00:01:00Z'),
  }
}

describe('subagent runtime helpers', () => {
  it('summarizes all discovered subagents when no assistant-turn scope is provided', () => {
    const summary = summarizeSubagentProgress([
      snapshot('tc-1', 'running'),
      snapshot('tc-2', 'complete'),
      snapshot('tc-3', 'error'),
    ])

    expect(summary).toEqual({ total: 3, running: 1, completed: 1, failed: 1 })
  })

  it('scopes progress to the current assistant turn tool-call ids', () => {
    const summary = summarizeSubagentProgress(
      [
        snapshot('tc-current', 'complete'),
        snapshot('tc-old', 'running'),
        snapshot('tc-other', 'error'),
      ],
      ['tc-current'],
    )

    expect(summary).toEqual({ total: 1, running: 0, completed: 1, failed: 0 })
  })

  it('auto-collapses completed cards when the turn has five or more subagents', () => {
    const snapshots = [
      snapshot('tc-1', 'complete'),
      snapshot('tc-2', 'complete'),
      snapshot('tc-3', 'complete'),
      snapshot('tc-4', 'complete'),
      snapshot('tc-5', 'complete'),
    ]

    expect(getSubagentInlinePolicy(snapshots, 'tc-1')).toMatchObject({
      defaultExpanded: false,
      canRenderInlineDetails: true,
      overflowedLiveDetails: false,
    })
  })

  it('caps default live detail subscriptions to the first small set of running subagents', () => {
    const snapshots = [
      snapshot('tc-1', 'running'),
      snapshot('tc-2', 'running'),
      snapshot('tc-3', 'running'),
    ]

    expect(getSubagentInlinePolicy(snapshots, 'tc-1')).toMatchObject({
      defaultExpanded: true,
      canRenderInlineDetails: true,
    })
    expect(getSubagentInlinePolicy(snapshots, 'tc-2')).toMatchObject({
      defaultExpanded: true,
      canRenderInlineDetails: true,
    })
    expect(getSubagentInlinePolicy(snapshots, 'tc-3')).toMatchObject({
      defaultExpanded: false,
      canRenderInlineDetails: false,
      overflowedLiveDetails: true,
    })
  })
})
