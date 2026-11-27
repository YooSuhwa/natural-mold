import { describe, expect, it } from 'vitest'
import { projectMcpApp } from '@/lib/chat/mcp-apps/metadata'
import type { AgUiActivitySnapshot, AgUiMessageLink } from '../chat-history-types'
import { findWidgetBinding } from '../activity-widget-binding'

const runId = '45206ae0-c53f-4be6-9ef2-a6968ca04ab5'
const otherRun = '8e2d4b26-d8d4-4237-91f1-215f2fd7e651'
const bindingId = 'bff36309-e8ad-4745-8080-1f76f33099ee'
const toolCallId = 'call:maps'
const rootToolId = 'tool-f328df3ac98e7b9a936d68152d26cd563b671be7929765afb4ffaeb6ed26cc6a'
const wireId = 'answer-from-provider'
const publicId = '4376ea88-f98c-4202-b5a5-6eb424c86d75'
const owner = {
  messageId: 'local-bubble',
  toolCallId: rootToolId,
  publicMessageId: publicId,
  toolParentId: wireId,
}
const binding = { runId, bindingId, toolCallId, publicToolCallId: rootToolId }
const link: AgUiMessageLink = {
  messageId: wireId,
  uiMessageId: publicId,
  runId,
  physicalRunId: runId,
  parentCheckpointId: null,
  checkpointId: null,
  parentMessageId: null,
  subagentRunId: undefined,
  createdAt: undefined,
}

function activity(overrides: Partial<AgUiActivitySnapshot> = {}): AgUiActivitySnapshot {
  return {
    type: 'ACTIVITY_SNAPSHOT',
    activityType: 'moldy.mcp_app',
    messageId: 'widget-pointer',
    content: {
      tool_call_id: toolCallId,
      public_tool_call_id: rootToolId,
      binding_id: bindingId,
      run_id: runId,
    },
    parentMessageId: wireId,
    ...overrides,
  }
}

describe('protected widget binding', () => {
  it('joins the exact originating tool to its public DB and wire message IDs', () => {
    const pointer = activity({ content: { ...activity().content, public_message_id: publicId } })
    expect(findWidgetBinding(owner, [pointer, pointer], [link])).toEqual(binding)
    expect(
      findWidgetBinding({ messageId: publicId, toolCallId: rootToolId }, [pointer], [link]),
    ).toEqual(binding)
    expect(findWidgetBinding({ ...owner, toolCallId }, [pointer], [link])).toBeNull()
  })

  it.each([undefined, 'child-A'])(
    'requires the public tool ID even when the raw ID coincides (%s)',
    (subagentRunId) => {
      const pointer = activity({
        subagentRunId,
        content: { tool_call_id: rootToolId, binding_id: bindingId, run_id: runId },
      })
      expect(findWidgetBinding(owner, [pointer], [{ ...link, subagentRunId }])).toBeNull()
    },
  )

  it('keeps a late pointer off another bubble, including when tool IDs are reused', () => {
    const similar = activity({
      parentMessageId: 'next-answer',
      content: { ...activity().content, run_id: otherRun },
    })
    expect(findWidgetBinding(owner, [similar, activity()], [link])).toEqual(binding)
    expect(findWidgetBinding(owner, [similar], [link])).toBeNull()
    expect(findWidgetBinding(owner, [activity({ parentMessageId: undefined })], [link])).toBeNull()
    expect(findWidgetBinding(owner, [activity({ physicalRunId: otherRun })], [link])).toBeNull()
    expect(
      findWidgetBinding(owner, [activity()], [{ ...link, physicalRunId: otherRun }]),
    ).toBeNull()
  })

  it('requires a matching child scope and unambiguous binding', () => {
    const child = activity({ subagentRunId: 'child-A' })
    expect(findWidgetBinding(owner, [child], [link])).toBeNull()
    expect(findWidgetBinding(owner, [child], [{ ...link, subagentRunId: 'child-B' }])).toBeNull()
    expect(findWidgetBinding(owner, [child], [{ ...link, subagentRunId: 'child-A' }])).toEqual(
      binding,
    )
    expect(
      findWidgetBinding(
        owner,
        [
          activity(),
          activity({
            content: { ...activity().content, binding_id: otherRun },
          }),
        ],
        [link],
      ),
    ).toBeNull()
  })

  it('matches the namespaced child part while keeping its opaque backend tool call ID', () => {
    const publicToolCallId = 'tool-9eb83161fd37ffeda4503e5112d1074790f8d010b51d0c303c2059de5cfed323'
    const childPointer = activity({
      subagentRunId: 'child-A',
      content: { ...activity().content, public_tool_call_id: publicToolCallId },
    })
    const childOwner = { ...owner, messageId: 'child-A', toolCallId: publicToolCallId }
    expect(findWidgetBinding(childOwner, [childPointer], [])).toEqual({
      ...binding,
      publicToolCallId,
    })
    expect(findWidgetBinding({ ...childOwner, toolCallId }, [childPointer], [])).toBeNull()
  })

  it('uses private data only from the approved run and binding in the GET response', () => {
    const canonical = {
      mcp_app: {
        version: 1,
        binding_id: bindingId,
        run_id: runId,
        resource_uri: 'ui://maps/route.html',
        tool_meta: { ui: { resourceUri: 'ui://maps/route.html' } },
      },
      structured_content: { kilometers: 12 },
    }
    expect(projectMcpApp(canonical, toolCallId, 'ready', binding)?.output).toEqual({
      content: 'ready',
      structuredContent: { kilometers: 12 },
    })
    expect(projectMcpApp(canonical, toolCallId, 'ready', { runId: otherRun, bindingId })).toBeNull()
    expect(projectMcpApp(canonical, toolCallId, 'ready', { runId, bindingId: otherRun })).toBeNull()
  })
})
