'use client'

import { makeAssistantToolUI, useMessage } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { useMemo } from 'react'
import { SubagentCard } from '@/components/chat/subagent-card'
import { pillStatusFromAssistantUi } from './collapsible-pill'

const TASK_TOOL_CALL_ID_SEPARATOR = '\n'
const EMPTY_TASK_TOOL_CALL_IDS: readonly string[] = []

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

function currentTurnTaskToolCallIdKey(
  content: readonly { readonly type: string }[],
): string {
  const ids: string[] = []
  for (const part of content) {
    if (isToolCallPartLike(part) && part.toolName === 'task') ids.push(part.toolCallId)
  }
  return ids.join(TASK_TOOL_CALL_ID_SEPARATOR)
}

function taskToolCallIdsFromKey(key: string): readonly string[] {
  if (!key) return EMPTY_TASK_TOOL_CALL_IDS
  return key.split(TASK_TOOL_CALL_ID_SEPARATOR)
}

interface SubAgentToolCardProps {
  toolCallId: string
  args: SubagentArgs | undefined
  statusType: string | undefined
}

export function SubAgentToolCard({ toolCallId, args, statusType }: SubAgentToolCardProps) {
  const t = useTranslations('chat.toolUi.subAgent')
  const turnToolCallIdKey = useMessage((message) => currentTurnTaskToolCallIdKey(message.content))
  const turnToolCallIds = useMemo(
    () => taskToolCallIdsFromKey(turnToolCallIdKey),
    [turnToolCallIdKey],
  )
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
