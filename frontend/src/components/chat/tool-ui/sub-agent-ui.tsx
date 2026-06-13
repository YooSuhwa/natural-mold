'use client'

import { makeAssistantToolUI, useMessage } from '@assistant-ui/react'
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

interface ToolCallPartLike {
  readonly type: 'tool-call'
  readonly toolName: string
  readonly toolCallId: string
}

function isToolCallPartLike(part: { readonly type: string }): part is ToolCallPartLike {
  return (
    part.type === 'tool-call' &&
    'toolName' in part &&
    typeof part.toolName === 'string' &&
    'toolCallId' in part &&
    typeof part.toolCallId === 'string'
  )
}

function currentTurnTaskToolCallIds(
  content: readonly { readonly type: string }[],
): readonly string[] {
  const ids: string[] = []
  for (const part of content) {
    if (isToolCallPartLike(part) && part.toolName === 'task') ids.push(part.toolCallId)
  }
  return ids
}

interface SubAgentToolCardProps {
  toolCallId: string
  args: SubagentArgs | undefined
  statusType: string | undefined
}

export function SubAgentToolCard({ toolCallId, args, statusType }: SubAgentToolCardProps) {
  const t = useTranslations('chat.toolUi.subAgent')
  const turnToolCallIds = useMessage((message) => currentTurnTaskToolCallIds(message.content))
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
      turnToolCallIds={turnToolCallIds}
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
