'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { useSetAtom } from 'jotai'
import { CollapsiblePill, pillStatusFromAssistantUi } from './collapsible-pill'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'

interface SubagentArgs {
  agent_name?: string
  subagent_type?: string
  description?: string
  input?: string
  prompt?: string
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

  return (
    <CollapsiblePill
      kind="subagent"
      status={pillStatusFromAssistantUi(statusType)}
      title={agentName}
      meta={input || 'Sub-agent invocation'}
      onClick={() =>
        setRail({
          mode: 'subagent',
          subagent: { toolCallId, agentName, input },
        })
      }
    />
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
