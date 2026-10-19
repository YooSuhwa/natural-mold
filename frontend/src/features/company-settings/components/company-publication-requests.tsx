'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { useCursorPager } from '@/features/organizations/lib/cursor-pager'
import { DirectoryPager } from '@/features/organizations/components/directory-pager'
import { useOrgErrorMessage } from '@/features/organizations/lib/error-message'
import { usePublicationDecisions, usePublicationRequests } from '@/lib/hooks/use-tenant-admin'
import type { PublicationRequestItem } from '@/lib/types/tenant-admin'

/** S05 "공개 승인": pending company-public items; the item contents are never shown (D8). */
export function CompanyPublicationRequests({ tenantId }: { tenantId: string }) {
  const t = useTranslations('orgAdmin.companySettings.publications')
  const errorMessage = useOrgErrorMessage()
  const pager = useCursorPager()
  const page = usePublicationRequests(tenantId, pager.cursor, true)
  const { approve } = usePublicationDecisions(tenantId)
  const [rejecting, setRejecting] = useState<PublicationRequestItem | null>(null)

  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      {page.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : !page.data ? (
        <ApiErrorState error={page.error} onRetry={() => void page.refetch()} />
      ) : page.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <ul className="divide-y divide-border">
          {page.data.items.map((item) => (
            <li key={item.item_id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start">
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{item.name}</span>
                  <Badge variant="outline">{item.resource_type}</Badge>
                </div>
                {item.description ? (
                  <p className="text-sm text-muted-foreground">{item.description}</p>
                ) : null}
                <p className="moldy-ui-caption text-muted-foreground">
                  {t('origin', {
                    organization: item.organization.name,
                    publisher: item.publisher.name ?? t('unknownPublisher'),
                  })}
                </p>
                <p className="moldy-ui-caption text-muted-foreground">
                  {t('credentialTypes', {
                    types: item.credential_types.join(', ') || t('noCredentials'),
                  })}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button
                  size="sm"
                  disabled={approve.isPending}
                  aria-label={t('approveItem', { name: item.name })}
                  onClick={() =>
                    approve.mutate(item.item_id, {
                      onSuccess: () => toast.success(t('approved')),
                      onError: (err) => toast.error(errorMessage(err)),
                    })
                  }
                >
                  {t('approve')}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  aria-label={t('rejectItem', { name: item.name })}
                  onClick={() => setRejecting(item)}
                >
                  {t('reject')}
                </Button>
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
      {rejecting ? (
        <RejectDialog tenantId={tenantId} item={rejecting} onClose={() => setRejecting(null)} />
      ) : null}
    </SettingsSectionCard>
  )
}

function RejectDialog({
  tenantId,
  item,
  onClose,
}: {
  tenantId: string
  item: PublicationRequestItem
  onClose: () => void
}) {
  const t = useTranslations('orgAdmin.companySettings.publications')
  const tCommon = useTranslations('orgAdmin.common')
  const errorMessage = useOrgErrorMessage()
  const { reject } = usePublicationDecisions(tenantId)
  const [note, setNote] = useState('')
  const trimmed = note.trim()

  return (
    <DialogShell
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="sm"
      height="auto"
    >
      <DialogShell.Header
        title={t('rejectTitle')}
        description={t('rejectDescription', { name: item.name })}
      />
      <DialogShell.Body>
        <FormFieldShell id="reject-note" label={t('note')} required>
          <Textarea
            id="reject-note"
            value={note}
            maxLength={1000}
            onChange={(event) => setNote(event.target.value)}
          />
        </FormFieldShell>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {tCommon('cancel')}
        </Button>
        <Button
          variant="destructive"
          disabled={trimmed.length === 0 || reject.isPending}
          onClick={() =>
            reject.mutate(
              { itemId: item.item_id, note: trimmed },
              {
                onSuccess: () => {
                  toast.success(t('rejected'))
                  onClose()
                },
                onError: (err) => toast.error(errorMessage(err)),
              },
            )
          }
        >
          {t('submitReject')}
        </Button>
      </DialogShell.Footer>
    </DialogShell>
  )
}
