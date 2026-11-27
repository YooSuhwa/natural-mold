'use client'

import { useLayoutEffect, useRef, useState } from 'react'
import { z } from 'zod'
import { useAgUiInterrupts } from '@assistant-ui/react-ag-ui'
import {
  currentChildInterrupts,
  verifiedChildInterrupts,
  type ChildInterruptForm,
} from '@/lib/ag-ui/approval-admission'
import type { AgUiChatSessionState } from '@/lib/ag-ui/chat-history'
import type { WireChatSnapshot } from '@/lib/ag-ui/chat-history-types'
import { HiTLContext, useHiTL } from '@/lib/chat/hitl-context'
import { standardInterruptToToolCalls } from '@/lib/chat/standard-interrupt'
import { useChildTask, useChildTasks, type ChildTask } from '@/lib/hooks/use-child-tasks'
import { currentOrgScope, isOrgScopeCurrent } from '@/lib/org-scope/org-scope-signal'
import type { StandardInterruptPayload } from '@/lib/types'
import { ApprovalForm } from './tool-ui/approval-card'
import { AuthRequiredForm } from './tool-ui/auth-required-card'
import { UserInputForm } from './tool-ui/user-input-ui'

type Managed = Extract<ChildInterruptForm, { mode: 'managed' }>
type TaskList = ReturnType<typeof useChildTasks>
type Recheck = (signal: AbortSignal) => Promise<boolean>
type ReadSnapshot = (signal?: AbortSignal) => Promise<WireChatSnapshot>
const toolArguments = z.record(z.string(), z.json())
const waiting = { type: 'requires-action', reason: 'interrupt' } as const

function matchesTask(task: ChildTask | undefined, taskId: string): task is ChildTask {
  // The public spawning call is opaque, including at the root. The server binds
  // childTaskId to that call and its verified namespace in this run.
  return (
    !!task && task.id === taskId && !!task.child_agent_id && task.status === 'awaiting_approval'
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
  readSnapshot,
}: {
  conversationId: string
  snapshot: AgUiChatSessionState
  readSnapshot?: ReadSnapshot
}) {
  const pending = useAgUiInterrupts()
  const slots = currentChildInterrupts(conversationId, snapshot, pending)
  const wire = snapshot.snapshot
  if (!wire || !slots.length) return null
  const managed = slots.filter((slot) => slot.mode === 'managed')
  return (
    <div className="max-h-72 overflow-y-auto">
      {managed.length > 0 && (
        <ManagedForms conversationId={conversationId} slots={managed} wire={wire} />
      )}
      {readSnapshot &&
        slots
          .filter((slot) => slot.mode === 'legacy')
          .map((slot) => (
            <AuthorizedForm
              key={slot.key}
              slot={slot}
              wire={wire}
              recheck={async (signal) => {
                const fresh = await readSnapshot(signal)
                return verifiedChildInterrupts(conversationId, fresh, pending).some(
                  (item) => item.key === slot.key,
                )
              }}
            />
          ))}
    </div>
  )
}

function ManagedForms({
  conversationId,
  slots,
  wire,
}: {
  conversationId: string
  slots: Managed[]
  wire: WireChatSnapshot
}) {
  const list = useChildTasks(conversationId)
  if (list.enabled !== true || list.isError || !list.isFetched) return null
  return slots.map((slot) => {
    const taskId = slot.child.childTaskId
    const listed = list.tasks.find((task) => task.id === taskId)
    if (listed ? !matchesTask(listed, taskId) : !list.hasNextPage) return null
    // Moving off a loaded page requires a fresh exact-UUID read.
    return (
      <ManagedForm
        key={slot.key + ':' + Boolean(listed)}
        {...{ conversationId, slot, wire, list, listed }}
      />
    )
  })
}

