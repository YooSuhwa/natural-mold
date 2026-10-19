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
import { useDeleteOrgGroup, useOrgGroups, useOrganizationInfo } from '@/lib/hooks/use-organizations'
import type { GroupView, OrganizationSummary } from '@/lib/types/organization'
import { orgAccess } from '../lib/access'
import { useCursorPager } from '../lib/cursor-pager'
import { useOrgErrorMessage } from '../lib/error-message'
import { DirectoryPager } from './directory-pager'
import { GroupFormDialog, GroupMembersPanel, type GroupDialogState } from './org-group-panels'
import { OrgPageFrame } from './org-page-frame'

export function OrgGroupsContent() {
  const t = useTranslations('orgAdmin.groups')
  return (
    <OrgPageFrame title={t('title')} description={t('description')}>
      {(organization) => <OrgGroupsBody organization={organization} />}
    </OrgPageFrame>
  )
}

function OrgGroupsBody({ organization }: { organization: OrganizationSummary }) {
  const t = useTranslations('orgAdmin.groups')
  const errorMessage = useOrgErrorMessage()
  const pager = useCursorPager()
  const page = useOrgGroups(organization.id, { cursor: pager.cursor })
  const info = useOrganizationInfo(organization.id)
  // Server permissions also cover company administrators who are plain org members.
  const access = orgAccess(organization, info.data?.permissions)
  const remove = useDeleteOrgGroup(organization.id)
  const [dialog, setDialog] = useState<GroupDialogState>(null)
  const [deleting, setDeleting] = useState<GroupView | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const version = page.data?.updated_at
  const canEdit = access.canManageMembers && !!version

  return (
    <div className="space-y-4">
      <SettingsSectionCard
        title={t('list.title')}
        actions={
          canEdit ? (
            <Button size="sm" onClick={() => setDialog({ mode: 'create' })}>
              {t('create.open')}
            </Button>
          ) : null
        }
      >
        {page.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : !page.data ? (
          <ApiErrorState error={page.error} onRetry={() => void page.refetch()} />
        ) : page.data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('list.empty')}</p>
        ) : (
          <ul className="divide-y divide-border">
            {page.data.items.map((group) => {
              const editable = canEdit && group.source === 'manual'
              const expanded = expandedId === group.id
              return (
                <li key={group.id} className="space-y-3 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium">{group.name}</span>
                        <Badge variant="outline">{t(`source.${group.source}`)}</Badge>
                      </div>
                      {group.description ? (
                        <p className="text-xs text-muted-foreground">{group.description}</p>
                      ) : null}
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        aria-expanded={expanded}
                        onClick={() => setExpandedId(expanded ? null : group.id)}
                      >
                        {t('members.toggle')}
                      </Button>
                      {editable ? (
                        <>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => setDialog({ mode: 'edit', group })}
                          >
                            {t('edit.open')}
                          </Button>
                          <Button
                            size="sm"
                            variant="destructive"
                            onClick={() => setDeleting(group)}
                          >
                            {t('delete.open')}
                          </Button>
                        </>
                      ) : null}
                    </div>
                  </div>
                  {expanded ? (
                    <GroupMembersPanel
                      orgId={organization.id}
                      group={group}
                      version={version}
                      canEdit={editable}
                    />
                  ) : null}
                </li>
              )
            })}
          </ul>
        )}
        <DirectoryPager
          canGoBack={pager.canGoBack}
          hasMore={page.data?.has_more ?? false}
          onBack={pager.back}
          onNext={() => pager.next(page.data?.next_cursor ?? null)}
        />
      </SettingsSectionCard>

      {dialog && version ? (
        <GroupFormDialog
          key={dialog.mode === 'edit' ? dialog.group.id : 'create'}
          orgId={organization.id}
          state={dialog}
          version={version}
          onClose={() => setDialog(null)}
        />
      ) : null}

      <DeleteConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null)
        }}
        title={t('delete.title')}
        description={deleting ? t('delete.description', { name: deleting.name }) : undefined}
        isPending={remove.isPending}
        onConfirm={() => {
          if (!deleting || !version) return
          remove.mutate(
            { groupId: deleting.id, version },
            {
              onSuccess: () => setDeleting(null),
              onError: (err) => toast.error(errorMessage(err)),
            },
          )
        }}
      />
    </div>
  )
}
