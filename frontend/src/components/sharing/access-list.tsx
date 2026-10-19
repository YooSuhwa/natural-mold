'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { formatMediumDate } from '@/lib/utils/format-relative-time'

import type { ShareBackend, ShareEntry } from './share-backend'
import { shareErrorMessage } from './share-error'
import { levelLabelKey } from './share-labels'

const SOURCE_ORDER = ['owner', 'direct', 'group', 'organization', 'governance'] as const

function entryName(entry: ShareEntry, t: ReturnType<typeof useTranslations>): string {
  if (entry.principal.displayName) return entry.principal.displayName
  return entry.principal.type === 'organization'
    ? t('principalType.organization')
    : entry.principal.id
}

interface AccessListProps {
  backend: ShareBackend
  currentUserId: string | null
  /** Called after the caller revoked their own grant (they can no longer see the list). */
  onLostAccess: () => void
}

/** Current access (`/access`): inherited rows are labelled, direct grants are revocable. */
export function AccessList({ backend, currentUserId, onLostAccess }: AccessListProps) {
  const t = useTranslations('sharing')
  const [target, setTarget] = useState<ShareEntry | null>(null)
  const entries = [...(backend.snapshot?.entries ?? [])].sort(
    (a, b) => SOURCE_ORDER.indexOf(a.source) - SOURCE_ORDER.indexOf(b.source),
  )

  async function confirmRevoke() {
    if (!target) return
    const name = entryName(target, t)
    const self = target.principal.type === 'user' && target.principal.id === currentUserId
    try {
      await backend.revoke(target)
      toast.success(t('toast.revoked', { name }))
      if (self) onLostAccess()
    } catch (error) {
      toast.error(shareErrorMessage(error, t))
    } finally {
      setTarget(null)
    }
  }

  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">{t('access.empty')}</p>
  }

  return (
    <>
      <ul
        className="divide-y divide-border rounded-md border border-border"
        aria-label={t('access.title')}
      >
        {entries.map((entry) => {
          const name = entryName(entry, t)
          const self = entry.principal.type === 'user' && entry.principal.id === currentUserId
          // A recipient may drop their own grant without can_revoke (E-01 R1).
          const revocable = entry.revocable && (backend.snapshot?.canRevoke || self)
          return (
            <li key={entry.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-foreground">
                  {name}
                  {self ? (
                    <span className="ml-1 text-xs text-muted-foreground">{t('access.you')}</span>
                  ) : null}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {entry.principal.type === 'group' && entry.principal.memberCount !== null
                    ? t('access.groupMembers', { count: entry.principal.memberCount })
                    : t(`principalType.${entry.principal.type}`)}
                  {entry.source === 'governance' ? ` · ${t('source.governance')}` : null}
                  {entry.expiresAt
                    ? ` · ${t('access.expires', { date: formatMediumDate(entry.expiresAt) })}`
                    : null}
                </span>
              </span>
              {entry.level ? (
                <Badge variant="secondary">{t(levelLabelKey(backend.surface, entry.level))}</Badge>
              ) : null}
              {revocable ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={backend.pending}
                  aria-label={t('access.revokeLabel', { name })}
                  onClick={() => setTarget(entry)}
                >
                  {t('access.revoke')}
                </Button>
              ) : null}
            </li>
          )
        })}
      </ul>
      <DeleteConfirmDialog
        open={target !== null}
        onOpenChange={(open) => !open && setTarget(null)}
        title={target ? t('revoke.title', { name: entryName(target, t) }) : ''}
        description={
          target?.principal.type === 'group' && target.principal.memberCount !== null
            ? t('revoke.groupDescription', { count: target.principal.memberCount })
            : t('revoke.description')
        }
        confirmLabel={t('access.revoke')}
        isPending={backend.pending}
        onConfirm={() => void confirmRevoke()}
      />
    </>
  )
}
