'use client'

import { useLayoutEffect, useRef } from 'react'
import { hashKey } from '@tanstack/react-query'
import { z } from 'zod'
import { useAgUiInterrupts, type AgUiInterrupt } from '@assistant-ui/react-ag-ui'
import type { AgUiChatSessionState } from '@/lib/ag-ui/chat-history'
import type { AgUiHistoryChild } from '@/lib/ag-ui/chat-history-children'
import { moldyAgUiInterruptPayloads } from '@/lib/ag-ui/chat-actions-interrupts'
import { HiTLContext, useHiTL } from '@/lib/chat/hitl-context'
import { standardInterruptToToolCalls } from '@/lib/chat/standard-interrupt'
import { useChildTask, useChildTasks, type ChildTask } from '@/lib/hooks/use-child-tasks'
import { currentOrgScope, isOrgScopeCurrent } from '@/lib/org-scope/org-scope-signal'
import type { StandardInterruptPayload } from '@/lib/types'
import { ApprovalForm } from './tool-ui/approval-card'
import { AuthRequiredForm } from './tool-ui/auth-required-card'
import { UserInputForm } from './tool-ui/user-input-ui'

type BoundChild = AgUiHistoryChild & { childTaskId: string }
type TaskList = ReturnType<typeof useChildTasks>
const toolArguments = z.record(z.string(), z.json())
const waiting = { type: 'requires-action', reason: 'interrupt' } as const

function currentSlots(
  conversationId: string,
  state: AgUiChatSessionState,
  pending: readonly AgUiInterrupt[],
) {
  const wire = state.snapshot
  // Only the authorized GET publishes this exact pair. A terminal event can arrive before
  // its durable child binding, and a successor run clears the live interrupt list immediately.
  if (
    !wire ||
    wire.threadId !== conversationId ||
    state.readDenied ||
    state.activeRunId ||
    !wire.physicalInterruptRunId ||
    !wire.interruptRunId ||
    state.interrupts !== wire.interrupts
  ) {
    return []
  }
  const payloads = new Map(
    moldyAgUiInterruptPayloads(pending).map((slot) => [slot.interrupt_id, slot]),
  )
  return moldyAgUiInterruptPayloads(wire.interrupts).flatMap((payload) => {
    const id = payload.interrupt_id
    const [slot, duplicate] = wire.interrupts.filter((item) => item.id === id)
    const [live, ambiguous] = pending.filter((item) => item.id === id)
    if (
      duplicate ||
      ambiguous ||
      !slot?.subagentRunId ||
      !live ||
      live.subagentRunId !== slot.subagentRunId ||
      live.reason !== slot.reason ||
      live.toolCallId !== slot.toolCallId ||
      live.expiresAt !== slot.expiresAt ||
      (slot.expiresAt && !(Date.parse(slot.expiresAt) > Date.now())) ||
      hashKey([payload]) !== hashKey([payloads.get(id)])
    )
      return []
    const [child, other] = wire.subagents.filter(
      (item) => item.subagentRunId === slot.subagentRunId,
    )
    if (
      other ||
      !child?.childTaskId ||
      !child.parentToolCallId ||
      !child.parentMessageId ||
      child.runId !== wire.interruptRunId ||
      child.physicalRunId !== wire.physicalInterruptRunId ||
      child.status !== 'interrupted' ||
      !child.interruptIds?.includes(id) ||
      wire.subagents.some(
        (sibling) =>
          sibling !== child &&
          (sibling.childTaskId === child.childTaskId || sibling.interruptIds?.includes(id)),
      )
    )
      return []
    const key = hashKey([
      conversationId,
      wire.interruptRunId,
      wire.physicalInterruptRunId,
      child.subagentRunId,
      child.childTaskId,
      payload,
      slot.expiresAt,
    ])
    return [
      {
        child: { ...child, childTaskId: child.childTaskId },
        payload,
        key,
        expiresAt: slot.expiresAt,
      },
    ]
  })
}

type Pending = ReturnType<typeof currentSlots>[number]

function matchesTask(task: ChildTask | undefined, child: BoundChild): task is ChildTask {
  // Nested public tool IDs are scoped aliases. Only the server can join these to the
  // durable task, using its exact namespace and original parent-run/tool-call identity.
  return (
    !!task &&
    task.id === child.childTaskId &&
    !!task.child_agent_id &&
    task.status === 'awaiting_approval' &&
    (!!child.parentSubagentRunId || task.tool_call_id === child.parentToolCallId)
  )
}

