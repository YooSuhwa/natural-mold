'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useGuardrailTemplateMutations, useGuardrailTemplates } from '@/lib/hooks/use-guardrails'
import type { GuardrailTemplate, GuardrailTemplateChoice } from '@/lib/types/guardrails'
import { useGuardrailErrorMessage } from '../lib/labels'
import { GuardrailTemplateCreateDialog } from './guardrail-template-create-dialog'
import { GuardrailTemplateEditor } from './guardrail-template-editor'

interface GuardrailTemplatesCardProps {
  tenantId: string
  /** Templates a new one can be copied from (built-in and published company ones). */
  choices: GuardrailTemplateChoice[]
}

/** Company templates: drafted, published as versions, archived (company admins only). */
export function GuardrailTemplatesCard({ tenantId, choices }: GuardrailTemplatesCardProps) {
  const t = useTranslations('guardrails.templates')
  const errorMessage = useGuardrailErrorMessage()
  const templates = useGuardrailTemplates(tenantId, true)
  const { publish, archive } = useGuardrailTemplateMutations(tenantId)
  const [creating, setCreating] = useState(false)
  // Ids, not snapshots: the dialogs read the current list after a refetch.
  const [editingId, setEditingId] = useState<string | null>(null)
  const [archivingId, setArchivingId] = useState<string | null>(null)
  const items = templates.data ?? []
  const editing = items.find((item) => item.id === editingId) ?? null
  const archiving = items.find((item) => item.id === archivingId) ?? null
  const busy = publish.isPending || archive.isPending

  function runPublish(template: GuardrailTemplate) {
    publish.mutate(
      { id: template.id, version: template.updated_at },
      {
        onSuccess: () => toast.success(t('published')),
        onError: (err) => toast.error(errorMessage(err)),
      },
    )
  }

  function runArchive(template: GuardrailTemplate) {
    archive.mutate(
      { id: template.id, version: template.updated_at },
      {
        onSuccess: () => {
          setArchivingId(null)
          toast.success(t('archived'))
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    )
  }

  return (
    <SettingsSectionCard
      title={t('title')}
      description={t('description')}
      actions={
        <Button size="sm" onClick={() => setCreating(true)}>
          {t('create')}
        </Button>
      }
    >
      {templates.isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : templates.isError ? (
        <ApiErrorState error={templates.error} onRetry={() => void templates.refetch()} />
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <ul className="divide-y divide-border">
          {items.map((template) => (
            <li key={template.id} className="flex flex-wrap items-center gap-2 py-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-foreground">{template.name}</p>
                {template.description ? (
                  <p className="moldy-ui-caption truncate text-muted-foreground">
                    {template.description}
                  </p>
                ) : null}
              </div>
              <Badge variant={template.status === 'published' ? 'secondary' : 'outline'}>
                {t(`status.${template.status}`)}
              </Badge>
              <span className="moldy-ui-caption text-muted-foreground">
                {template.published_version === null
                  ? t('unpublished')
                  : t('version', { version: template.published_version })}
              </span>
              {template.status === 'archived' ? null : (
                <>
                  <Button
                    size="sm"
                    variant="outline"
                    aria-label={t('edit', { name: template.name })}
                    onClick={() => setEditingId(template.id)}
                  >
                    {t('editShort')}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    aria-label={t('publish', { name: template.name })}
                    onClick={() => runPublish(template)}
                  >
                    {t('publishShort')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    aria-label={t('archive', { name: template.name })}
                    onClick={() => setArchivingId(template.id)}
                  >
                    {t('archiveShort')}
                  </Button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {creating ? (
        <GuardrailTemplateCreateDialog
          tenantId={tenantId}
          choices={choices}
          onClose={() => setCreating(false)}
        />
      ) : null}
      {editing ? (
        <GuardrailTemplateEditor
          // By id: a refetch after saving must not remount it before it closes.
          key={editing.id}
          tenantId={tenantId}
          template={editing}
          onClose={() => setEditingId(null)}
        />
      ) : null}
      <DeleteConfirmDialog
        open={archiving !== null}
        onOpenChange={(open) => (open ? undefined : setArchivingId(null))}
        title={t('archiveTitle', { name: archiving?.name ?? '' })}
        description={t('archiveDescription')}
        confirmLabel={t('archiveConfirm')}
        isPending={archive.isPending}
        onConfirm={() => (archiving ? runArchive(archiving) : undefined)}
      />
    </SettingsSectionCard>
  )
}
