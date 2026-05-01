'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { useSetAtom } from 'jotai'
import { Loader2Icon, CheckCircle2Icon, XCircleIcon, UsersIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'

interface SubagentArgs {
  agent_name?: string
  subagent_type?: string
  description?: string
  input?: string
  prompt?: string
}

type Phase = 'running' | 'complete' | 'incomplete'

function resolvePhase(statusType: string | undefined): Phase {
  if (statusType === 'complete') return 'complete'
  if (statusType === 'incomplete') return 'incomplete'
  return 'running'
}

function resolveAgentName(args: SubagentArgs | undefined): string {
  if (!args) return 'Sub-agent'
  return args.agent_name || args.subagent_type || 'Sub-agent'
}

function resolveInput(args: SubagentArgs | undefined): string {
  if (!args) return ''
  return args.input || args.prompt || args.description || ''
}

interface SubAgentCardProps {
  toolCallId: string
  args: SubagentArgs | undefined
  statusType: string | undefined
}

function SubAgentCard({ toolCallId, args, statusType }: SubAgentCardProps) {
  const setRail = useSetAtom(chatRightRailAtom)
  const agentName = resolveAgentName(args)
  const input = resolveInput(args)
  const phase = resolvePhase(statusType)

  return (
    <button
      type="button"
      onClick={() =>
        setRail({
          mode: 'subagent',
          subagent: { toolCallId, agentName, input },
        })
      }
      className="group block w-full rounded-xl border border-border/60 bg-card/50 p-3 text-left transition-colors hover:bg-card"
    >
      <div className="flex items-center gap-3">
        <div
          className={cn(
            'flex size-7 shrink-0 items-center justify-center rounded-lg',
            phase === 'running' && 'bg-status-warn/10 text-status-warn',
            phase === 'complete' && 'bg-status-success/10 text-status-success',
            phase === 'incomplete' && 'bg-status-danger/10 text-status-danger',
          )}
        >
          {phase === 'running' ? <Loader2Icon className="size-4 animate-spin" /> : null}
          {phase === 'complete' ? <CheckCircle2Icon className="size-4" /> : null}
          {phase === 'incomplete' ? <XCircleIcon className="size-4" /> : null}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <UsersIcon className="size-3 shrink-0 text-muted-foreground" />
            <p className="truncate text-sm font-medium text-foreground">{agentName}</p>
          </div>
          {input ? (
            <p className="mt-0.5 truncate text-xs text-muted-foreground">{input}</p>
          ) : (
            <p className="mt-0.5 text-xs text-muted-foreground/70">Sub-agent invocation</p>
          )}
        </div>
      </div>
    </button>
  )
}

/**
 * deepagents의 sub-agent 호출은 표준 `task` 도구로 들어온다.
 * (backend/app/agent_runtime/executor.py 참조 — task tool의 subagent_type 인자)
 */
export const SubAgentToolUI = makeAssistantToolUI<SubagentArgs, unknown>({
  toolName: 'task',
  render: ({ args, status, toolCallId }) => (
    <SubAgentCard toolCallId={toolCallId} args={args} statusType={status?.type} />
  ),
})
