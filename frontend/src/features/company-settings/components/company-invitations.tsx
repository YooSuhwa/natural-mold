'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { useSearchParams } from 'next/navigation'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { DirectoryPager } from '@/features/organizations/components/directory-pager'
import { useCursorPager } from '@/features/organizations/lib/cursor-pager'
import { useOrgErrorMessage } from '@/features/organizations/lib/error-message'
import { useTenantInvitationMutations, useTenantInvitations } from '@/lib/hooks/use-tenant-admin'
import type { TenantInvitationCreated } from '@/lib/types/tenant-admin'
import { formatDisplayDateTime } from '@/lib/utils/display-format'
import type { CompanyScope } from './company-admin-gate'
import { CompanyInviteDialog, InvitationTokenResult } from './company-invite-dialog'

/** S06 "회사에 초대": invitation links (no mail is sent, Q19); tokens go to the clipboard only. */
export function CompanyInvitations({ scope }: { scope: CompanyScope }) {
  const t = useTranslations('orgAdmin.companySettings.invitations')
  const errorMessage = useOrgErrorMessage()
  const pager = useCursorPager()
  const page = useTenantInvitations(scope.tenantId, pager.cursor)
  const { revoke, resend } = useTenantInvitationMutations(scope.tenantId)
  const searchParams = useSearchParams()
  // ``?invite=1`` (the solo workspace's "make it a team" entry) opens the dialog at once.
  const [inviting, setInviting] = useState(() => searchParams.get('invite') === '1')
  const [resent, setResent] = useState<TenantInvitationCreated | null>(null)
  const version = scope.overview.updated_at

  return (
    <SettingsSectionCard
      title={t('title')}
      description={t('description')}
      actions={
        <Button size="sm" onClick={() => setInviting(true)}>
          {t('invite')}
        </Button>
      }
    >
      {page.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : !page.data ? (
        <ApiErrorState error={page.error} onRetry={() => void page.refetch()} />
      ) : page.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <ul className="divide-y divide-border">
          {page.data.items.map((invitation) => (
            <li
              key={invitation.id}
              className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center"
            >
              <div className="min-w-0 flex-1">
                <span className="block text-sm">{invitation.email}</span>
                <span className="moldy-ui-caption text-muted-foreground">
                  {t('expires', { date: formatDisplayDateTime(invitation.expires_at) })}
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary">
                  {invitation.role ? t(`role.${invitation.role}`) : t('role.none')}
                </Badge>
                <Badge variant="outline">{t(`status.${invitation.status}`)}</Badge>
                {invitation.status === 'pending' || invitation.status === 'expired' ? (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={resend.isPending}
                    aria-label={t('resendFor', { email: invitation.email })}
                    onClick={() =>
                      resend.mutate(
                        { id: invitation.id, version },
                        { onSuccess: setResent, onError: (err) => toast.error(errorMessage(err)) },
                      )
                    }
                  >
                    {t('resend')}
                  </Button>
                ) : null}
                {invitation.status === 'pending' ? (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={revoke.isPending}
                    aria-label={t('revokeFor', { email: invitation.email })}
                    onClick={() =>
                      revoke.mutate(
                        { id: invitation.id, version },
                        {
                          onSuccess: () => toast.success(t('revoked')),
                          onError: (err) => toast.error(errorMessage(err)),
                        },
                      )
                    }
                  >
                    {t('revoke')}
                  </Button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
      <DirectoryPager
        canGoBack={pager.canGoBack}
        hasMore={page.data?.has_more ?? false}
        onBack={pager.back}
        onNext={() => pager.next(page.data?.next_cursor ?? null)}
      />
      {inviting ? <CompanyInviteDialog scope={scope} onClose={() => setInviting(false)} /> : null}
      {resent ? (
        <InvitationTokenResult invitation={resent} onClose={() => setResent(null)} />
      ) : null}
    </SettingsSectionCard>
  )
}
