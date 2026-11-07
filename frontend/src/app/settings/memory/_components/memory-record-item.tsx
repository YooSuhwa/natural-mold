'use client'

import { useState, type FormEvent } from 'react'
import { useTranslations } from 'next-intl'
import {
  ArchiveRestoreIcon,
  BanIcon,
  CheckIcon,
  HistoryIcon,
  Loader2Icon,
  PencilIcon,
  Trash2Icon,
  XIcon,
} from 'lucide-react'
import { toast } from 'sonner'

import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api/client'
import {
  useDeleteMemory,
  useMemoryRevisions,
  useRestoreMemory,
  useRevokeMemory,
  useUpdateMemory,
} from '@/lib/hooks/use-memory'
import type { MemoryRecord } from '@/lib/types'
import { formatDisplayDate, formatDisplayDateTime } from '@/lib/utils/display-format'

/** Flag keys whose value is truthy, minus the one the sensitivity badge already shows. */
function activeFlags(memory: MemoryRecord): string[] {
  return Object.entries(memory.flags ?? {})
    .filter(([key, value]) => Boolean(value) && key !== memory.sensitivity)
    .map(([key]) => key)
}

export function MemoryRecordItem({
  memory,
  agentName,
}: {
  memory: MemoryRecord
  agentName: string | null | undefined
}) {
  const t = useTranslations('appSettings.memory')
  const update = useUpdateMemory()
  const remove = useDeleteMemory()
  const restore = useRestoreMemory()
  const [editing, setEditing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [revoking, setRevoking] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [content, setContent] = useState(memory.content)
  const [reason, setReason] = useState(memory.reason ?? '')
  const isActive = memory.status === 'active'
  const isRevoked = memory.status === 'revoked'

  async function handleSave() {
    try {
      await update.mutateAsync({
        id: memory.id,
        data: { content: content.trim(), reason: reason.trim() || null },
      })
      setEditing(false)
      toast.success(t('toast.updated'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('toast.updateFailed'))
    }
  }

  async function handleDelete() {
    try {
      await remove.mutateAsync(memory.id)
      setConfirmDelete(false)
      toast.success(t('toast.deleted'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('toast.deleteFailed'))
    }
  }

  async function handleRestore() {
    try {
      await restore.mutateAsync(memory.id)
      toast.success(t('toast.restored'))
    } catch (error) {
      if (error instanceof ApiError && error.code.startsWith('MEMORY_PII_')) {
        toast.error(t('toast.restoreBlockedPii'))
        return
      }
      toast.error(error instanceof Error ? error.message : t('toast.restoreFailed'))
    }
  }

  const flags = activeFlags(memory)

  return (
    <article
      className="rounded-lg border border-border/70 bg-background p-3"
      data-testid={`memory-item-${memory.id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">{t(`kind.${memory.kind}`)}</Badge>
            <Badge variant="outline">{t(`scope.${memory.scope}`)}</Badge>
            {agentName ? <Badge variant="outline">{agentName}</Badge> : null}
            <Badge variant="outline">{t(`origin.${memory.origin}`)}</Badge>
            {memory.approval_channel ? (
              <Badge variant="outline">
                {t.has(`channel.${memory.approval_channel}`)
                  ? t(`channel.${memory.approval_channel}`)
                  : memory.approval_channel}
              </Badge>
            ) : null}
            {!isActive ? <Badge variant="destructive">{t(`status.${memory.status}`)}</Badge> : null}
            {memory.sensitivity !== 'normal' ? (
              <Badge variant="destructive">{t(`sensitivity.${memory.sensitivity}`)}</Badge>
            ) : null}
            {flags.map((flag) => (
              <Badge key={flag} variant="outline" data-testid={`memory-flag-${flag}`}>
                {t.has(`flags.${flag}`) ? t(`flags.${flag}`) : t('flags.unknown', { flag })}
              </Badge>
            ))}
          </div>
          {editing ? (
            <div className="space-y-3">
              <Textarea
                value={content}
                onChange={(event) => setContent(event.target.value)}
                className="min-h-20"
                aria-label={t('list.editContent')}
                data-testid="memory-edit-content"
              />
              <Input
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder={t('list.reasonPlaceholder')}
                aria-label={t('list.editReason')}
              />
            </div>
          ) : (
            <div className="space-y-1">
              {memory.title ? (
                <p className="text-sm font-medium text-foreground">{memory.title}</p>
              ) : null}
              <p className="whitespace-pre-wrap text-sm leading-6 text-foreground">
                {memory.content}
              </p>
              {memory.reason ? (
                <p className="text-xs text-muted-foreground">{memory.reason}</p>
              ) : null}
              {isRevoked && memory.revoke_reason ? (
                <p className="text-xs text-muted-foreground">
                  {t('list.revokeReasonShown', { reason: memory.revoke_reason })}
                </p>
              ) : null}
            </div>
          )}
          <p className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
            <span>{t('list.createdAt', { date: formatDisplayDate(memory.created_at) })}</span>
            <span>{t('list.updatedAt', { date: formatDisplayDate(memory.updated_at) })}</span>
            {memory.revoked_at ? (
              <span>{t('list.revokedAt', { date: formatDisplayDate(memory.revoked_at) })}</span>
            ) : null}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {editing ? (
            <>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() => setEditing(false)}
                aria-label={t('list.cancel')}
                data-testid="memory-cancel-edit"
              >
                <XIcon className="size-4" aria-hidden />
              </Button>
              <Button
                type="button"
                size="icon-sm"
                onClick={handleSave}
                disabled={update.isPending || content.trim().length === 0}
                aria-label={t('list.save')}
                data-testid="memory-save-edit"
              >
                {update.isPending ? (
                  <Loader2Icon className="size-4 animate-spin" aria-hidden />
                ) : (
                  <CheckIcon className="size-4" aria-hidden />
                )}
              </Button>
            </>
          ) : (
            <>
              {isActive ? (
                <>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => setEditing(true)}
                    aria-label={t('list.edit')}
                    data-testid="memory-edit-button"
                  >
                    <PencilIcon className="size-4" aria-hidden />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => setRevoking(true)}
                    aria-label={t('list.revoke')}
                    data-testid="memory-revoke-button"
                  >
                    <BanIcon className="size-4" aria-hidden />
                  </Button>
                </>
              ) : null}
              {isRevoked ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => void handleRestore()}
                  disabled={restore.isPending}
                  aria-label={t('list.restore')}
                  data-testid="memory-restore-button"
                >
                  <ArchiveRestoreIcon className="size-4" aria-hidden />
                </Button>
              ) : null}
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() => setHistoryOpen(true)}
                aria-label={t('list.history')}
                data-testid="memory-history-button"
              >
                <HistoryIcon className="size-4" aria-hidden />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() => setConfirmDelete(true)}
                aria-label={t('list.delete')}
                data-testid="memory-delete-button"
              >
                <Trash2Icon className="size-4" aria-hidden />
              </Button>
            </>
          )}
        </div>
      </div>
      <DeleteConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t('deleteDialog.title')}
        description={t('deleteDialog.description')}
        confirmLabel={t('deleteDialog.submit')}
        isPending={remove.isPending}
        onConfirm={() => void handleDelete()}
      />
      <RevokeMemoryDialog memoryId={memory.id} open={revoking} onOpenChange={setRevoking} />
      <MemoryHistoryDialog
        memoryId={historyOpen ? memory.id : null}
        onClose={() => setHistoryOpen(false)}
      />
    </article>
  )
}

function RevokeMemoryDialog({
  memoryId,
  open,
  onOpenChange,
}: {
  memoryId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <RevokeMemoryDialogInner
      key={open ? 'open' : 'closed'}
      memoryId={memoryId}
      open={open}
      onOpenChange={onOpenChange}
    />
  )
}

function RevokeMemoryDialogInner({
  memoryId,
  open,
  onOpenChange,
}: {
  memoryId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const t = useTranslations('appSettings.memory')
  const tCommon = useTranslations('common')
  const revoke = useRevokeMemory()
  const [reason, setReason] = useState('')

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    try {
      await revoke.mutateAsync({ id: memoryId, reason: reason.trim() || null })
      onOpenChange(false)
      toast.success(t('toast.revoked'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('toast.revokeFailed'))
    }
  }

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="sm" height="auto">
      <DialogShell.Header
        icon={<BanIcon className="size-4" />}
        title={t('revokeDialog.title')}
        description={t('revokeDialog.description')}
      />
      <DialogShell.Body>
        <form className="space-y-4" onSubmit={handleSubmit}>
          <FormFieldShell id={`memory-revoke-reason-${memoryId}`} label={t('revokeDialog.reason')}>
            <Input
              id={`memory-revoke-reason-${memoryId}`}
              value={reason}
              maxLength={200}
              placeholder={t('revokeDialog.reasonPlaceholder')}
              onChange={(event) => setReason(event.target.value)}
            />
          </FormFieldShell>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {tCommon('cancel')}
            </Button>
            <Button
              type="submit"
              variant="destructive"
              disabled={revoke.isPending}
              data-testid="memory-revoke-submit"
            >
              {t('revokeDialog.submit')}
            </Button>
          </div>
        </form>
      </DialogShell.Body>
    </DialogShell>
  )
}

function MemoryHistoryDialog({
  memoryId,
  onClose,
}: {
  memoryId: string | null
  onClose: () => void
}) {
  const t = useTranslations('appSettings.memory.history')
  const tCommon = useTranslations('common')
  const revisions = useMemoryRevisions(memoryId)
  const rows = revisions.data ?? []

  return (
    <DialogShell open={memoryId !== null} onOpenChange={(open) => !open && onClose()} size="md">
      <DialogShell.Header
        icon={<HistoryIcon className="size-4" />}
        title={t('title')}
        description={t('description')}
      />
      <DialogShell.Body>
        {revisions.isLoading ? (
          <p className="text-sm text-muted-foreground">{t('loading')}</p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('empty')}</p>
        ) : (
          <ol className="space-y-3" data-testid="memory-revisions">
            {rows.map((revision) => (
              <li key={revision.id} className="rounded-lg border border-border/70 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">
                    {t.has(`action.${revision.action}`)
                      ? t(`action.${revision.action}`)
                      : t('action.unknown')}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {formatDisplayDateTime(revision.created_at)}
                  </span>
                </div>
                {revision.reason ? (
                  <p className="mt-2 text-xs text-muted-foreground">
                    {t('reason', { reason: revision.reason })}
                  </p>
                ) : null}
                {revision.before_content !== null || revision.after_content !== null ? (
                  <div className="mt-2 grid gap-2 md:grid-cols-2">
                    <div className="space-y-1">
                      <p className="text-xs font-medium text-muted-foreground">{t('before')}</p>
                      <p className="whitespace-pre-wrap text-sm text-foreground">
                        {revision.before_content ?? t('none')}
                      </p>
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs font-medium text-muted-foreground">{t('after')}</p>
                      <p className="whitespace-pre-wrap text-sm text-foreground">
                        {revision.after_content ?? t('none')}
                      </p>
                    </div>
                  </div>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button type="button" variant="outline" onClick={onClose}>
          {tCommon('close')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
