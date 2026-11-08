'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { PlusIcon, WorkflowIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { EmptyState } from '@/components/shared/empty-state'
import { PageShell } from '@/components/shared/page-shell'
import { ResourceGrid, ResourceListCard } from '@/components/shared/resource-layout'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { useAgents } from '@/lib/hooks/use-agents'
import { useCreateWorkflow, useWorkflows } from '@/lib/hooks/use-workflows'
import { getResourceTone } from '@/lib/resource-tones'
import { formatDisplayDateTime } from '@/lib/utils/display-format'
import {
  inquiryTemplate,
  WORKFLOW_TEMPLATES,
  type WorkflowTemplate,
} from '@/features/workflows/lib/templates'

const SELECT_CLASS =
  'h-9 w-full rounded-md border bg-background px-2 text-sm focus-visible:ring-2 focus-visible:ring-ring'

export function WorkflowsPageClient() {
  const t = useTranslations('workflows')
  const router = useRouter()
  const workflows = useWorkflows()
  const [creating, setCreating] = useState(false)
  const [query, setQuery] = useState('')
  const needle = query.trim().toLowerCase()
  const shown = (workflows.data ?? []).filter((workflow) =>
    `${workflow.name} ${workflow.description ?? ''}`.toLowerCase().includes(needle),
  )
  const tone = getResourceTone('violet')
  return (
    <PageShell
      title={t('title')}
      description={t('description')}
      isError={workflows.isError}
      onRetry={() => void workflows.refetch()}
      action={
        <Button onClick={() => setCreating(true)}>
          <PlusIcon className="size-4" aria-hidden />
          {t('create')}
        </Button>
      }
    >
      {workflows.isPending ? (
        <Skeleton className="h-32 w-full" />
      ) : workflows.data && workflows.data.length > 0 ? (
        <div className="flex flex-col gap-4">
          <Input
            className="max-w-sm"
            value={query}
            placeholder={t('search')}
            aria-label={t('search')}
            onChange={(event) => setQuery(event.target.value)}
          />
          <ResourceGrid>
            {shown.map((workflow) => (
              <ResourceListCard
                key={workflow.id}
                as="button"
                tone={tone}
                onClick={() => router.push(`/workflows/${workflow.id}`)}
              >
                <ResourceListCard.Header>
                  <WorkflowIcon className="size-4" aria-hidden />
                </ResourceListCard.Header>
                <ResourceListCard.Title>{workflow.name}</ResourceListCard.Title>
                {workflow.description ? (
                  <ResourceListCard.Description>
                    {workflow.description}
                  </ResourceListCard.Description>
                ) : null}
                <ResourceListCard.StatusRow>
                  <span
                    className={
                      workflow.published_version_no
                        ? 'moldy-status-surface moldy-status-success rounded-full px-2 py-0.5 text-xs'
                        : 'moldy-status-surface moldy-status-warn rounded-full px-2 py-0.5 text-xs'
                    }
                  >
                    {workflow.published_version_no
                      ? t('published', { version: workflow.published_version_no })
                      : t('draft')}
                  </span>
                </ResourceListCard.StatusRow>
                <ResourceListCard.MetaRow>
                  <span>{t('updated', { time: formatDisplayDateTime(workflow.updated_at) })}</span>
                </ResourceListCard.MetaRow>
              </ResourceListCard>
            ))}
          </ResourceGrid>
        </div>
      ) : (
        <EmptyState title={t('empty')} description={t('emptyHint')} />
      )}
      {creating ? (
        <CreateDialog
          onClose={() => setCreating(false)}
          onCreated={(id) => router.push(`/workflows/${id}`)}
        />
      ) : null}
    </PageShell>
  )
}

function CreateDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (id: string) => void
}) {
  const t = useTranslations('workflows')
  const tt = useTranslations('workflows.templates.inquiry')
  const [name, setName] = useState('')
  const [template, setTemplate] = useState<WorkflowTemplate>('blank')
  const [classifier, setClassifier] = useState('')
  const [writer, setWriter] = useState('')
  const agents = useAgents()
  const create = useCreateWorkflow()
  const needsAgents = template === 'inquiry' && (!classifier || !writer)
  const ready = name.trim() !== '' && !needsAgents && !create.isPending
  const submit = () => {
    if (!ready) return
    const definition =
      template === 'inquiry' ? inquiryTemplate({ classifier, writer }, tt) : undefined
    create.mutate(
      { name: name.trim(), definition },
      {
        onSuccess: (workflow) => onCreated(workflow.id),
        onError: (error) => toast.error(error instanceof ApiError ? error.message : String(error)),
      },
    )
  }
  const agentSelect = (label: string, value: string, onChange: (id: string) => void) => (
    <label className="flex flex-col gap-2 text-sm">
      {label}
      <select
        className={SELECT_CLASS}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{t('templates.pickAgent')}</option>
        {(agents.data ?? []).map((agent) => (
          <option key={agent.id} value={agent.id}>
            {agent.name}
          </option>
        ))}
      </select>
    </label>
  )
  return (
    <DialogShell open onOpenChange={(open) => !open && onClose()} size="sm">
      <DialogShell.Header title={t('createTitle')} />
      <DialogShell.Body className="flex flex-col gap-4">
        <label className="flex flex-col gap-2 text-sm">
          {t('nameLabel')}
          <Input
            autoFocus
            value={name}
            maxLength={100}
            placeholder={t('namePlaceholder')}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && submit()}
          />
        </label>
        <fieldset className="flex flex-col gap-2 text-sm">
          <legend className="mb-2">{t('templates.title')}</legend>
          {WORKFLOW_TEMPLATES.map((kind) => (
            <label key={kind} className="flex items-start gap-2">
              <input
                type="radio"
                name="workflow-template"
                className="mt-1"
                aria-label={t(`templates.${kind}.name`)}
                checked={template === kind}
                onChange={() => setTemplate(kind)}
              />
              <span className="flex flex-col">
                {t(`templates.${kind}.name`)}
                <span className="text-xs text-muted-foreground">{t(`templates.${kind}.hint`)}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {template === 'inquiry' ? (
          <div className="flex flex-col gap-3">
            {agentSelect(tt('classifierAgent'), classifier, setClassifier)}
            {agentSelect(tt('writerAgent'), writer, setWriter)}
          </div>
        ) : null}
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {t('cancel')}
        </Button>
        <Button disabled={!ready} onClick={submit}>
          {t('createSubmit')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
