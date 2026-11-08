'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { PlusIcon, WorkflowIcon } from 'lucide-react'
import { useFormatter, useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { EmptyState } from '@/components/shared/empty-state'
import { PageShell } from '@/components/shared/page-shell'
import { ResourceGrid, ResourceListCard } from '@/components/shared/resource-layout'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { useCreateWorkflow, useWorkflows } from '@/lib/hooks/use-workflows'
import { getResourceTone } from '@/lib/resource-tones'

export function WorkflowsPageClient() {
  const t = useTranslations('workflows')
  const format = useFormatter()
  const router = useRouter()
  const workflows = useWorkflows()
  const [creating, setCreating] = useState(false)
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
        <ResourceGrid>
          {workflows.data.map((workflow) => (
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
                <ResourceListCard.Description>{workflow.description}</ResourceListCard.Description>
              ) : null}
              <ResourceListCard.MetaRow>
                <span>
                  {workflow.published_version_no
                    ? t('published', { version: workflow.published_version_no })
                    : t('draft')}
                </span>
                <span>
                  {t('updated', { time: format.relativeTime(new Date(workflow.updated_at)) })}
                </span>
              </ResourceListCard.MetaRow>
            </ResourceListCard>
          ))}
        </ResourceGrid>
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
  const [name, setName] = useState('')
  const create = useCreateWorkflow()
  const submit = () => {
    const trimmed = name.trim()
    if (!trimmed || create.isPending) return
    create.mutate(trimmed, {
      onSuccess: (workflow) => onCreated(workflow.id),
      onError: (error) => toast.error(error instanceof ApiError ? error.message : String(error)),
    })
  }
  return (
    <DialogShell open onOpenChange={(open) => !open && onClose()} size="sm">
      <DialogShell.Header title={t('createTitle')} />
      <DialogShell.Body>
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
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {t('cancel')}
        </Button>
        <Button disabled={!name.trim() || create.isPending} onClick={submit}>
          {t('createSubmit')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