function ManagedForm({
  conversationId,
  slot,
  wire,
  list,
  listed,
}: {
  conversationId: string
  slot: Managed
  wire: WireChatSnapshot
  list: TaskList
  listed?: ChildTask
}) {
  const taskId = slot.child.childTaskId
  const detail = useChildTask(conversationId, taskId)
  const current = detail.data
  if (
    detail.isError ||
    !matchesTask(current, taskId) ||
    (listed && !sameAdmission(current, listed))
  )
    return null
  async function recheck() {
    const [page, row] = await Promise.all([
      list.refetch({ cancelRefetch: false }),
      detail.refetch({ cancelRefetch: false }),
    ])
    const pages = page.data?.pages
    if (
      !page.isSuccess ||
      !row.isSuccess ||
      !pages?.length ||
      !pages.every((item) => item.enabled) ||
      !current ||
      !matchesTask(row.data, taskId) ||
      !sameAdmission(row.data, current)
    )
      return false
    const fresh = pages.flatMap((item) => item.items).find((item) => item.id === taskId)
    return fresh
      ? matchesTask(fresh, taskId) && sameAdmission(fresh, current)
      : pages.at(-1)?.next_offset != null
  }
  return (
    <AuthorizedForm
      slot={slot}
      wire={wire}
      agentId={current.child_agent_id ?? undefined}
      label={current.role}
      recheck={recheck}
    />
  )
}

function AuthorizedForm({
  slot,
  wire,
  agentId,
  label,
  recheck,
}: {
  slot: ChildInterruptForm
  wire: WireChatSnapshot
  agentId?: string
  label?: string
  recheck: Recheck
}) {
  const actions = useHiTL()
  const [deniedAt, setDeniedAt] = useState<WireChatSnapshot | null>(null)
  const lifetime = useRef<AbortController | null>(null)
  const latestWire = useRef(wire)
  useLayoutEffect(() => {
    latestWire.current = wire
  }, [wire])
  useLayoutEffect(() => {
    const controller = new AbortController()
    lifetime.current = controller
    return () => controller.abort()
  }, [])
  const register = actions?.registerDecision
  const canRespondTo = actions?.canRespondTo
  if (!register || actions?.canRespond === false || deniedAt === wire) return null
  const scope = currentOrgScope()
  return (
    <HiTLContext.Provider
      value={{
        agentId,
        canRespond: actions?.canRespond,
        approvalGeneration: actions?.approvalGeneration,
        canRespondTo: canRespondTo
          ? (id, index) => id === slot.payload.interrupt_id && canRespondTo(id, index)
          : undefined,
        onResumeDecisions: async () => {
          throw new Error('Child responses require an interrupt ID')
        },
        registerDecision: async (index, decision, text, id) => {
          const signal = lifetime.current?.signal
          if (!signal || signal.aborted || id !== slot.payload.interrupt_id) {
            throw new DOMException('The child request is no longer current', 'InvalidStateError')
          }
          let authorized = false
          try {
            authorized = await recheck(signal)
          } finally {
            if (!signal.aborted && !authorized) setDeniedAt(latestWire.current)
          }
          if (
            !authorized ||
            signal.aborted ||
            !isOrgScopeCurrent(scope) ||
            (slot.expiresAt && !(Date.parse(slot.expiresAt) > Date.now()))
          ) {
            throw new DOMException('The child request is no longer current', 'InvalidStateError')
          }
          await register(index, decision, text, id)
        },
      }}
    >
      <div className="space-y-2 px-4 py-2">
        {label && <h3 className="text-sm font-semibold">{label}</h3>}
        <PendingActions payload={slot.payload} allowConnections={!!agentId} />
      </div>
    </HiTLContext.Provider>
  )
}

function PendingActions({
  payload,
  allowConnections,
}: {
  payload: StandardInterruptPayload
  allowConnections: boolean
}) {
  return standardInterruptToToolCalls(payload).map((call) => {
    const args = toolArguments.safeParse(call.args)
    if (!args.success) return null
    if (call.name === 'ask_user')
      return <UserInputForm key={call.id} args={args.data} status={waiting} />
    if (call.name === 'moldy_auth_required') {
      // A verified checkpoint can accept an auth answer without identifying a
      // registered child agent to which credentials could safely be connected.
      return <AuthRequiredForm key={call.id} args={args.data} allowConnections={allowConnections} />
    }
    if (call.name === 'request_approval') {
      return (
        <ApprovalForm
          key={call.id}
          args={args.data}
          status={waiting}
          showConversationGrant={false}
        />
      )
    }
    return null
  })
}
