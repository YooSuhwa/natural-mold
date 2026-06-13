'use client'

import { makeAssistantToolUI } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { SubagentCard } from '@/components/chat/subagent-card'
import { pillStatusFromAssistantUi } from './collapsible-pill'

interface SubagentArgs {
  agent_name?: string
  subagent_type?: string
  description?: string
  input?: string
  prompt?: string
}

function resolveAgentName(args: SubagentArgs | undefined, fallback: string): string {
  if (!args) return fallback
  return args.agent_name || args.subagent_type || fallback
}

function resolveInput(args: SubagentArgs | undefined): string {
  if (!args) return ''
  return args.input || args.prompt || args.description || ''
}

interface SubAgentToolCardProps {
  toolCallId: string
  args: SubagentArgs | undefined
  statusType: string | undefined
}

export function SubAgentToolCard({ toolCallId, args, statusType }: SubAgentToolCardProps) {
  const t = useTranslations('chat.toolUi.subAgent')
  const agentName = resolveAgentName(args, t('fallbackName'))
  const input = resolveInput(args)

  return (
    <SubagentCard
      fallback={{
        agentName,
        input,
        status: pillStatusFromAssistantUi(statusType),
      }}
      toolCallId={toolCallId}
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
    <SubAgentToolCard toolCallId={toolCallId} args={args} statusType={status?.type} />
  ),
})
