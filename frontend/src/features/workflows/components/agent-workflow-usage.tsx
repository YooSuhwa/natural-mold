'use client'

import Link from 'next/link'
import { WorkflowIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { useWorkflowsUsingAgent } from '@/lib/hooks/use-workflows'

/**
 * On an agent's settings (appendix D): the workflows that use it. Publishing pins the
 * agent's settings (Q1), so changing it means republishing them; a drifted one says so.
 */
export function AgentWorkflowUsage({ agentId }: { agentId: string }) {
  const t = useTranslations('agent.workflowUsage')
  const usage = useWorkflowsUsingAgent(agentId)
  const items = usage.data ?? []
  if (items.length === 0) return null
  const drifted = items.some((item) => item.drifted)
  return (
    <div
      className={
        drifted
          ? 'moldy-status-surface moldy-status-warn mx-3 mt-3 flex flex-wrap items-center gap-2 rounded-md px-3 py-2 text-sm'
          : 'moldy-muted-panel mx-3 mt-3 flex flex-wrap items-center gap-2 px-3 py-2 text-sm'
      }
    >
      <WorkflowIcon className="size-4 shrink-0" aria-hidden />
      <span>{t('title', { count: items.length })}</span>
      {items.map((item) => (
        <Link key={item.id} href={`/workflows/${item.id}`} className="underline">
          {item.name}
          {item.drifted ? ` · ${t('republish')}` : ''}
        </Link>
      ))}
      <span className="text-xs text-muted-foreground">{t('hint')}</span>
    </div>
  )
}
