'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { DialogShell } from '@/components/shared/dialog-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  useOrgInvitations,
  useResendInvitation,
  useRevokeInvitation,
} from '@/lib/hooks/use-org-governance'
import type { InvitationView } from '@/lib/types/org-governance'
import { useCursorPager } from '../lib/cursor-pager'
import { useOrgErrorMessage } from '../lib/error-message'
import { DirectoryPager } from './directory-pager'
import { InviteResultList, type InviteResult } from './org-invite-results'

const DAY_MS = 86_400_000

/** S10 초대 행: pending and past invitations with resend (new link) and revoke. */
export function OrgInvitationsSection({ orgId }: { orgId: string }) {
  const t = useTranslations('orgAdmin.members.invitations')
  const pager = useCursorPager()
  const page = useOrgInvitations(orgId, { cursor: pager.cursor })
  const revoke = useRevokeInvitation(orgId)
  const errorMessage = useOrgErrorMessage()
  const tCommon = useTranslations('orgAdmin.common')
  const [revoking, setRevoking] = useState<InvitationView | null>(null)
  const [resent, setResent] = useState<InviteResult | null>(null)
  // Accepted and revoked rows are history (the audit log has them); resend replaces a row.
  const open = (page.data?.items ?? []).filter(
    (item) => item.status === 'pending' || item.status === 'expired',
  )

  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      {page.isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : !page.data ? (
        <ApiErrorState error={page.error} onRetry={() => void page.refetch()} />
      ) : open.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <ul className="divide-y divide-border">
          {open.map((invitation) => (
            <InvitationRow
              key={invitation.id}
              orgId={orgId}
              invitation={invitation}
              onRevoke={() => setRevoking(invitation)}
              onResent={setResent}
            />
          ))}
        </ul>
      )}
      <DirectoryPager
        canGoBack={pager.canGoBack}
        hasMore={page.data?.has_more ?? false}
        onBack={pager.back}
        onNext={() => pager.next(page.data?.next_cursor ?? null)}
      />
      <DialogShell
        open={resent !== null}
        onOpenChange={(next) => (next ? undefined : setResent(null))}
        size="sm"
        height="auto"
      >
        <DialogShell.Header title={t('resentTitle')} />
        <DialogShell.Body>
          {resent ? <InviteResultList results={[resent]} /> : null}
        </DialogShell.Body>
        <DialogShell.Footer>
          <Button onClick={() => setResent(null)}>{tCommon('close')}</Button>
        </DialogShell.Footer>
      </DialogShell>
      <DeleteConfirmDialog
        open={revoking !== null}
        onOpenChange={(open) => {
          if (!open) setRevoking(null)
        }}
        title={t('revokeTitle')}
        description={revoking ? t('revokeDescription', { email: revoking.email }) : undefined}
        confirmLabel={t('revoke')}
        isPending={revoke.isPending}
        onConfirm={() => {
          if (!revoking) return
          revoke.mutate(revoking.id, {
            onSuccess: () => {
              setRevoking(null)
              toast.success(t('revoked'))
            },
            onError: (error) => toast.error(errorMessage(error)),
          })
        }}
      />
    </SettingsSectionCard>
  )
}

function InvitationRow({
  orgId,
  invitation,
  onRevoke,
  onResent,
}: {
  orgId: string
  invitation: InvitationView
  onRevoke: () => void
  onResent: (result: InviteResult) => void
}) {
  const t = useTranslations('orgAdmin.members.invitations')
  const tRole = useTranslations('orgAdmin.members.role')
  const errorMessage = useOrgErrorMessage()
  const resend = useResendInvitation(orgId)
  const [now] = useState(() => Date.now()) // render-stable "today" for the countdown
  const days = Math.max(0, Math.ceil((Date.parse(invitation.expires_at) - now) / DAY_MS))

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="min-w-0 space-y-1">
        <p className="truncate text-sm font-medium">{invitation.email}</p>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="outline">{tRole(invitation.role)}</Badge>
          <Badge variant="secondary">{t(`status.${invitation.status}`)}</Badge>
          {invitation.status === 'pending' ? <span>{t('expiresIn', { days })}</span> : null}
        </div>
      </div>
      <div className="flex shrink-0 gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={resend.isPending}
          onClick={() =>
            resend.mutate(invitation.id, {
              onSuccess: (created) =>
                onResent({ email: created.email, token: created.token, error: null }),
              onError: (error) => toast.error(errorMessage(error)),
            })
          }
        >
          {t('resend')}
        </Button>
        {invitation.status === 'pending' ? (
          <Button size="sm" variant="destructive" onClick={onRevoke}>
            {t('revoke')}
          </Button>
        ) : null}
      </div>
    </li>
  )
}
