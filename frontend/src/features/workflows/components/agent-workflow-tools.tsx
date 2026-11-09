'use client'

import { useState } from 'react'
import Link from 'next/link'
import { WorkflowIcon, XIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api/client'
import {
  useAgentWorkflowTools,
  useSetAgentWorkflowTools,
  useWorkflows,
} from '@/lib/hooks/use-workflows'

const SELECT_CLASS =
  'h-8 rounded-md border bg-background px-2 text-sm focus-visible:ring-2 focus-visible:ring-ring'

/**
 * On an agent's settings (workflow-tool-plan 3.5): the published workflows the agent may call
 * as tools in chat. Saved right away (not part of the agent draft); only the person's own
 * published workflows can be added, and one that stopped being usable says why.
 */
export function AgentWorkflowTools({ agentId }: { agentId: string }) {
  const t = useTranslations('workflows.chatTool.settings')
  const linked = useAgentWorkflowTools(agentId)
  const workflows = useWorkflows()
  const save = useSetAgentWorkflowTools(agentId)
  const [picked, setPicked] = useState('')
  const items = linked.data ?? []
  const linkedIds = new Set(items.map((item) => item.workflow_id))
  const candidates = (workflows.data ?? []).filter(
    (workflow) =>
      workflow.published_version_no !== null &&
      workflow.archived_at === null &&
      !linkedIds.has(workflow.id),
  )
  const apply = (ids: string[]) =>
    save.mutate(ids, {
      onSuccess: () => setPicked(''),
      onError: (error) => toast.error(error instanceof ApiError ? error.message : t('saveFailed')),
    })
  if (linked.isPending) return null
  return (
    <div
      className="moldy-muted-panel mx-3 mt-3 flex flex-col gap-2 px-3 py-2 text-sm"
      role="region"
      aria-label={t('title')}
    >
      <div className="flex flex-wrap items-center gap-2">
        <WorkflowIcon className="size-4 shrink-0" aria-hidden />
        <span className="font-medium">{t('title')}</span>
        <span className="text-xs text-muted-foreground">{t('hint')}</span>
      </div>
      {items.length > 0 ? (
        <ul className="flex flex-wrap gap-2" aria-label={t('linked')}>
          {items.map((item) => (
            <li
              key={item.workflow_id}
              className={
                item.usable
                  ? 'moldy-status-surface moldy-status-info flex items-center gap-1 rounded-full px-2 py-0.5 text-xs'
                  : 'moldy-status-surface moldy-status-warn flex items-center gap-1 rounded-full px-2 py-0.5 text-xs'
              }
            >
              <Link href={`/workflows/${item.workflow_id}`} className="underline">
                {item.name || t('unknown')}
              </Link>
              {item.usable ? null : (
                <span>· {t(`broken.${item.broken_reason ?? 'workflow_unavailable'}`)}</span>
              )}
              <button
                type="button"
                className="rounded-full hover:bg-accent"
                aria-label={t('remove', { name: item.name })}
                disabled={save.isPending}
                onClick={() =>
                  apply(
                    items
                      .filter((other) => other.workflow_id !== item.workflow_id)
                      .map((other) => other.workflow_id),
                  )
                }
              >
                <XIcon className="size-3" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <select
          className={SELECT_CLASS}
          value={picked}
          aria-label={t('add')}
          onChange={(event) => setPicked(event.target.value)}
        >
          <option value="">{candidates.length > 0 ? t('pick') : t('none')}</option>
          {candidates.map((workflow) => (
            <option key={workflow.id} value={workflow.id}>
              {workflow.name}
            </option>
          ))}
        </select>
        <Button
          size="sm"
          variant="outline"
          disabled={!picked || save.isPending}
          onClick={() => apply([...items.map((item) => item.workflow_id), picked])}
        >
          {t('addButton')}
        </Button>
      </div>
    </div>
  )
}