function sameAdmission(first: ChildTask, second: ChildTask) {
  return (
    first.id === second.id &&
    first.run_id === second.run_id &&
    first.tool_call_id === second.tool_call_id &&
    first.child_agent_id === second.child_agent_id
  )
}

export function AgUiChildPendingForms({
  conversationId,
  snapshot,
}: {
  conversationId: string
  snapshot: AgUiChatSessionState
}) {
  const pending = useAgUiInterrupts()
  const slots = currentSlots(conversationId, snapshot, pending)
  return slots.length ? <ManagedForms conversationId={conversationId} slots={slots} /> : null
}

function ManagedForms({ conversationId, slots }: { conversationId: string; slots: Pending[] }) {
  const list = useChildTasks(conversationId)
  if (list.enabled !== true || list.isError || !list.isFetched) return null
  const forms = slots.map((slot) => {
    const listed = list.tasks.find((task) => task.id === slot.child.childTaskId)
    if (listed ? !matchesTask(listed, slot.child) : !list.hasNextPage) return null
    // A task moving off a loaded page requires a new exact-UUID read.
    return (
      <ManagedForm
        key={slot.key + ':' + Boolean(listed)}
        {...{ conversationId, slot, list, listed }}
      />
    )
  })
  return <div className="max-h-72 overflow-y-auto">{forms}</div>
}

function ManagedForm({
  conversationId,
  slot,
  list,
  listed,
}: {
  conversationId: string
  slot: Pending
  list: TaskList
  listed?: ChildTask
}) {
  const detail = useChildTask(conversationId, slot.child.childTaskId)
  const actions = useHiTL()
  const current = detail.data
  const authorized =
    matchesTask(current, slot.child) &&
    !detail.isError &&
    (!listed || sameAdmission(current, listed))
  const mounted = useRef(false)
  useLayoutEffect(() => {
    mounted.current = authorized
    return () => {
      mounted.current = false
    }
  }, [authorized])
  if (!authorized || !current?.child_agent_id || !actions?.registerDecision) return null
  const agentId = current.child_agent_id
  const register = actions.registerDecision
  const scope = currentOrgScope()
  const { child, payload } = slot
  return (
    <HiTLContext.Provider
      value={{
        agentId,
        onResumeDecisions: async () => {
          throw new Error('Child responses require an interrupt ID')
        },
        registerDecision: async (index, decision, text, interruptId) => {
          const [page, row] = await Promise.all([
            list.refetch({ cancelRefetch: false }),
            detail.refetch({ cancelRefetch: false }),
          ])
          const pages = page.data?.pages
          const listed = pages
            ?.flatMap((item) => item.items)
            .find((item) => item.id === child.childTaskId)
          if (
            !mounted.current ||
            !isOrgScopeCurrent(scope) ||
            interruptId !== payload.interrupt_id ||
            (slot.expiresAt && !(Date.parse(slot.expiresAt) > Date.now())) ||
            !page.isSuccess ||
            !row.isSuccess ||
            !pages?.length ||
            !pages.every((item) => item.enabled) ||
            (listed
              ? !matchesTask(listed, child) || !sameAdmission(listed, current)
              : pages.at(-1)?.next_offset == null) ||
            !matchesTask(row.data, child) ||
            !sameAdmission(row.data, current)
          ) {
            throw new DOMException('The child request is no longer current', 'InvalidStateError')
          }
          await register(index, decision, text, interruptId)
        },
      }}
    >
      <section aria-label={current.role} className="space-y-2 px-4 py-2">
        <h3 className="text-sm font-semibold">{current.role}</h3>
        <PendingActions payload={payload} />
      </section>
    </HiTLContext.Provider>
  )
}

function PendingActions({ payload }: { payload: StandardInterruptPayload }) {
  return standardInterruptToToolCalls(payload).map((call) => {
    const args = toolArguments.safeParse(call.args)
    if (!args.success) return null
    if (call.name === 'ask_user')
      return <UserInputForm key={call.id} args={args.data} status={waiting} />
    if (call.name === 'moldy_auth_required')
      return <AuthRequiredForm key={call.id} args={args.data} />
    if (call.name === 'request_approval')
      return (
        <ApprovalForm
          key={call.id}
          args={args.data}
          status={waiting}
          showConversationGrant={false}
        />
      )
    return null
  })
}
