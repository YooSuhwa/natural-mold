'use client'

import type { SubagentDiscoverySnapshot } from '@langchain/react'
import { useTranslations } from 'next-intl'
import { useMemo } from 'react'
import { resolveDataUI } from '@/lib/chat/data-ui-registry'
import { TodosBody } from './deepagents-todos'
import type { AgUiSubagentSnapshot } from '@/lib/ag-ui/chat-history-children'

export function isAgUiSubagentSnapshot(
  child: SubagentDiscoverySnapshot | null | undefined,
): child is AgUiSubagentSnapshot {
  return Boolean(
    child &&
    'subagentRunId' in child &&
    typeof child.subagentRunId === 'string' &&
    'parentMessageId' in child &&
    typeof child.parentMessageId === 'string' &&
    'transcript' in child &&
    Array.isArray(child.transcript) &&
    'tools' in child &&
    Array.isArray(child.tools),
  )
}

export function AgUiSubagentDetails({ child }: { readonly child: AgUiSubagentSnapshot }) {
  const t = useTranslations('chat.toolUi.subAgent')
  const cards = useMemo(
    () =>
      child.dataCards?.flatMap((card) => {
        const resolved = resolveDataUI(card.type, card.props)
        return resolved ? [{ id: card.id, ...resolved }] : []
      }) ?? [],
    [child.dataCards],
  )
  const output = typeof child.output === 'string' ? child.output : ''
  const hasDetails =
    child.transcript.length > 0 ||
    child.tools.length > 0 ||
    cards.length > 0 ||
    Boolean(child.todos?.length) ||
    output ||
    child.error
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 moldy-ui-micro text-muted-foreground">
        <span>{t('messageCount', { count: child.transcript.length })}</span>
        <span aria-hidden>·</span>
        <span>{t('toolCount', { count: child.tools.length })}</span>
      </div>
      {child.error && <p className="text-xs text-status-danger">{child.error}</p>}
      {child.transcript.map((message) => (
        <p key={message.id} className="whitespace-pre-wrap break-words text-xs leading-relaxed">
          {message.text}
        </p>
      ))}
      {child.tools.map((tool) => (
        <div key={tool.id} className="space-y-1">
          <span className="font-mono moldy-ui-micro text-muted-foreground">{tool.name}</span>
          {tool.result && <p className="whitespace-pre-wrap break-words text-xs">{tool.result}</p>}
        </div>
      ))}
      {child.todos && child.todos.length > 0 && <TodosBody todos={child.todos} />}
      {cards.map(({ id, Component, props }) => (
        <Component key={id} {...props} />
      ))}
      {output && <p className="whitespace-pre-wrap break-words text-xs">{output}</p>}
      {!hasDetails && <p className="text-xs text-muted-foreground">{t('waiting')}</p>}
    </div>
  )
}
