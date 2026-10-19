'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import {
  useAddOrgGroupMember,
  useCreateOrgGroup,
  useOrgGroupMembers,
  useRemoveOrgGroupMember,
  useUpdateOrgGroup,
} from '@/lib/hooks/use-organizations'
import type { GroupView } from '@/lib/types/organization'
import { useOrgErrorMessage } from '../lib/error-message'
import { PrincipalPicker } from './principal-picker'

const USER_ONLY = ['user'] as const

export type GroupDialogState = { mode: 'create' } | { mode: 'edit'; group: GroupView } | null

export function GroupFormDialog({
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

export function GroupMembersPanel({
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
        <ApiErrorState error={members.error} onRetry={() => void members.refetch()} />
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
