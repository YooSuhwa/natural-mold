'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import {
  useAddOrgGroupMember,
  useCreateOrgGroup,
  useDeleteOrgGroup,
  useOrgGroupMembers,
  useOrgGroups,
  useRemoveOrgGroupMember,
  useUpdateOrgGroup,
  useOrganizationInfo,
} from '@/lib/hooks/use-organizations'
import type { GroupView, OrganizationSummary } from '@/lib/types/organization'
import { orgAccess } from '../lib/access'
import { useCursorPager } from '../lib/cursor-pager'
import { useOrgErrorMessage } from '../lib/error-message'
import { DirectoryPager } from './directory-pager'
import { OrgPageFrame } from './org-page-frame'
import { PrincipalPicker } from './principal-picker'

const USER_ONLY = ['user'] as const

type GroupDialogState = { mode: 'create' } | { mode: 'edit'; group: GroupView } | null

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
          <p className="text-sm text-status-danger">{errorMessage(page.error)}</p>
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

function GroupFormDialog({
  orgId,
  state,
  version,
  onClose,
}: {
  orgId: string
  state: Exclude<GroupDialogState, null>
  version: string
  onClose: () => void
}) {
  const t = useTranslations('orgAdmin.groups')
  const tCommon = useTranslations('orgAdmin.common')
  const errorMessage = useOrgErrorMessage()
  const create = useCreateOrgGroup(orgId)
  const update = useUpdateOrgGroup(orgId)
  const initial = state.mode === 'edit' ? state.group : null
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [error, setError] = useState<string | null>(null)
  const pending = create.isPending || update.isPending
  const trimmed = name.trim()

  function submit() {
    setError(null)
    const data = { name: trimmed, description: description.trim() || null }
    const handlers = { onSuccess: onClose, onError: (err: unknown) => setError(errorMessage(err)) }
    if (state.mode === 'edit') {
      update.mutate({ groupId: state.group.id, data, version }, handlers)
    } else {
      create.mutate({ data, version }, handlers)
    }
  }

  return (
    <DialogShell
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="sm"
      height="auto"
    >
      <DialogShell.Header title={state.mode === 'edit' ? t('edit.title') : t('create.title')} />
      <DialogShell.Body>
        <div className="space-y-3">
          <FormFieldShell id="group-name" label={t('form.name')} required error={error}>
            <Input
              id="group-name"
              value={name}
              maxLength={200}
              onChange={(event) => setName(event.target.value)}
            />
          </FormFieldShell>
          <FormFieldShell id="group-description" label={t('form.description')}>
            <Textarea
              id="group-description"
              value={description}
              maxLength={5000}
              onChange={(event) => setDescription(event.target.value)}
            />
          </FormFieldShell>
        </div>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {tCommon('cancel')}
        </Button>
        <Button disabled={pending || trimmed.length === 0} onClick={submit}>
          {tCommon('save')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}

function GroupMembersPanel({
  orgId,
  group,
  version,
  canEdit,
}: {
  orgId: string
  group: GroupView
  version: string | undefined
  canEdit: boolean
}) {
  const t = useTranslations('orgAdmin.groups.members')
  const errorMessage = useOrgErrorMessage()
  const members = useOrgGroupMembers(orgId, group.id)
  const add = useAddOrgGroupMember(orgId)
  const remove = useRemoveOrgGroupMember(orgId)
  const onError = (err: unknown) => toast.error(errorMessage(err))
  const busy = add.isPending || remove.isPending

  return (
    <div className="moldy-muted-panel space-y-3 p-3">
      {members.isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : !members.data ? (
        <p className="text-sm text-status-danger">{errorMessage(members.error)}</p>
      ) : members.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <ul className="space-y-1">
          {members.data.items.map((member) => (
            <li key={member.user_id} className="flex items-center justify-between gap-2">
              <span className="truncate text-sm">
                {member.display_name}
                {member.email ? (
                  <span className="ml-2 text-xs text-muted-foreground">{member.email}</span>
                ) : null}
              </span>
              {canEdit && version ? (
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={busy}
                  onClick={() =>
                    remove.mutate(
                      { groupId: group.id, userId: member.user_id, version },
                      { onError },
                    )
                  }
                >
                  {t('remove')}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {canEdit && version ? (
        <PrincipalPicker
          orgId={orgId}
          types={USER_ONLY}
          actionLabel={t('add')}
          disabled={busy}
          onPick={(principal) =>
            add.mutate({ groupId: group.id, userId: principal.id, version }, { onError })
          }
        />
      ) : null}
    </div>
  )
}
